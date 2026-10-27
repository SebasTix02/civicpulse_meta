"""Day-1 check of SAM 3.1 on the Meta Model API against the notebook, with META_MODEL_API_KEY from .env.

    pip install -e services/orchestrator pillow numpy
    python scripts/smoke_sam.py                 # 3 pothole pairs (before + after), 3 prompts each
    python scripts/smoke_sam.py --pairs 5 --prompts "pothole,road crack"

Only already anonymized photos (data/private/anon/) are sent: one request per photo and prompt
(USD 0.0025 each). Prints every detection with its confidence and the latency, and saves overlays
to data/private/sam_smoke/ so you can see the masks. Notebook reference: pothole ~0.93 on a clear
pothole, closure thresholds visible 0.60 / candidate 0.10.
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import os
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from meta_sam_parser import CompletedOutcome, decode_mask_to_raster, image_segmentation_format
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
sys.path.insert(0, str(ROOT / "scripts"))

from civicpulse.sam.service import MetaApiSAM
from smoke_models import load_env

ANON = ROOT / "data" / "private" / "anon"
OUT = ROOT / "data" / "private" / "sam_smoke"
COLORS = {0: (255, 60, 60), 1: (60, 160, 255), 2: (255, 200, 0), 3: (80, 220, 120)}


def pairs(n: int) -> list[tuple[str, str]]:
    """First n cases usable as a pair whose two photos were not blurred (nothing hidden)."""
    decisions = {}
    report = ROOT / "data" / "private" / "prefilter.csv"
    if report.exists():
        decisions = {Path(r["photo"]).stem: r for r in csv.DictReader(report.open(encoding="utf-8"))}
    out = []
    for row in csv.DictReader((ROOT / "data" / "pothole_pairs" / "pairs.csv").open(encoding="utf-8")):
        if row.get("usable_as") != "pair":
            continue
        sides = [f"{row['pair_id']}_before", f"{row['pair_id']}_after"]
        if all((ANON / f"{s}.jpg").exists() and float(decisions.get(s, {}).get("blurred_share") or 0) < 0.2
               for s in sides):
            out.append((sides[0], sides[1]))
        if len(out) == n:
            break
    return out


def jpeg(path: Path) -> tuple[bytes, Image.Image]:
    im = Image.open(path).convert("RGB")
    im.thumbnail((1600, 1600))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue(), im


def overlay(im: Image.Image, layers: list[tuple[int, str, list]]) -> Image.Image:
    base = np.asarray(im).astype(np.float32)
    draw_boxes = []
    for color_idx, prompt, records in layers:
        color = np.array(COLORS[color_idx % 4], np.float32)
        for r in records:
            b = r.bounds
            local = np.frombuffer(decode_mask_to_raster(r.mask), np.uint8).reshape(r.mask.height, r.mask.width)
            w, h = int(b.right - b.left), int(b.bottom - b.top)
            full = np.asarray(Image.fromarray(local * 255).resize((w, h), Image.Resampling.NEAREST)) > 0
            region = base[int(b.top):int(b.bottom), int(b.left):int(b.right)]
            region[full] = region[full] * 0.45 + color * 0.55
            draw_boxes.append((color_idx, prompt, r, b))
    out = Image.fromarray(base.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(out)
    for color_idx, prompt, r, b in draw_boxes:
        d.rectangle([b.left, b.top, b.right, b.bottom], outline=COLORS[color_idx % 4], width=3)
        d.text((b.left + 4, b.top + 4), f"{prompt} {r.confidence if r.confidence is not None else '?'}",
               fill=COLORS[color_idx % 4])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=3)
    ap.add_argument("--prompts", default="pothole,person,license plate")
    args = ap.parse_args()
    load_env()
    key = os.environ.get("META_MODEL_API_KEY")
    if not key:
        print("META_MODEL_API_KEY missing in .env", file=sys.stderr)
        return 2
    prompts = [p.strip() for p in args.prompts.split(",") if p.strip()]
    sam = MetaApiSAM(key, os.environ.get("META_API_BASE", "https://api.meta.ai/v1"))
    OUT.mkdir(parents=True, exist_ok=True)
    chosen = pairs(args.pairs)
    if not chosen:
        print("no anonymized pairs found: run qc.py and prefilter.py first", file=sys.stderr)
        return 2
    total_ms, calls = 0, 0
    for before, after in chosen:
        for stem in (before, after):
            data, im = jpeg(ANON / f"{stem}.jpg")
            image_url = "data:image/jpeg;base64," + base64.b64encode(data).decode()
            layers, line = [], []
            for i, prompt in enumerate(prompts):
                t = time.monotonic()
                try:
                    text = MetaApiSAM.output_text(sam._ask(image_url, prompt))
                except httpx.HTTPError as exc:  # show the API error and keep going
                    body = exc.response.text[:300] if isinstance(exc, httpx.HTTPStatusError) else ""
                    line.append(f"{prompt}: ERROR {str(exc)[:120]} {body}")
                    continue
                ms = int((time.monotonic() - t) * 1000)
                total_ms, calls = total_ms + ms, calls + 1
                parser = image_segmentation_format().create_parser()
                parser.push(text if text.endswith("\n") else text + "\n", emit=False)
                records = [r for r in parser.finish(CompletedOutcome()).result.records if r.kind == "mask"]
                scores = sorted((round(r.confidence, 2) for r in records if r.confidence is not None), reverse=True)
                line.append(f"{prompt}: {len(records)} {scores[:5]} {ms} ms")
                layers.append((i, prompt, records))
                assert len(MetaApiSAM.parse(prompt, text, 0.0)) == len(records)  # service parser agrees
            overlay(im, layers).save(OUT / f"{stem}.jpg", quality=85)
            print(f"{stem}: " + " | ".join(line))
    if calls:
        print(f"\n{calls} requests, {total_ms // calls} ms average, about USD {calls * 0.0025:.3f}")
    print(f"overlays in {OUT.relative_to(ROOT)}/ (red = {prompts[0]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
