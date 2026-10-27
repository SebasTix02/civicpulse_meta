"""Anonymize the pothole pair photos on your own CPU, without uploading anything.

    pip install "opencv-python-headless<5" pillow numpy
    python data/pothole_pairs/prefilter.py            # everything local (default)
    python data/pothole_pairs/prefilter.py --sam      # flagged photos go to the SAM 3.1 Colab notebook instead

Two small OpenCV Zoo detectors (downloaded once into data/private/models/, ~4 MB, checked by
sha256) run on every photo that qc.py did not mark as a placeholder:
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
import hashlib
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "data" / "private"
RAW = PRIVATE / "raw"
MODELS = PRIVATE / "models"
ANON = PRIVATE / "anon"
NEEDS_SAM = PRIVATE / "needs_sam"
REVIEW = PRIVATE / "review"

ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
MODEL_FILES = {
    "yunet.onnx": (f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
                   "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "nanodet.onnx": (f"{ZOO}/object_detection_nanodet/object_detection_nanodet_2022nov.onnx",
                     "4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186"),
}

MAX_SIDE = 1600
FACE_MIN_SCORE = 0.5
PERSON_MIN_SCORE = 0.25
VEHICLE_MIN_SCORE = 0.30
VEHICLE_MIN_WIDTH = 0.08  # of the photo width: smaller cars are too far for a legible plate
COCO_PERSON, COCO_VEHICLES = 0, {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


def fetch_models() -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    for name, (url, digest) in MODEL_FILES.items():
        path = MODELS / name
        if not path.exists():
            print(f"downloading {name} ...")
            urllib.request.urlretrieve(url, path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            path.unlink()
            raise SystemExit(f"{name}: sha256 mismatch, deleted; run again")


def load(path: Path) -> np.ndarray:
    """RGB array with EXIF orientation applied and the long side at most MAX_SIDE."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        return np.asarray(im)


Box = tuple[str, float, int, int, int, int]  # kind, score, x1, y1, x2, y2 in photo pixels


class Faces:
    def __init__(self) -> None:
        self.net = cv2.FaceDetectorYN.create(str(MODELS / "yunet.onnx"), "", (320, 320), FACE_MIN_SCORE, 0.3, 5000)

    def find(self, rgb: np.ndarray) -> list[Box]:
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        self.net.setInputSize((bgr.shape[1], bgr.shape[0]))
        _, faces = self.net.detect(bgr)
        if faces is None:
            return []
        return [("face", float(f[-1]), int(f[0]), int(f[1]), int(f[0] + f[2]), int(f[1] + f[3])) for f in faces]


