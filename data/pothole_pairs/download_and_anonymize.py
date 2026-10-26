"""Download the pothole pair photos listed in the private manifest and write anonymized copies.

Run on a machine that can reach the source server (it refuses cloud IPs):

    pip install httpx pillow "opencv-python-headless<5"   # 5.x dropped the Haar cascades
    python data/pothole_pairs/download_and_anonymize.py [--limit 50]

Layout (all under git-ignored data/private/):
  raw/<pair_id>_before.<ext>, raw/<pair_id>_after.<ext>   originals, never uploaded anywhere
  anon/<pair_id>_before.jpg,  anon/<pair_id>_after.jpg     anonymized copies
  anon/report.csv                                          what was blurred, for manual review

Anonymization of each photo:
  1. EXIF orientation applied, then ALL metadata dropped (GPS, device, timestamps): the
     image is re-encoded from pixels only.
  2. Longest side resized to 1600 px (same as the PWA does before upload).
  3. Faces (frontal + profile) and license plates detected with OpenCV cascades and blurred
     with a padded, strong Gaussian blur. Cascades miss things, so:
  4. A second pass with SAM 3.1 prompts "face" and "license plate" (same as the product's
     anonymization, plan §5.3) runs once SAMService is live: scripts/anonymize_with_sam.py.
     Until that pass and a manual look at report.csv, anon/ photos are for local eval only
     and are not published.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "data" / "private"
MANIFEST = PRIVATE / "pairs_manifest.csv"
RAW, ANON = PRIVATE / "raw", PRIVATE / "anon"
MAX_SIDE = 1600

CASCADES = {
    "face": ["haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"],
    "plate": ["haarcascade_russian_plate_number.xml", "haarcascade_license_plate_rus_16stages.xml"],
}


def load_cascades() -> dict[str, list[cv2.CascadeClassifier]]:
    out = {}
    for kind, files in CASCADES.items():
        out[kind] = [c for f in files if not (c := cv2.CascadeClassifier(cv2.data.haarcascades + f)).empty()]
    return out


def download(client: httpx.Client, url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    for attempt in range(3):
        try:
            resp = client.get(url)
            if resp.status_code == 200 and resp.content:
                dest.write_bytes(resp.content)
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2 ** attempt)
    return False


def blur_boxes(rgb: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> np.ndarray:
    h, w = rgb.shape[:2]
    for x, y, bw, bh in boxes:
        pad = int(0.25 * max(bw, bh))
        x0, y0, x1, y1 = max(0, x - pad), max(0, y - pad), min(w, x + bw + pad), min(h, y + bh + pad)
        roi = rgb[y0:y1, x0:x1]
        k = max(31, (max(x1 - x0, y1 - y0) // 2) | 1)
        rgb[y0:y1, x0:x1] = cv2.GaussianBlur(roi, (k, k), 0)
    return rgb


def anonymize(raw_bytes: bytes, cascades) -> tuple[bytes, dict[str, int]]:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes))).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    rgb = np.array(img)
    gray = cv2.equalizeHist(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
    counts, boxes = {}, []
    for kind, models in cascades.items():
        found = []
        for m in models:
            found += [tuple(map(int, b)) for b in m.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(24, 24))]
        counts[kind] = len(found)
        boxes += found
    rgb = blur_boxes(rgb, boxes)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=88)  # new file from pixels: no EXIF survives
    return buf.getvalue(), counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="only the first N pairs (0 = all)")
    args = parser.parse_args()
    if not MANIFEST.exists():
        print("run data/pothole_pairs/prepare.py first", file=sys.stderr)
        return 2
    RAW.mkdir(parents=True, exist_ok=True)
    ANON.mkdir(parents=True, exist_ok=True)
    cascades = load_cascades()
    rows = list(csv.DictReader(MANIFEST.open(encoding="utf-8")))
    if args.limit:
        rows = rows[: args.limit]

    report, failed = [], 0
    with httpx.Client(timeout=30.0, follow_redirects=True, verify=True) as client:
        for i, row in enumerate(rows, 1):
            for side in ("before", "after"):
                url = row[f"{side}_url"]
                if not url:
                    continue
                ext = url.rsplit(".", 1)[-1].lower()
                raw = RAW / f"{row['pair_id']}_{side}.{ext}"
                if not download(client, url, raw):
                    failed += 1
                    continue
                data, counts = anonymize(raw.read_bytes(), cascades)
                (ANON / f"{row['pair_id']}_{side}.jpg").write_bytes(data)
                report.append({"pair_id": row["pair_id"], "side": side, "faces_blurred": counts.get("face", 0),
                               "plates_blurred": counts.get("plate", 0), "reviewed": ""})
            if i % 50 == 0:
                print(f"{i}/{len(rows)} pairs")

    with (ANON / "report.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["pair_id", "side", "faces_blurred", "plates_blurred", "reviewed"])
        w.writeheader()
        w.writerows(report)
    print(f"{len(report)} photos anonymized, {failed} downloads failed -> {ANON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
