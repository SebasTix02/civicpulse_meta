"""Blur faces, people and close vehicles on the CPU before a photo leaves our infrastructure.

Every photo that goes to the Meta Model API (free credits: Meta may use the content) or to the
public bucket is this copy, never the raw one. Two small OpenCV Zoo detectors, ~4 MB, checked
by sha256:
  YuNet        faces, on the whole photo at full size
  NanoDet-Plus people, bicycles, motorcycles, cars, buses, trucks; on the whole photo and on
               four overlapping tiles, so a small person in a corner is still seen

Every detected box is padded, pixelated and blurred: the whole person, the whole vehicle when it
is close enough for a plate to be legible. Thresholds lean towards blurring: a false alarm costs
a blurred patch, a miss leaves a face in the data. Shared with data/pothole_pairs/prefilter.py.

    pip install -e "services/orchestrator[vision]"
"""

from __future__ import annotations

import hashlib
import io
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

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
PAD = {"face": 0.3, "person": 0.1, "vehicle": 0.05}

Box = tuple[str, float, int, int, int, int]  # kind, score, x1, y1, x2, y2 in photo pixels


def fetch_models(models_dir: Path) -> None:
    models_dir.mkdir(parents=True, exist_ok=True)
    for name, (url, digest) in MODEL_FILES.items():
        path = models_dir / name
        if not path.exists():
            print(f"downloading {name} ...")
            urllib.request.urlretrieve(url, path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            path.unlink()
            raise SystemExit(f"{name}: sha256 mismatch, deleted; run again")


def load(source: Path | bytes) -> np.ndarray:
    """RGB array with EXIF orientation applied and the long side at most MAX_SIDE."""
    with Image.open(io.BytesIO(source) if isinstance(source, bytes) else source) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        return np.asarray(im)


def to_jpeg(rgb: np.ndarray, quality: int = 90) -> bytes:
    """A new file built from pixels: no EXIF or other metadata survives."""
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


class Faces:
    def __init__(self, models_dir: Path) -> None:
        self.net = cv2.FaceDetectorYN.create(str(models_dir / "yunet.onnx"), "", (320, 320), FACE_MIN_SCORE, 0.3, 5000)

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

    def __init__(self, models_dir: Path) -> None:
        self.net = cv2.dnn.readNet(str(models_dir / "nanodet.onnx"))
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


def blur_boxes(rgb: np.ndarray, boxes: list[Box]) -> tuple[np.ndarray, float]:
    """Pixelate and blur every padded box, same treatment as the SAM notebook gives its masks.
    Also returns the blurred share of the photo: a large one may hide the reported problem too."""
    h, w = rgb.shape[:2]
    mask = np.zeros((h, w), bool)
    for kind, _, x1, y1, x2, y2 in boxes:
        px, py = int((x2 - x1) * PAD[kind]), int((y2 - y1) * PAD[kind])
        mask[max(0, y1 - py):min(h, y2 + py), max(0, x1 - px):min(w, x2 + px)] = True
    small = cv2.resize(rgb, (max(1, w // 24), max(1, h // 24)), interpolation=cv2.INTER_AREA)
    hidden = cv2.GaussianBlur(cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST), (0, 0), 8)
    return np.where(mask[..., None], hidden, rgb), float(mask.mean())


@dataclass
class Anonymized:
    jpeg: bytes
    width: int
    height: int
    faces: int
    people: int  # boxes, before merging the tile duplicates: a hint, not a count
    vehicles: int
    blurred_share: float


class Anonymizer:
    """Loads both detectors once (a Modal container keeps them between reports)."""

    def __init__(self, models_dir: Path) -> None:
        self.faces, self.objects = Faces(models_dir), Objects(models_dir)

    def __call__(self, photo: bytes) -> Anonymized:
        rgb = load(photo)
        boxes = self.faces.find(rgb) + self.objects.scan(rgb)
        covered = 0.0
        if boxes:
            rgb, covered = blur_boxes(rgb, boxes)
        count = {k: sum(1 for b in boxes if b[0] == k) for k in PAD}
        return Anonymized(to_jpeg(rgb), rgb.shape[1], rgb.shape[0], count["face"], count["person"],
                          count["vehicle"], round(covered, 4))
