"""Build the training data for the Muse Glimmer spec model, with Muse Spark Open as the teacher.

    pip install -e services/orchestrator
    python services/glimmer/make_dataset.py --dry-run          # counts and cost estimate, no calls
    python services/glimmer/make_dataset.py                    # about USD 4 of Baseten credits
    python services/glimmer/make_dataset.py --per-category 12  # smaller and cheaper

Steps (everything synthetic, no citizen text):
  1. generate: for each catalog category, Muse Spark Open writes short citizen reports in the
     voice of people in an Andean city (Spanish, English, mixed, typos, vague, urgent, some with
     obviously fake names or phone numbers). Batches of 6; batch 0 of each category is the
     held-out batch and is never trained on (no near copies between train and test).
  2. label: each report goes through redact() and the production spec prompt (spec_system), the
     same call build_spec makes. A label is kept only when the teacher's category matches the
     category the report was written for.
  3. write data/glimmer/train.jsonl and holdout.jsonl as chat rows
     {"messages": [system, user, assistant], "gold_category", "teacher"}; the assistant turn is
     the teacher's spec as one line of JSON.

Uses BASETEN_API_KEY from .env (never printed). Answers are cached in
data/glimmer/cache/*.jsonl, so a run that stops resumes without paying twice.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_models import load_env

from civicpulse.catalog import Catalog
from civicpulse.config import _provider_chain
from civicpulse.llm.prompts import spec_system
from civicpulse.llm.router import AllProvidersFailed, Breaker, LLMRouter, extract_json
from civicpulse.models import SpecLLMOutput
from civicpulse.redact import redact
from civicpulse.spec_grade import compact

OUT = ROOT / "data" / "glimmer"
CACHE = OUT / "cache"
BATCH = 6
HOLDOUT_PER_CATEGORY = 2
# Baseten list price for Muse Spark Open (per token), reasoning billed as output.
PRICE_IN, PRICE_OUT = 1.25e-6, 4.25e-6
EST_GEN = (700, 1500)     # tokens in/out per generation call (incl. low-effort reasoning)
EST_LABEL = (1700, 700)   # tokens in/out per label call (the catalog is in the system prompt)

STYLES = [
    "Spanish as spoken in Ecuador, casual, one or two sentences",
    "Spanish, angry and urgent, with exclamation marks",
    "Spanish with typos and no accents, written fast on a phone",
    "English, neutral and short",
    "English written by a tourist, a bit unsure of the place",
    "Spanish mixed with a few English words",
    "very vague Spanish, the place is only described by a landmark",
    "Spanish, an older person writing politely and at length (3 to 4 sentences)",
]

GEN_SYSTEM = """You write realistic, varied citizen reports for testing a civic reporting app.
Write exactly {n} different reports about this kind of problem: {name} ({means}).
Style of these reports: {style}.
Rules:
- Each report is what a resident types into the app: 1 to 4 sentences, no title.
- Vary the place (a street, a market, a school, a park, a bus stop, a neighborhood), the time and
  how bad it is. Invent generic places; never use real people.
- In {pd} of the {n} reports, include an obviously fake contact like "soy Juan Perez" or the phone
  0990000000, as people sometimes do.
