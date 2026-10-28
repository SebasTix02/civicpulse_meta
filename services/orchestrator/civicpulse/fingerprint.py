"""Photo fingerprints for reuse detection (plan §5.5: reused photos, one query).

sha256 catches the exact same file; the perceptual hash also catches a re-encoded, resized or
re-photographed copy. Both are taken from the raw photo, before blurring (the blur would make
two copies of one photo differ), and only the hashes are stored.
"""

from __future__ import annotations

import hashlib

import cv2
import numpy as np

from civicpulse.anonymize import load


def sha256(photo: bytes) -> str:
    return hashlib.sha256(photo).hexdigest()


def phash(photo: bytes) -> str:
    """64-bit DCT perceptual hash as 16 hex digits: the 8x8 lowest frequencies of a 32x32
    grayscale copy, one bit per coefficient above their median."""
    gray = cv2.cvtColor(load(photo), cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(small)[:8, :8].flatten()
    bits = low > np.median(low[1:])  # the DC term would dominate the median
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()
