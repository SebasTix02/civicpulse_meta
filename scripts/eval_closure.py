"""Closure eval (plan §5.4 C, main metric): how often does the closure say "resolved" when it is not?

    pip install -e "services/orchestrator[vision]"
    python scripts/eval_closure.py --dry-run           # what it would cost, nothing is sent
    python scripts/eval_closure.py                     # 20 repaired, the 4 not repaired, 20 synthetic
    python scripts/eval_closure.py --pairs 68          # every usable pair
    python scripts/eval_closure.py --figures           # also save the crops the classifier saw

Runs the real closure check (pipeline/closure.py: SAM 3.1 on both photos, homography, Muse on
the pair of crops, asymmetric decision) on the anonymized pothole pairs, labeled by hand in
data/pothole_pairs/closure_labels_v2.csv. Nothing is uploaded; only data/private/anon/ is read.

Sets:
  repaired        labeled repaired: the best answer is RESOLVED_CANDIDATE (recall)
  not_repaired    labeled not repaired (partial repairs): must never be RESOLVED_CANDIDATE
  before_as_after synthetic: the BEFORE photo, slightly moved, darker and recompressed, sent as
                  the AFTER, i.e. "a new photo of the same unrepaired pothole": must never be
                  RESOLVED_CANDIDATE. Always reported as synthetic.

The main number is false "resolved": RESOLVED_CANDIDATE on a negative. Goal 0.
Results go to data/private/closure_eval.csv; --resume continues a stopped run.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
sys.path.insert(0, str(ROOT / "scripts"))

import cv2
import numpy as np
from civicpulse.anonymize import Anonymized, load, to_jpeg
from civicpulse.catalog import Catalog
from civicpulse.config import REPO_ROOT, Settings
from civicpulse.llm.router import LLMRouter
from civicpulse.models import SpecLLMOutput
from civicpulse.pipeline.closure import RESOLVED, verify_pair
from eval_robustness import PRICE_IN, PRICE_OUT, PRICE_SAM, CountingSAM
from smoke_models import load_env

ANON = ROOT / "data" / "private" / "anon"
LABELS = ROOT / "data" / "pothole_pairs" / "closure_labels_v2.csv"
PREFILTER = ROOT / "data" / "private" / "prefilter.csv"
OUT = ROOT / "data" / "private" / "closure_eval.csv"
FIG = ROOT / "data" / "private" / "closure_eval"
MAX_BLURRED = 0.4
EST_SAM_PER_RUN, EST_LLM_PER_RUN, EST_TOKENS_IN, EST_TOKENS_OUT = 8.0, 1.5, 2600, 450
SETS = ("repaired", "not_repaired", "before_as_after")
NEGATIVE = {"not_repaired", "before_as_after"}
FIELDS = ["pair_id", "set", "ai_status", "registration", "regions", "first_region", "ms", "error"]


def labeled() -> dict[str, list[str]]:
    blur = {}
    if PREFILTER.exists():
        blur = {Path(r["photo"]).stem: float(r["blurred_share"] or 0)
                for r in csv.DictReader(PREFILTER.open(encoding="utf-8"))}
    out: dict[str, list[str]] = {"repaired": [], "not_repaired": []}
    for r in csv.DictReader(LABELS.open(encoding="utf-8")):
        sides = [f"{r['pair_id']}_before", f"{r['pair_id']}_after"]
        if r["label"] in out and all((ANON / f"{s}.jpg").exists() and blur.get(s, 0) <= MAX_BLURRED for s in sides):
            out[r["label"]].append(r["pair_id"])
    return out


def anon(jpeg: bytes) -> Anonymized:
    rgb = load(jpeg)
    return Anonymized(jpeg, rgb.shape[1], rgb.shape[0], faces=0, people=0, vehicles=0, blurred_share=0.0)


def new_shot_of(rgb: np.ndarray) -> bytes:
    """The same scene as if photographed again: 2% shift, a bit darker, recompressed."""
    h, w = rgb.shape[:2]
    m = np.float32([[1.01, 0, 0.02 * w], [0, 1.01, 0.01 * h]])
    moved = cv2.warpAffine(rgb, m, (w, h), borderMode=cv2.BORDER_REFLECT)
    return to_jpeg(np.clip(moved.astype(np.float32) * 0.85, 0, 255).astype(np.uint8), 70)


def run_one(pair_id: str, which: str, spec: dict, router, sam, figures: bool) -> dict:
    before = (ANON / f"{pair_id}_before.jpg").read_bytes()
    after = new_shot_of(load(before)) if which == "before_as_after" else (ANON / f"{pair_id}_after.jpg").read_bytes()
    row = {"pair_id": pair_id, "set": which, "error": ""}
    started = time.monotonic()
    try:
        res = verify_pair(anon(before), anon(after), spec, sam, router)
        v = res.verification
        first = v["region_checks"][0] if v["region_checks"] else {}
        row |= {"ai_status": v["ai_status"], "registration": v["registration"]["reason"] or "ok",
                "regions": len(v["region_checks"]),
                "first_region": (f"{first.get('status')} visible={first.get('region_visible_after')} "
                                 f"remaining={first.get('remaining_issue_ratio')} "
                                 f"classifier={(first.get('classifier') or {}).get('after')}") if first else ""}
        if figures:
            FIG.mkdir(parents=True, exist_ok=True)
            for name, jpeg in res.figures.items():
                (FIG / f"{pair_id}_{which}_{name}.jpg").write_bytes(jpeg)
    except Exception as exc:  # noqa: BLE001 - recorded, the rest keeps going
        row |= {"ai_status": "", "registration": "", "regions": 0, "first_region": "",
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    row["ms"] = int((time.monotonic() - started) * 1000)
    return row


def summary(rows: list[dict]) -> None:
    ok = [r for r in rows if not r["error"]]
    print(f"\n{'set':<16} {'runs':>5} {'resolved':>9} {'persists':>9} {'not verif.':>11}  top reasons")
    for which in SETS:
        rs = [r for r in ok if r["set"] == which]
        if not rs:
            continue
        resolved = sum(r["ai_status"] == RESOLVED for r in rs)
        persists = sum(r["ai_status"] == "PERSISTENT_ISSUE" for r in rs)
        reasons = Counter(r["ai_status"].removeprefix("NOT_VERIFIABLE_") for r in rs
                          if r["ai_status"].startswith("NOT_VERIFIABLE")).most_common(3)
        print(f"{which:<16} {len(rs):>5} {resolved:>9} {persists:>9} {len(rs) - resolved - persists:>11}  "
              + ", ".join(f"{k.lower()} {n}" for k, n in reasons))
    negatives = [r for r in ok if r["set"] in NEGATIVE]
    false = [r["pair_id"] + "/" + r["set"] for r in negatives if r["ai_status"] == RESOLVED]
    print(f"\nFALSE RESOLVED: {len(false)}/{len(negatives)} negatives"
          + (f" -> {', '.join(false)}" if false else " (goal 0)"))
    pos = [r for r in ok if r["set"] == "repaired"]
    if pos:
        print(f"repaired sent to human review as candidates: "
              f"{sum(r['ai_status'] == RESOLVED for r in pos)}/{len(pos)}")
    errors = sum(1 for r in rows if r["error"])
    if errors:
        print(f"{errors} run(s) failed (see error column); --resume retries them")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=20, help="repaired pairs (and synthetic ones) to use")
    ap.add_argument("--sets", default=",".join(SETS))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sets = [s for s in args.sets.split(",") if s in SETS]
    lab = labeled()
    chosen = {"repaired": lab["repaired"][: args.pairs], "not_repaired": lab["not_repaired"],
              "before_as_after": lab["repaired"][: args.pairs]}
    done = []
    if args.resume and OUT.exists():
        done = [r for r in csv.DictReader(OUT.open(encoding="utf-8")) if not r["error"]]
    seen = {(r["pair_id"], r["set"]) for r in done}
    jobs = [(p, s) for s in sets for p in chosen[s] if (p, s) not in seen]
    sam_usd = len(jobs) * EST_SAM_PER_RUN * PRICE_SAM
    llm_usd = len(jobs) * EST_LLM_PER_RUN * (EST_TOKENS_IN * PRICE_IN + EST_TOKENS_OUT * PRICE_OUT)
    print(" + ".join(f"{len([j for j in jobs if j[1] == s])} {s}" for s in sets)
          + f" = {len(jobs)} runs; estimate USD {sam_usd:.2f} of Meta credits (SAM) + USD {llm_usd:.2f} of Baseten (Muse)")
    if args.dry_run or not jobs:
        if done:
            summary(done)
        return 0

    load_env()
    if not os.environ.get("META_MODEL_API_KEY"):
        print("META_MODEL_API_KEY missing in .env", file=sys.stderr)
        return 2
    os.environ.setdefault("SAM_BACKEND", "meta_api")
    settings = Settings()
    spec = Catalog.load(REPO_ROOT / "packages/catalog/civic_catalog.json").merge_spec(SpecLLMOutput(
        category_id="pothole", summary_en="Citizen reports a pothole on this street", severity=3, sam_prompts=[],
        photo_verifiable=True, confidence=0.9, contains_personal_data=False), "eval")
    router, sam = LLMRouter(settings.llm_providers), CountingSAM(settings.sam())

    rows = list(done)
    new_file = not (args.resume and OUT.exists())
    with OUT.open("w" if new_file else "a", encoding="utf-8", newline="") as f:
        out = csv.DictWriter(f, FIELDS, extrasaction="ignore")
        if new_file:
            out.writeheader()
        with ThreadPoolExecutor(args.workers) as pool:
            futures = [pool.submit(run_one, p, s, spec, router, sam, args.figures) for p, s in jobs]
            for k, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                rows.append(row)
                out.writerow(row)
                f.flush()
                if k % 10 == 0 or k == len(jobs):
                    usage = list(router.usage.values())
                    usd = sam.requests * PRICE_SAM + sum(u["prompt_tokens"] * PRICE_IN
                                                         + u["completion_tokens"] * PRICE_OUT for u in usage)
                    print(f"  {k}/{len(jobs)} runs, {sam.requests} SAM requests, "
                          f"{sum(u['calls'] for u in usage)} Muse calls, about USD {usd:.2f} so far")
    summary(rows)
    print(f"\nper-pair results in {OUT.relative_to(ROOT)}" + (f", crops in {FIG.relative_to(ROOT)}/" if args.figures else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
