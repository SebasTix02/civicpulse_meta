"""Anonymize the pothole pair photos on your own CPU, without uploading anything.

    pip install "opencv-python-headless<5" pillow numpy
    python data/pothole_pairs/prefilter.py            # everything local (default)
    python data/pothole_pairs/prefilter.py --sam      # flagged photos go to the SAM 3.1 Colab notebook instead

Two small OpenCV Zoo detectors (downloaded once into data/private/models/, ~4 MB, checked by
sha256) run on every photo that qc.py marked ok or reused (no placeholders, nothing banned):
  YuNet        faces, on the whole photo at full size
  NanoDet-Plus people, bicycles, motorcycles, cars, buses, trucks; on the whole photo and on
               four overlapping tiles, so a small person in a corner is still seen

Every detected box is padded, pixelated and blurred: the whole person, the whole vehicle when it
is close enough for a plate to be legible. Boxes cover more than SAM masks would, which is fine:
potholes are on the road surface. Thresholds lean towards blurring: a false alarm costs a blurred
patch, a miss leaves a face in the data.

Writes (all under data/private/, never in git):
  anon/<photo>.jpg     every photo: EXIF orientation applied, at most 1600 px, boxes blurred, JPEG
                       rebuilt from pixels so no metadata survives
  prefilter.csv        photo, decision (clean / blurred / needs_sam), what was found, best scores,
                       blurred_share (over ~0.4 the pothole may be hidden: check it, keep it out of evals)
  review/clean_NN.jpg, review/blurred_NN.jpg   contact sheets: skim them before using anon/
  needs_sam/ + needs_sam.zip   only with --sam: flagged photos, untouched, for the notebook
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import time
import zipfile
from pathlib import Path

from banned import banned_stems
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))  # same detectors as live reports

from civicpulse.anonymize import PAD, Faces, Objects, blur_boxes, fetch_models, load

PRIVATE = ROOT / "data" / "private"
RAW = PRIVATE / "raw"
MODELS = PRIVATE / "models"
ANON = PRIVATE / "anon"
NEEDS_SAM = PRIVATE / "needs_sam"
REVIEW = PRIVATE / "review"


def contact_sheets(names: list[str], prefix: str, per_sheet: int = 48, cols: int = 8, cell: int = 240) -> int:
    REVIEW.mkdir(parents=True, exist_ok=True)
    for old in REVIEW.glob(f"{prefix}_*.jpg"):
        old.unlink()
    sheets = 0
    for start in range(0, len(names), per_sheet):
        chunk = names[start:start + per_sheet]
        rows = -(-len(chunk) // cols)
        sheet = Image.new("RGB", (cols * cell, rows * cell), "white")
        draw = ImageDraw.Draw(sheet)
        for i, name in enumerate(chunk):
            x, y = (i % cols) * cell, (i // cols) * cell
            with Image.open(ANON / name) as im:
                im.thumbnail((cell - 4, cell - 18))
                sheet.paste(im, (x + 2, y + 2))
            draw.text((x + 4, y + cell - 15), Path(name).stem, fill="black")
        sheets += 1
        sheet.save(REVIEW / f"{prefix}_{sheets:02d}.jpg", quality=80)
    return sheets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, help="only the first N photos (try it out)")
    ap.add_argument("--sam", action="store_true",
                    help="do not blur flagged photos here; pack them into needs_sam.zip for the Colab notebook")
    ap.add_argument("--resume", action="store_true", help="keep prefilter.csv and skip the photos already in it")
    ap.add_argument("--max-minutes", type=float, help="stop cleanly after this long; continue later with --resume")
    args = ap.parse_args()

    qc = PRIVATE / "photo_qc.csv"
    if not qc.exists():
        print("run qc.py first: it marks the logo placeholders", file=sys.stderr)
        return 2
    banned = banned_stems()
    photos = [RAW / r["photo"] for r in csv.DictReader(qc.open(encoding="utf-8"))
              if r["status"] in ("ok", "reused") and Path(r["photo"]).stem not in banned]
    for stem in banned:  # banned after an earlier run: its anonymized copy goes too
        (ANON / f"{stem}.jpg").unlink(missing_ok=True)
    report = PRIVATE / "prefilter.csv"
    rows = list(csv.reader(report.open(encoding="utf-8")))[1:] if args.resume and report.exists() else []
    rows = [r for r in rows if Path(r[0]).stem not in banned]
    seen = {r[0] for r in rows}
    photos = [p for p in photos if p.exists() and p.name not in seen][: args.limit]
    deadline = time.monotonic() + args.max_minutes * 60 if args.max_minutes else None
    fetch_models(MODELS)
    faces, objects = Faces(MODELS), Objects(MODELS)
    ANON.mkdir(parents=True, exist_ok=True)
    if args.sam and not args.resume:
        shutil.rmtree(NEEDS_SAM, ignore_errors=True)
    if args.sam:
        NEEDS_SAM.mkdir(parents=True, exist_ok=True)

    stopped = False
    for i, p in enumerate(photos, 1):
        if deadline and time.monotonic() > deadline:
            print(f"stopped after {i - 1} photos; run again with --resume")
            stopped = True
            break
        try:
            rgb = load(p)
        except OSError as e:
            rows.append([p.name, "unreadable", str(e), "", "", "", ""])
            continue
        boxes = faces.find(rgb) + objects.scan(rgb)
        best = {k: max((b[1] for b in boxes if b[0] == k), default=0.0) for k in PAD}
        reasons = "+".join(k for k in PAD if best[k])
        out, covered = f"{p.stem}.jpg", 0.0
        if not boxes:
            decision = "clean"
        elif args.sam:
            decision = "needs_sam"
            shutil.copy2(p, NEEDS_SAM / p.name)
            (ANON / out).unlink(missing_ok=True)  # a stale local copy must not survive a re-run
        else:
            decision = "blurred"
            rgb, covered = blur_boxes(rgb, boxes)
        if decision != "needs_sam":
            Image.fromarray(rgb).save(ANON / out, "JPEG", quality=90)  # new file from pixels: no EXIF
        rows.append([p.name, decision, reasons, *(f"{best[k]:.2f}" for k in PAD), f"{covered:.2f}"])
        if i % 50 == 0 or i == len(photos):
            print(f"{i}/{len(photos)}", flush=True)

    with report.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["photo", "decision", "reasons", "face", "person", "vehicle", "blurred_share"])
        w.writerows(rows)
    if stopped:
        return 0
    done = {d: [f"{Path(r[0]).stem}.jpg" for r in rows if r[1] == d] for d in ("clean", "blurred", "needs_sam")}
    print(f"clean: {len(done['clean'])}, {contact_sheets(done['clean'], 'clean')} review sheets")
    if args.sam:
        with zipfile.ZipFile(PRIVATE / "needs_sam.zip", "w", zipfile.ZIP_STORED) as zf:
            for p in sorted(NEEDS_SAM.iterdir()):
                zf.write(p, p.name)
        print(f"needs SAM: {len(done['needs_sam'])} -> data/private/needs_sam.zip (upload it to MyDrive/civicpulse/)")
    else:
        print(f"blurred here: {len(done['blurred'])}, {contact_sheets(done['blurred'], 'blurred')} review sheets")
    print("all output in data/private/anon/, review sheets in data/private/review/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
