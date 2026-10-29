"""Homography registration of the AFTER photo onto the BEFORE photo (plan §5.4 B step 2).

SIFT keypoints + Lowe's ratio test + RANSAC. The result only enables comparing the same region in
both photos; it never certifies a repair. Every quality check that fails says why, and then the
closure is NOT_VERIFIABLE_REGION_NOT_VISIBLE.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MAX_SIDE = 1024          # registration runs on a downscaled copy; H is rescaled to full size
RATIO = 0.75             # Lowe's ratio test
MIN_INLIERS = 25
MIN_INLIER_RATIO = 0.25
MIN_OVERLAP = 0.30       # share of the BEFORE frame covered by the warped AFTER frame
SCALE_RANGE = (0.33, 3.0)  # sqrt(|det|) of the affine part: zoom between the two shots


@dataclass
class Registration:
    ok: bool
    reason: str | None
    H: np.ndarray | None = None       # 3x3, AFTER pixels -> BEFORE pixels
    inliers: int | None = None
    inlier_ratio: float | None = None
    context_overlap_iou: float | None = None

    def as_json(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "inliers": self.inliers,
                "inlier_ratio": None if self.inlier_ratio is None else round(self.inlier_ratio, 3),
                "context_overlap_iou": None if self.context_overlap_iou is None else round(self.context_overlap_iou, 3)}


def _small_gray(rgb: np.ndarray) -> tuple[np.ndarray, float]:
    scale = min(1.0, MAX_SIDE / max(rgb.shape[:2]))
    small = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else rgb
    return cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), scale


def register(before_rgb: np.ndarray, after_rgb: np.ndarray) -> Registration:
    gb, sb = _small_gray(before_rgb)
    ga, sa = _small_gray(after_rgb)
    sift = cv2.SIFT_create(nfeatures=4000)
    kb, db = sift.detectAndCompute(gb, None)
    ka, da = sift.detectAndCompute(ga, None)
    if db is None or da is None or len(kb) < MIN_INLIERS or len(ka) < MIN_INLIERS:
        return Registration(False, "too_few_keypoints")
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(da, db, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < RATIO * n.distance]
    if len(good) < MIN_INLIERS:
        return Registration(False, "too_few_matches", inliers=len(good))
    src = np.float32([ka[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H_small, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if H_small is None:
        return Registration(False, "no_homography")
    inliers = int(mask.sum())
    ratio = inliers / len(good)
    # Back to full resolution: H = S_b^-1 * H_small * S_a
    H = np.diag([1 / sb, 1 / sb, 1]) @ H_small @ np.diag([sa, sa, 1])
    if inliers < MIN_INLIERS or ratio < MIN_INLIER_RATIO:
        return Registration(False, "weak_match", H, inliers, ratio)
    scale = float(np.sqrt(abs(np.linalg.det(H[:2, :2]))))
    if not SCALE_RANGE[0] <= scale <= SCALE_RANGE[1] or abs(H[2, 0]) + abs(H[2, 1]) > 0.002:
        return Registration(False, "implausible_geometry", H, inliers, ratio)
    h, w = before_rgb.shape[:2]
    ah, aw = after_rgb.shape[:2]
    corners = cv2.perspectiveTransform(np.float32([[0, 0], [aw, 0], [aw, ah], [0, ah]]).reshape(-1, 1, 2), H)
    if not cv2.isContourConvex(corners.astype(np.int32)):
        return Registration(False, "folded_warp", H, inliers, ratio)
    frame = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(frame, corners.reshape(-1, 2).astype(np.int32), 1)
    overlap = float(frame.mean())
    if overlap < MIN_OVERLAP:
        return Registration(False, "little_overlap", H, inliers, ratio, overlap)
    return Registration(True, None, H, inliers, ratio, overlap)


def warp_to_before(after: np.ndarray, H: np.ndarray, before_shape: tuple[int, int], nearest: bool = False) -> np.ndarray:
    """An AFTER image or mask in the BEFORE frame (zeros where the AFTER photo does not reach)."""
    h, w = before_shape
    return cv2.warpPerspective(after, H, (w, h), flags=cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0)