class Objects:
    """NanoDet-Plus 416 (COCO), pre/post-processing as in the OpenCV Zoo demo."""

    SIZE, STRIDES, REG_MAX = 416, (8, 16, 32, 64), 7

    def __init__(self) -> None:
        self.net = cv2.dnn.readNet(str(MODELS / "nanodet.onnx"))
        self.mean = np.array([103.53, 116.28, 123.675], np.float32)
        self.std = np.array([57.375, 57.12, 58.395], np.float32)
        self.anchors = []
        for s in self.STRIDES:
            n = self.SIZE // s
            xv, yv = np.meshgrid(np.arange(n) * s, np.arange(n) * s)
            self.anchors.append(np.column_stack((xv.ravel() + 0.5 * (s - 1), yv.ravel() + 0.5 * (s - 1))))

    def detect(self, rgb: np.ndarray, ox: int = 0, oy: int = 0) -> list[Box]:
        """People and vehicles in this crop; boxes shifted by (ox, oy) into photo pixels."""
        h, w = rgb.shape[:2]
        scale = self.SIZE / max(h, w)
        nh, nw = round(h * scale), round(w * scale)
        canvas = np.zeros((self.SIZE, self.SIZE, 3), np.uint8)
        canvas[:nh, :nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
        self.net.setInput(cv2.dnn.blobFromImage((canvas.astype(np.float32) - self.mean) / self.std))
        outs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        # match outputs by shape, not order (OpenCV 4 and 5 list them differently): finest stride first
        cls_outs = sorted((o.reshape(-1, 80) for o in outs if o.shape[-1] == 80), key=len)[::-1]
        reg_outs = sorted((o.reshape(-1, 4, self.REG_MAX + 1) for o in outs if o.shape[-1] == 32), key=len)[::-1]
        found: list[Box] = []
        for cls, reg, anchors, stride in zip(cls_outs, reg_outs, self.anchors, self.STRIDES):
            for c in (COCO_PERSON, *COCO_VEHICLES):  # each class of interest, even when not the top one
                kind = "person" if c == COCO_PERSON else "vehicle"
                keep = cls[:, c] >= (PERSON_MIN_SCORE if kind == "person" else VEHICLE_MIN_SCORE)
                if not keep.any():
                    continue
                e = np.exp(reg[keep])
                d = (e / e.sum(2, keepdims=True)) @ np.arange(self.REG_MAX + 1) * stride
                a = anchors[keep]
                x1, x2 = np.clip(a[:, 0] - d[:, 0], 0, nw) / scale + ox, np.clip(a[:, 0] + d[:, 2], 0, nw) / scale + ox
                y1, y2 = np.clip(a[:, 1] - d[:, 1], 0, nh) / scale + oy, np.clip(a[:, 1] + d[:, 3], 0, nh) / scale + oy
                found += [(kind, float(sc), int(b[0]), int(b[1]), int(b[2]), int(b[3]))
                          for sc, b in zip(cls[keep, c], np.column_stack((x1, y1, x2, y2)))]
        return found

    def scan(self, rgb: np.ndarray) -> list[Box]:
        """Whole photo plus four overlapping tiles, so a small person in a corner is still seen."""
        h, w = rgb.shape[:2]
        found = self.detect(rgb)
        th, tw = int(h * 0.6), int(w * 0.6)
        for y in (0, h - th):
            for x in (0, w - tw):
                found += self.detect(rgb[y:y + th, x:x + tw], x, y)
        w_min = VEHICLE_MIN_WIDTH * w  # far vehicles: plate too small to read, leave them sharp
        return [b for b in found if b[0] != "vehicle" or b[4] - b[2] >= w_min]


PAD = {"face": 0.3, "person": 0.1, "vehicle": 0.05}


def blur_boxes(rgb: np.ndarray, boxes: list[Box]) -> tuple[np.ndarray, float]:
    """Pixelate and blur every padded box, same treatment as the SAM notebook gives its masks.
    Also returns the blurred share of the photo: a large one may hide the pothole too."""
    h, w = rgb.shape[:2]
    mask = np.zeros((h, w), bool)
    for kind, _, x1, y1, x2, y2 in boxes:
        px, py = int((x2 - x1) * PAD[kind]), int((y2 - y1) * PAD[kind])
        mask[max(0, y1 - py):min(h, y2 + py), max(0, x1 - px):min(w, x2 + px)] = True
    small = cv2.resize(rgb, (max(1, w // 24), max(1, h // 24)), interpolation=cv2.INTER_AREA)
    hidden = cv2.GaussianBlur(cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST), (0, 0), 8)
    return np.where(mask[..., None], hidden, rgb), float(mask.mean())


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
    photos = [RAW / r["photo"] for r in csv.DictReader(qc.open(encoding="utf-8")) if r["status"] != "placeholder"]
    report = PRIVATE / "prefilter.csv"
    rows = list(csv.reader(report.open(encoding="utf-8")))[1:] if args.resume and report.exists() else []
    seen = {r[0] for r in rows}
    photos = [p for p in photos if p.exists() and p.name not in seen][: args.limit]
    deadline = time.monotonic() + args.max_minutes * 60 if args.max_minutes else None
    fetch_models()
    faces, objects = Faces(), Objects()
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
