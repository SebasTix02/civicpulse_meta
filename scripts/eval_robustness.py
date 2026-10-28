"""Robustness test of the vision stage: does it still get the pothole right on worse photos?

    pip install -e "services/orchestrator[vision]"
    python scripts/eval_robustness.py --dry-run      # what it would cost, nothing is sent
    python scripts/eval_robustness.py                # 20 repaired pairs x 6 variants, keys from .env
    python scripts/eval_robustness.py --pairs 70 --workers 4

Takes the pairs labeled `repaired` in data/pothole_pairs/closure_labels_v2.csv. Their "before" photo
shows a pothole (it should be confirmed) and their "after" photo shows the same street fixed (it
must not be confirmed). Each photo is degraded in the ways a citizen's photo usually is
(rotated, dark, overexposed, blurred, small and compressed) and goes through VisionStage exactly
as in Modal, with real SAM 3.1 and the real Muse classifier, against an in-memory stand-in for
Supabase. Only the already anonymized copies in data/private/anon/ are used, and nothing is
uploaded anywhere.

Per variant it prints how many potholes were confirmed, how many repaired streets were wrongly
confirmed (must stay 0) and how many went to review, with the crop checked once (one view) and
with the wider second view that production now asks for (two views). Results go to
data/private/robustness.csv; a run that stops can continue with --resume. Spend so far is
printed from the real request and token counts.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import threading
import time
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
from civicpulse.pipeline.vision import VisionStage, _confirms
from smoke_models import load_env

ANON = ROOT / "data" / "private" / "anon"
LABELS = ROOT / "data" / "pothole_pairs" / "closure_labels_v2.csv"
PREFILTER = ROOT / "data" / "private" / "prefilter.csv"
OUT = ROOT / "data" / "private" / "robustness.csv"
MAX_BLURRED = 0.4
# Estimates for --dry-run, then replaced by the real counts. Muse Spark Open on Baseten: USD 1.25
# per 1M input and 4.25 per 1M output tokens (third-party price list, 2026-10-28); SAM 3.1 on the
# Meta Model API: USD 0.0025 per request.
PRICE_IN, PRICE_OUT, PRICE_SAM = 1.25e-6, 4.25e-6, 0.0025
EST_SAM_PER_RUN, EST_LLM_PER_RUN, EST_TOKENS_IN, EST_TOKENS_OUT = 5.5, 2.2, 1500, 450
FIELDS = ["pair_id", "side", "variant", "expected", "one_view", "two_views", "llm_calls", "ms", "error"]


def rotated(rgb):
    return cv2.rotate(rgb, cv2.ROTATE_90_COUNTERCLOCKWISE)


def dark(rgb):
    return np.clip(rgb.astype(np.float32) * 0.35, 0, 255).astype(np.uint8)


def bright(rgb):
    return np.clip(rgb.astype(np.float32) * 1.7 + 40, 0, 255).astype(np.uint8)


def blurred(rgb):
    return cv2.GaussianBlur(rgb, (0, 0), 4)


def small(rgb):
    h, w = rgb.shape[:2]
    scale = 480 / max(h, w)
    tiny = cv2.resize(rgb, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    _, buf = cv2.imencode(".jpg", cv2.cvtColor(tiny, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 25])
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


VARIANTS = {"original": lambda rgb: rgb, "rotated": rotated, "dark": dark, "bright": bright,
            "blurred": blurred, "small": small}


class MemoryDb:
    """Just what VisionStage uses. Uploads are dropped: nothing leaves this computer but the
    SAM and classifier requests."""

    def __init__(self, report: dict, photo: bytes):
        self.photo = photo
        self.rows = {"reports": report, "report_photos": {"photo_id": "p1"},
                     "photos": {"id": "p1", "storage_path": "raw/eval.jpg"}, "domains": {"count_prompts": []}}

    def select_one(self, table, **eq):
        return self.rows[table]

    def stage_done(self, *a, **k):
        return False

    def insert(self, *a, **k):
        pass

    def update(self, *a, **k):
        pass

    def download(self, path):
        return self.photo

    def rpc(self, fn, args):
        return {"reused": False, "matches": []}

    def select(self, table, params):
        return []

    def upload(self, *a, **k):
        pass

    def compare_and_set(self, table, id_, expected, patch):
        self.rows[table] |= patch
        return True


class CountingSAM:
    def __init__(self, sam):
        self.sam, self.requests, self.lock = sam, 0, threading.Lock()

    def segment(self, image_jpeg, prompts, threshold=0.4):
        with self.lock:
            self.requests += len(prompts)
        return self.sam.segment(image_jpeg, prompts, threshold=threshold)


def same_photo(jpeg: bytes) -> Anonymized:
    """The photos are already anonymized; the degraded copy goes on as it is."""
    rgb = load(jpeg)
    return Anonymized(jpeg, rgb.shape[1], rgb.shape[0], faces=0, people=0, vehicles=0, blurred_share=0.0)


def pairs(n: int) -> list[str]:
    blur = {}
    if PREFILTER.exists():
        blur = {Path(r["photo"]).stem: float(r["blurred_share"] or 0)
                for r in csv.DictReader(PREFILTER.open(encoding="utf-8"))}
    out = []
    for r in csv.DictReader(LABELS.open(encoding="utf-8")):
        sides = [f"{r['pair_id']}_before", f"{r['pair_id']}_after"]
        if r["label"] == "repaired" and all((ANON / f"{s}.jpg").exists() and blur.get(s, 0) <= MAX_BLURRED
                                            for s in sides):
            out.append(r["pair_id"])
    return out[:n]


def outcome(status: str | None) -> str:
    if status == "REPORT_CONFIRMED_BY_PHOTO":
        return "confirmed"
    if status and status.endswith("REQUIRES_REVIEW"):
        return "review"
    return "not_found"


def run_one(job, spec, router, sam) -> dict:
    pair_id, side, variant = job
    photo = to_jpeg(VARIANTS[variant](load((ANON / f"{pair_id}_{side}.jpg").read_bytes())), 90)
    report = {"id": f"{pair_id}_{side}_{variant}", "status": "spec_ready", "current_photo_version": 1,
              "spec": spec, "domain_id": None, "is_sandbox": False}
    stage = VisionStage(MemoryDb(report, photo), router, sam, same_photo)
    row = {"pair_id": pair_id, "side": side, "variant": variant,
           "expected": "confirmed" if side == "before" else "not_confirmed", "error": ""}
    started = time.monotonic()
    try:
        stage.run(report)
        intake = report["intake"]
        row["two_views"] = outcome(report["intake_status"])
        # With one view the first confirming crop was enough; the rest of the run is the same.
        one = any(_confirms(c.get("classifier")) for c in intake["candidates"])
        row["one_view"] = "confirmed" if one else row["two_views"]
        row["llm_calls"] = sum(bool(c.get("classifier")) + bool(c.get("second_view")) for c in intake["candidates"]) \
            + bool(intake["whole_image_check"])
    except Exception as exc:  # noqa: BLE001 - recorded, the rest keeps going
        row |= {"one_view": "", "two_views": "", "llm_calls": 0, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    row["ms"] = int((time.monotonic() - started) * 1000)
    return row


def summary(rows: list[dict]) -> None:
    print(f"\n{'variant':<9} {'potholes confirmed':>20} {'repaired confirmed':>20} {'to review':>11}  errors")
    for view in ("one_view", "two_views"):
        print(f"-- {view.replace('_', ' ')}")
        for variant in VARIANTS:
            vr = [r for r in rows if r["variant"] == variant and not r["error"]]
            pos = [r for r in vr if r["side"] == "before"]
            neg = [r for r in vr if r["side"] == "after"]
            hit = sum(r[view] == "confirmed" for r in pos)
            false = sum(r[view] == "confirmed" for r in neg)
            review = sum(r[view] == "review" for r in vr)
            errors = sum(1 for r in rows if r["variant"] == variant and r["error"])
            print(f"{variant:<9} {hit:>9}/{len(pos):<3} ({100 * hit / max(1, len(pos)):3.0f}%) "
                  f"{false:>9}/{len(neg):<3} ({100 * false / max(1, len(neg)):3.0f}%) {review:>11}  {errors}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=20)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    variants = [v for v in args.variants.split(",") if v in VARIANTS]
    chosen = pairs(args.pairs)
    if not chosen:
        print("no repaired pair with both anonymized photos: check closure_labels_v2.csv and data/private/anon/",
              file=sys.stderr)
        return 2
    done = []
    if args.resume and OUT.exists():
        done = [r for r in csv.DictReader(OUT.open(encoding="utf-8")) if not r["error"]]
    seen = {(r["pair_id"], r["side"], r["variant"]) for r in done}
    jobs = [(p, s, v) for p in chosen for s in ("before", "after") for v in variants if (p, s, v) not in seen]
    sam_usd = len(jobs) * EST_SAM_PER_RUN * PRICE_SAM
    llm_usd = len(jobs) * EST_LLM_PER_RUN * (EST_TOKENS_IN * PRICE_IN + EST_TOKENS_OUT * PRICE_OUT)
    print(f"{len(chosen)} pairs x 2 photos x {len(variants)} variants = {len(jobs)} runs to do; estimate "
          f"USD {sam_usd:.2f} of Meta credits (SAM) + USD {llm_usd:.2f} of Baseten credits (Muse)")
    if args.dry_run or not jobs:
        if done:
            summary(done)
        return 0

    load_env()
    if not os.environ.get("META_MODEL_API_KEY"):
        print("META_MODEL_API_KEY missing in .env", file=sys.stderr)
        return 2
    os.environ.setdefault("SAM_BACKEND", "meta_api")
    os.environ.setdefault("CLASSIFY_SECOND_VIEW", "1")
    settings = Settings()
    catalog = Catalog.load(REPO_ROOT / "packages/catalog/civic_catalog.json")
    spec = catalog.merge_spec(SpecLLMOutput(
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
            futures = [pool.submit(run_one, j, spec, router, sam) for j in jobs]
            for k, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                rows.append(row)
                out.writerow(row)
                f.flush()
                if k % 10 == 0 or k == len(jobs):
                    tokens = list(router.usage.values())
                    usd = sam.requests * PRICE_SAM + sum(u["prompt_tokens"] * PRICE_IN
                                                         + u["completion_tokens"] * PRICE_OUT for u in tokens)
                    print(f"  {k}/{len(jobs)} runs, {sam.requests} SAM requests, "
                          f"{sum(u['calls'] for u in tokens)} Muse calls, about USD {usd:.2f} so far")
    summary(rows)
    print(f"\nper-photo results in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
