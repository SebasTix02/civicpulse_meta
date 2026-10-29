"""Registration sweep (free, local CPU, no API): which alignment settings line up the most real
before/after pairs without lining up the wrong ones?

    python scripts/eval_registration.py                 # all configs, repaired + not_repaired + not_comparable
    python scripts/eval_registration.py --configs current,relaxed

Reads only data/private/anon/ and prints numbers; per-pair rows go to data/private/reg_sweep_<config>.csv.
`ok` on a not_comparable pair is suspicious (different spot or angle); ncc is the gray-level
correlation of the aligned photos where both exist (1.0 = the same photo).
"""

import argparse
import csv
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ANON = ROOT / "data/private/anon"
LABELS = {
    r["pair_id"]: r["label"]
    for r in csv.DictReader(
        (ROOT / "data/pothole_pairs/closure_labels_v2.csv").open(encoding="utf-8")
    )
}
pairs = [
    p
    for p, l in LABELS.items()
    if l in ("repaired", "not_repaired", "not_comparable")
    and (ANON / f"{p}_before.jpg").exists()
    and (ANON / f"{p}_after.jpg").exists()
]


def gray(path, side, clahe):
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    s = min(1.0, side / max(img.shape))
    if s < 1:
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    if clahe:
        img = cv2.createCLAHE(2.0, (8, 8)).apply(img)
    return img


def feats(img, n, root):
    k, d = cv2.SIFT_create(nfeatures=n).detectAndCompute(img, None)
    if d is not None and root:
        d = d / (d.sum(axis=1, keepdims=True) + 1e-7)
        d = np.sqrt(d).astype(np.float32)
    return k, d


CONFIGS = {
    "current": {
        "side": 1024,
        "n": 4000,
        "ratio": 0.75,
        "method": "ransac",
        "thr": 5.0,
        "min_in": 25,
        "min_ratio": 0.25,
        "clahe": False,
        "root": False,
    },
    "bigger": {
        "side": 1600,
        "n": 8000,
        "ratio": 0.75,
        "method": "ransac",
        "thr": 5.0,
        "min_in": 25,
        "min_ratio": 0.25,
        "clahe": False,
        "root": False,
    },
    "relaxed": {
        "side": 1600,
        "n": 8000,
        "ratio": 0.80,
        "method": "magsac",
        "thr": 4.0,
        "min_in": 15,
        "min_ratio": 0.15,
        "clahe": False,
        "root": False,
    },
    "clahe_root": {
        "side": 1600,
        "n": 8000,
        "ratio": 0.80,
        "method": "magsac",
        "thr": 4.0,
        "min_in": 15,
        "min_ratio": 0.15,
        "clahe": True,
        "root": True,
    },
    "affine": {
        "side": 1600,
        "n": 8000,
        "ratio": 0.80,
        "method": "affine",
        "thr": 4.0,
        "min_in": 15,
        "min_ratio": 0.15,
        "clahe": True,
        "root": True,
    },
}


def run(p, c):
    b, a = (
        gray(ANON / f"{p}_before.jpg", c["side"], c["clahe"]),
        gray(ANON / f"{p}_after.jpg", c["side"], c["clahe"]),
    )
    kb, db = feats(b, c["n"], c["root"])
    ka, da = feats(a, c["n"], c["root"])
    if db is None or da is None or len(kb) < 10 or len(ka) < 10:
        return "few_kp", 0, 0, None
    good = [
        m
        for m, n in (
            x for x in cv2.BFMatcher(cv2.NORM_L2).knnMatch(da, db, k=2) if len(x) == 2
        )
        if m.distance < c["ratio"] * n.distance
    ]
    if len(good) < c["min_in"]:
        return "few_matches", len(good), 0, None
    src = np.float32([ka[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    if c["method"] == "affine":
        M, mask = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=c["thr"]
        )
        H = None if M is None else np.vstack([M, [0, 0, 1]])
    else:
        H, mask = cv2.findHomography(
            src,
            dst,
            cv2.USAC_MAGSAC if c["method"] == "magsac" else cv2.RANSAC,
            c["thr"],
        )
    if H is None:
        return "no_H", len(good), 0, None
    inl = int(mask.sum())
    ratio = inl / len(good)
    if inl < c["min_in"] or ratio < c["min_ratio"]:
        return "weak", len(good), inl, None
    sc = float(np.sqrt(abs(np.linalg.det(H[:2, :2]))))
    if not 0.33 <= sc <= 3 or abs(H[2, 0]) + abs(H[2, 1]) > 0.002:
        return "geometry", len(good), inl, None
    h, w = b.shape
    ah, aw = a.shape
    cor = cv2.perspectiveTransform(
        np.float32([[0, 0], [aw, 0], [aw, ah], [0, ah]]).reshape(-1, 1, 2), H
    )
    if not cv2.isContourConvex(cor.astype(np.int32)):
        return "folded", len(good), inl, None
    fr = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(fr, cor.reshape(-1, 2).astype(np.int32), 1)
    if fr.mean() < 0.3:
        return "overlap", len(good), inl, None
    # context similarity: NCC of the warped AFTER vs BEFORE where both exist
    wa = cv2.warpPerspective(a, H, (w, h)).astype(np.float32)
    m = fr.astype(bool)
    x, y = b.astype(np.float32)[m], wa[m]
    ncc = float(((x - x.mean()) * (y - y.mean())).mean() / (x.std() * y.std() + 1e-6))
    return "ok", len(good), inl, ncc


def job(args):
    p, name = args
    return (p, LABELS[p], *run(p, CONFIGS[name]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    with Pool(args.workers) as pool:
        for name in args.configs.split(","):
            t = time.time()
            rows = pool.map(job, [(p, name) for p in pairs])
            print(f"\n== {name} ({time.time() - t:.0f}s)")
            for lab in ("repaired", "not_repaired", "not_comparable"):
                rs = [r for r in rows if r[1] == lab]
                ok = [r for r in rs if r[2] == "ok"]
                nccs = sorted(round(r[5], 2) for r in ok)
                print(
                    f"  {lab:<15} {len(ok):>3}/{len(rs)} ok  {dict(Counter(r[2] for r in rs))}  ncc={nccs}"
                )
            with open(
                ROOT / f"data/private/reg_sweep_{name}.csv", "w", newline=""
            ) as f:
                csv.writer(f).writerows(
                    [("pair", "label", "result", "matches", "inliers", "ncc"), *rows]
                )


if __name__ == "__main__":
    main()