- Do not name the category word in every report; describe what is seen or felt.
Answer with JSON only: {{"reports": ["...", "..."]}}"""

OTHER_MEANS = ("something a city should hear about that fits none of the usual categories, or a "
               "message that is not a city problem at all (a question, a greeting, a complaint about "
               "a private business)")


def gen_call(p, system: str, client: httpx.Client) -> list[str]:
    body = {"model": p.model, "temperature": 0.9, "max_tokens": 1200 + p.reasoning_min_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": "Write them now."}]}
    if p.reasoning_effort:
        body["reasoning_effort"] = p.reasoning_effort
    resp = client.post(f"{p.base_url.rstrip('/')}/chat/completions",
                       headers={"Authorization": f"Bearer {p.api_key}"}, json=body)
    resp.raise_for_status()
    data = extract_json(resp.json()["choices"][0]["message"]["content"] or "")
    return [r.strip() for r in data.get("reports", []) if isinstance(r, str) and 8 <= len(r.strip()) <= 600]


def cached(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {row["key"]: row for row in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if row}


def append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=24, help="reports per category (multiple of 6)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    catalog = Catalog.load(ROOT / "packages/catalog/civic_catalog.json")
    cats = catalog.active_ids()
    batches = max(2, -(-args.per_category // BATCH))
    gen_calls = len(cats) * batches
    label_calls = gen_calls * BATCH
    cost = (gen_calls * (EST_GEN[0] * PRICE_IN + EST_GEN[1] * PRICE_OUT)
            + label_calls * (EST_LABEL[0] * PRICE_IN + EST_LABEL[1] * PRICE_OUT))
    print(f"{len(cats)} categories x {batches} batches of {BATCH} = {label_calls} reports; "
          f"{gen_calls} generation + {label_calls} label calls; estimate USD {cost:.2f} of Baseten")
    if args.dry_run:
        return 0

    load_env()
    chain = [p for p in _provider_chain() if p.name == "baseten_muse_spark_open" and p.api_key]
    if not chain:
        print("BASETEN_API_KEY missing in .env", file=sys.stderr)
        return 1
    teacher = chain[0]
    router = LLMRouter([teacher])
    client = httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))
    CACHE.mkdir(parents=True, exist_ok=True)
    gen_path, label_path = CACHE / "generated.jsonl", CACHE / "labels.jsonl"
    gens, labels = cached(gen_path), cached(label_path)
    rng = random.Random(args.seed)
    system = spec_system(catalog.categories)

    # 1. generate
    for cid in cats:
        cat = catalog.categories[cid]
        means = OTHER_MEANS if cid == "other" else (cat.get("issue_description") or cat["display_name"]["en"])
        for b in range(batches):
            key = f"{cid}:{b}"
            if key in gens:
                continue
            style = STYLES[(b + len(cid)) % len(STYLES)]
            prompt = GEN_SYSTEM.format(n=BATCH, name=cat["display_name"]["en"], means=means, style=style,
                                       pd=rng.choice(["one", "one", "two"]))
            for attempt in range(3):
                try:
                    reports = gen_call(teacher, prompt, client)
                    break
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    print(f"  {key}: {type(exc).__name__}, retry {attempt + 1}", file=sys.stderr)
                    time.sleep(2 * (attempt + 1))
            else:
                continue
            row = {"key": key, "category": cid, "batch": b, "style": style, "reports": reports[:BATCH]}
            append(gen_path, row)
            gens[key] = row
        print(f"generated {cid}: {sum(len(gens[k]['reports']) for k in gens if gens[k]['category'] == cid)}")

    # 2. label (same call as build_spec: redacted text, production system prompt)
    done = 0
    for row in gens.values():
        for i, text in enumerate(row["reports"]):
            key = f"{row['key']}:{i}"
            if key in labels and labels[key]["teacher"]:
                continue  # rows that failed before are tried again
            safe, _ = redact(text)
            spec, err = None, None
            for attempt in range(3):
                # One answer the schema rejects must not stop the rest: no circuit breaker here.
                router.breakers[teacher.name] = Breaker()
                try:
                    spec = router.structured(system, safe, SpecLLMOutput).data.model_dump(mode="json")
                    err = None
                    break
                except AllProvidersFailed as exc:
                    err = str(exc)[:300]
                    if "invalid_output" in err:
                        break  # the teacher answered twice with a bad spec: skip this report
                    time.sleep(3 * (attempt + 1))  # network or rate limit
            lab = {"key": key, "category": row["category"], "batch": row["batch"], "text": safe,
                   "teacher": spec, "error": err}
            append(label_path, lab)
            labels[key] = lab
            done += 1
            if done % 50 == 0:
                print(f"  labeled {done} new reports")

    # 3. split and write
    rows = [lab for lab in labels.values() if lab["teacher"]]
    agree = [lab for lab in rows if (lab["teacher"]["category_id"] if lab["teacher"]["category_id"] in cats
                                     else "other") == lab["category"]]
    train, holdout = [], []
    for cid in cats:
        mine = [lab for lab in agree if lab["category"] == cid]
        held = [lab for lab in mine if lab["batch"] == 0][:HOLDOUT_PER_CATEGORY]
        holdout += held
        train += [lab for lab in mine if lab["batch"] != 0]
    rng.shuffle(train)

    def chat(lab: dict) -> dict:
        return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": lab["text"]},
                             {"role": "assistant", "content": compact(lab["teacher"])}],
                "gold_category": lab["category"], "teacher": lab["teacher"], "key": lab["key"]}

    OUT.mkdir(parents=True, exist_ok=True)
    for name, part in (("train", train), ("holdout", holdout)):
        with (OUT / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for lab in part:
                f.write(json.dumps(chat(lab), ensure_ascii=False) + "\n")
    used = router.usage.get(teacher.name, {})
    print(f"\nlabeled {len(rows)} reports; teacher agreed with the intended category on {len(agree)} "
          f"({len(agree) / max(1, len(rows)):.0%}); train {len(train)}, holdout {len(holdout)}")
    print(f"label calls this run: {used.get('calls', 0)}, tokens in {used.get('prompt_tokens', 0)}, "
          f"out {used.get('completion_tokens', 0)}")
    print(f"written {OUT / 'train.jsonl'} and {OUT / 'holdout.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
