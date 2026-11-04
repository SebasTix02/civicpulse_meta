"""Day-2 check of the vision stage with the real SAM 3.1 (Meta API) and Muse classifier, keys from .env.

    pip install -e "services/orchestrator[vision]"
    python scripts/smoke_vision.py                  # 3 anonymized pothole "before" photos as pothole reports
    python scripts/smoke_vision.py --photos 5 --category damaged_pavers
    python scripts/smoke_vision.py --photo bach_0211_before --trace \
        --text "Hay un bache grande en el carril derecho, los carros se desvian"   # whole process, every call

Runs VisionStage exactly as Modal does, against an in-memory stand-in for Supabase, on photos
already anonymized in data/private/anon/ (they are anonymized again, as a live photo would be).
About 4-6 SAM requests (USD 0.0025 each) and 1-4 classifier calls per photo. Prints the intake
status, candidates and classifier answers, and saves the public copy and the outlined crops to
data/private/vision_smoke/ so you can look at what the classifier saw.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
sys.path.insert(0, str(ROOT / "scripts"))

from civicpulse.anonymize import Anonymizer, fetch_models
from civicpulse.catalog import Catalog
from civicpulse.config import REPO_ROOT, Settings
from civicpulse.llm.router import LLMRouter
from civicpulse.models import SpecLLMOutput
from civicpulse.pipeline.intake import build_spec
from civicpulse.pipeline.vision import VisionStage
from smoke_models import load_env

ANON = ROOT / "data" / "private" / "anon"
OUT = ROOT / "data" / "private" / "vision_smoke"


class MemoryDb:
    """Just what VisionStage uses; uploads go to data/private/vision_smoke/."""

    def __init__(self, report: dict, photo: bytes):
        self.photo, self.inserted = photo, []
        self.rows = {"reports": report, "report_photos": {"photo_id": "p1"},
                     "photos": {"id": "p1", "storage_path": "raw/smoke.jpg"}, "domains": {"count_prompts": []}}

    def select_one(self, table, **eq):
        return self.rows[table]

    def stage_done(self, *a, **k):
        return False

    def insert(self, table, row):
        self.inserted.append(row)

    def update(self, *a, **k):
        pass

    def download(self, path):
        return self.photo

    def rpc(self, fn, args):  # check_photo_reuse: the smoke photos are never "reused"
        return {"reused": False, "matches": []}

    def select(self, table, params):  # no moderation flags here
        return []

    def upload(self, path, data, content_type="image/jpeg"):
        name = f"{self.rows['reports']['id']}_{Path(path).name}"
        (OUT / name).write_bytes(data)

    def compare_and_set(self, table, id_, expected, patch):
        self.rows[table] |= patch
        return True


def _short(value, n: int = 600) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= n else text[:n] + " ..."


def trace_calls(router: LLMRouter, sam) -> None:
    """--trace: print every live model call (who, what was asked, what came back, how long)."""
    clock = lambda: time.strftime("%H:%M:%S")  # noqa: E731
    structured, segment = router.structured, sam.segment

    def traced_structured(system, user, schema, **kw):
        texts = [user] if isinstance(user, str) else [u.get("text", "") for u in user if u.get("type") == "text"]
        images = 0 if isinstance(user, str) else sum(u.get("type") == "image_url" for u in user)
        print(f"[{clock()}] MUSE call -> {schema.__name__}: system prompt {len(system)} chars, "
              f"{images} image(s); user: {_short(' | '.join(t for t in texts if t), 300)}")
        res = structured(system, user, schema, **kw)
        print(f"[{clock()}]   <- {res.provider} / {res.model}, {res.latency_ms} ms: "
              f"{_short(res.data.model_dump(mode='json'))}")
        return res

    def traced_segment(image_jpeg, prompts, threshold=0.4):
        print(f"[{clock()}] SAM 3.1 call -> prompts {prompts} (threshold {threshold})")
        res = segment(image_jpeg, prompts, threshold)
        for prompt, dets in res.detections.items():
            best = max((d.score for d in dets), default=0)
            print(f"[{clock()}]   <- '{prompt}': {len(dets)} mask(s), best score {best:.2f}")
        print(f"[{clock()}]   <- {res.backend}, {res.latency_ms} ms")
        return res

    router.structured, sam.segment = traced_structured, traced_segment


def photos(n: int) -> list[Path]:
    blurred = {}
    report = ROOT / "data" / "private" / "prefilter.csv"
    if report.exists():
        blurred = {Path(r["photo"]).stem: float(r["blurred_share"] or 0)
                   for r in csv.DictReader(report.open(encoding="utf-8"))}
    found = [p for p in sorted(ANON.glob("*_before.jpg")) if blurred.get(p.stem, 0) < 0.2]
    return found[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", type=int, default=3)
    ap.add_argument("--category", default="pothole")
    ap.add_argument("--photo", action="append", default=[],
                    help="exact photo name in data/private/anon/, e.g. bach_0211_before (repeatable)")
    ap.add_argument("--text", help="a citizen report: builds the spec with the real Muse call first")
    ap.add_argument("--trace", action="store_true", help="print every live SAM 3.1 and Muse call")
    args = ap.parse_args()
    load_env()
    if not os.environ.get("META_MODEL_API_KEY"):
        print("META_MODEL_API_KEY missing in .env", file=sys.stderr)
        return 2
    os.environ.setdefault("SAM_BACKEND", "meta_api")
    settings = Settings()
    catalog = Catalog.load(REPO_ROOT / "packages/catalog/civic_catalog.json")
    router, sam = LLMRouter(settings.llm_providers), settings.sam()
    if args.trace:
        trace_calls(router, sam)
    if args.text:
        print(f"== report text: {args.text}")
        out = build_spec(args.text, catalog, router)
        spec = out.spec
        print(f"== spec ({out.provider}): category {spec.get('category_id')}, severity {spec.get('severity')}, "
              f"SAM prompts {spec.get('target_prompts')}")
        print(f"   summary: {spec.get('summary_en')}")
    else:
        spec = catalog.merge_spec(SpecLLMOutput(
            category_id=args.category, summary_en=f"Citizen reports a {args.category.replace('_', ' ')} on this street",
            severity=3, sam_prompts=[], photo_verifiable=True, confidence=0.9, contains_personal_data=False), "smoke")
    models = ROOT / "data" / "private" / "models"
    fetch_models(models)
    OUT.mkdir(parents=True, exist_ok=True)
    stage = VisionStage(None, router, sam, Anonymizer(models))
    chosen = [ANON / f"{name.removesuffix('.jpg')}.jpg" for name in args.photo] or photos(args.photos)
    missing = [p.name for p in chosen if not p.exists()]
    if missing:
        print(f"not in data/private/anon/: {missing}", file=sys.stderr)
        return 2
    if not chosen:
        print("no anonymized photos in data/private/anon/", file=sys.stderr)
        return 2
    for path in chosen:
        report = {"id": path.stem, "status": "spec_ready", "current_photo_version": 1, "spec": spec,
                  "domain_id": None, "is_sandbox": False}
        stage.db = MemoryDb(report, path.read_bytes())
        t = time.monotonic()
        stage.run(report)
        intake = report["intake"]
        print(f"\n== {path.stem}: {report['intake_status']} ({int((time.monotonic() - t) * 1000)} ms, "
              f"budget {intake['budget']}, classifier {intake['models']['classifier']})")
        for c in intake["candidates"]:
            print(f"  #{c['candidate_id']} {c['prompt']} sam {c['sam_score']} box {c['box_xyxy']} "
                  f"confuser {c['main_confuser']} -> {json.dumps(c['classifier'])}")
        if intake["whole_image_check"]:
            print(f"  whole photo -> {json.dumps(intake['whole_image_check'])}")
        if intake["global_detections"]:
            print(f"  global: {intake['global_detections']}")
    print(f"\npublic copies and outlined crops in {OUT.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
