"""Day-1 check of the real Muse providers, with the keys in your .env (never printed).

    pip install -e services/orchestrator pillow
    python scripts/smoke_models.py                # every provider that has a key
    python scripts/smoke_models.py --only baseten_muse_spark_open

Per provider:
  1. raw call: latency, prompt / completion tokens, how much of it was reasoning
  2. stage `spec` through the real LLMRouter on three Spanish reports (category expected)
  3. vision: one already anonymized pothole pair photo (data/private/anon/, a `clean` one), asking
     for JSON about the road surface

Only redacted text and anonymized photos are sent. Nothing is written.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))

from civicpulse.catalog import Catalog
from civicpulse.config import REPO_ROOT, _provider_chain
from civicpulse.llm.router import AllProvidersFailed, LLMRouter, image_part
from civicpulse.pipeline.intake import build_spec

REPORTS = [  # (text, expected category)
    ("Hay un bache enorme frente al mercado central, los carros lo esquivan y casi chocan", "pothole"),
    ("Se rompió una tubería y sale agua a la calle desde ayer en la noche", "water_leak"),
    ("Llevan una semana sin recoger la basura en la esquina y ya huele horrible", "garbage"),
]


class RoadLook(BaseModel):
    road_visible: bool
    pothole_visible: bool
    confidence: float = Field(ge=0, le=1)
    description_en: str = Field(max_length=300)


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#") and key.strip() not in os.environ:
            os.environ[key.strip()] = value.split(" #", 1)[0].strip().strip('"')


def sample_photo() -> bytes | None:
    report = ROOT / "data" / "private" / "prefilter.csv"
    if not report.exists():
        return None
    from PIL import Image

    for row in csv.DictReader(report.open(encoding="utf-8")):
        path = ROOT / "data" / "private" / "anon" / f"{Path(row['photo']).stem}.jpg"
        if row["decision"] == "clean" and "before" in row["photo"] and path.exists():
            with Image.open(path) as im:
                im.thumbnail((1024, 1024))
                buf = io.BytesIO()
                im.convert("RGB").save(buf, "JPEG", quality=85)
                return buf.getvalue()
    return None


def raw_call(p) -> None:
    body = {"model": p.model, "messages": [{"role": "user", "content": "Answer with one word: what is 2+2?"}],
            "max_tokens": 64 + p.reasoning_min_tokens}
    if p.reasoning_effort:
        body["reasoning_effort"] = p.reasoning_effort
    t = time.monotonic()
    r = httpx.post(f"{p.base_url.rstrip('/')}/chat/completions", json=body, timeout=120,
                   headers={"Authorization": f"Bearer {p.api_key}"})
    ms = int((time.monotonic() - t) * 1000)
    if r.status_code != 200:
        print(f"  raw: HTTP {r.status_code} {r.text[:300]}")
        return
    data = r.json()
    msg, usage = data["choices"][0]["message"], data.get("usage", {})
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    print(f"  raw: {ms} ms, answer {msg.get('content')!r}, tokens in {usage.get('prompt_tokens')} "
          f"out {usage.get('completion_tokens')}, reasoning {len(reasoning)} chars")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="provider name, e.g. baseten_muse_spark_open")
    args = ap.parse_args()
    load_env()
    catalog = Catalog.load(REPO_ROOT / "packages/catalog/civic_catalog.json")
    photo = sample_photo()
    providers = [p for p in _provider_chain() if p.api_key and (not args.only or p.name == args.only)]
    if not providers:
        print("no provider has a key in .env (BASETEN_API_KEY, META_MODEL_API_KEY, ...)", file=sys.stderr)
        return 2
    for p in providers:
        print(f"\n== {p.name} ({p.model})")
        raw_call(p)
        router = LLMRouter([p])
        for text, expected in REPORTS:
            out = build_spec(text, catalog, router)
            mark = "" if expected is None else ("OK " if out.spec["category_id"] == expected else "MISS ")
            print(f"  spec: {mark}{out.spec['category_id']} via {out.provider} in {out.latency_ms} ms")
        if photo is None:
            print("  vision: skipped (no clean anonymized photo in data/private/anon/)")
            continue
        user = [{"type": "text", "text": "Describe the road surface in this photo as JSON with keys road_visible, "
                 "pothole_visible, confidence (0-1), description_en."}, image_part(photo)]
        try:
            res = router.structured("You inspect street photos for a civic reporting app. Answer with JSON only.",
                                    user, RoadLook, vision=True, max_tokens=300)
            print(f"  vision: {res.latency_ms} ms -> {res.data.model_dump()}")
        except AllProvidersFailed as exc:
            print(f"  vision: FAILED {str(exc)[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
