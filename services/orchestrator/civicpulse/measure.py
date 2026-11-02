"""Relative size and estimated depth of a confirmed problem (pothole), for the case priority.

Size: SAM 3.1 segments the roadway ("road") in the same photo. Along the rows where the pothole
is widest, the pothole's width is divided by the roadway's width in that same row. Both are at the
same distance from the camera, so the ratio does not depend on how far the phone was. The share
of the visible roadway area is kept too, but perspective skews it, so it only informs. SAM's road
mask leaves the pothole out, so the roadway is the road mask plus the pothole. When the roadway
runs off both sides of the photo in that row, the street is wider than what we see: the share is
a maximum (`clipped`), and it adds no priority.

Depth: one photo cannot measure it. Muse Spark answers a few visual cues on the confirmed crop
(water inside, exposed base layer, steep edges with shadow, only surface wear) and the report
text adds words like "profundo" or "superficial". The result is low / medium / high, never cm.
"""

from __future__ import annotations

import re
import unicodedata

import cv2
import numpy as np

from civicpulse.sam.service import Detection

SURFACE_MIN_SCORE = 0.40
WIDE_ROWS = 0.8  # rows where the pothole is at least 80% of its widest
SIZE_CLASSES = ((0.15, "small"), (0.35, "medium"))  # width share of the roadway; above: large
DEPTH_POINTS = {"base_layer_visible": 2, "steep_edges": 1, "water_inside": 1, "surface_only": -2}
TEXT_DEEP = (r"profund[oa]s?", r"hond[oa]s?", r"enorme", r"crater", r"socavon", r"se (hunde|hundio)",
             r"deep", r"huge", r"sinkhole")
TEXT_SHALLOW = (r"pequen[oa]s?", r"superficial(es)?", r"leve", r"chiquit[oa]", r"small", r"shallow", r"minor")


def full_mask(d: Detection, width: int, height: int) -> np.ndarray:
    """The detection's mask over the whole image (the box when there is no raster)."""
    out = np.zeros((height, width), np.uint8)
    x1, y1, x2, y2 = (int(v) for v in d.box_px)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return out.astype(bool)
    if d.mask_local:
        raster, mw, mh = d.mask_local
        local = np.frombuffer(raster, np.uint8).reshape(mh, mw)
        out[y1:y2, x1:x2] = cv2.resize(local, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST)
    else:
        out[y1:y2, x1:x2] = 1
    return out.astype(bool)


def size_class(width_share: float) -> str:
    for limit, name in SIZE_CLASSES:
        if width_share < limit:
            return name
    return "large"


def relative_size(target: Detection, surfaces: list[Detection], width: int, height: int) -> dict:
    """Pothole width / roadway width in the same rows, and the share of the visible roadway."""
    hole = full_mask(target, width, height)
    road = np.zeros_like(hole)
    for s in surfaces:
        if s.score >= SURFACE_MIN_SCORE:
            road |= full_mask(s, width, height)
    if not road.any() or not hole.any():
        return {"status": "no_road" if not road.any() else "no_mask"}
    road |= hole
    rows = np.flatnonzero(hole.any(axis=1))
    hole_w = np.array([np.ptp(np.flatnonzero(hole[r])) + 1 for r in rows])
    wide = rows[hole_w >= WIDE_ROWS * hole_w.max()]
    shares, clipped = [], 0
    for r in wide:
        cols = np.flatnonzero(road[r])
        hcols = np.flatnonzero(hole[r])
        shares.append((np.ptp(hcols) + 1) / (np.ptp(cols) + 1))
        clipped += int(cols[0] == 0 and cols[-1] == width - 1)
    share = float(np.median(shares))
    return {"status": "ok", "width_share": round(share, 3),
            "area_share": round(float(hole.sum() / road.sum()), 3),
            "clipped": clipped > len(wide) / 2, "size_class": size_class(share)}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


_DEEP = re.compile(r"\b(?:" + "|".join(TEXT_DEEP) + r")\b")
_SHALLOW = re.compile(r"\b(?:" + "|".join(TEXT_SHALLOW) + r")\b")


def text_depth(text: str) -> int:
    """+1 when the text says deep, -1 when it says shallow, 0 otherwise (or both)."""
    folded = _fold(text)
    return int(bool(_DEEP.search(folded))) - int(bool(_SHALLOW.search(folded)))


def estimate_depth(cues: dict | None, text: str) -> dict:
    """low / medium / high from the visual cues and the text; unknown when neither says anything."""
    t = text_depth(text)
    seen = [k for k in DEPTH_POINTS if cues and cues.get(k)]
    if cues is None and t == 0:
        return {"level": None, "cues": [], "text": 0}
    points = sum(DEPTH_POINTS[k] for k in seen) + t
    level = "low" if points <= 0 else "medium" if points <= 2 else "high"
    return {"level": level, "cues": seen, "text": t}
