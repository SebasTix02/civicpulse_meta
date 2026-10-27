"""Quality check of the downloaded pothole pair photos: which ones can be used, alone or as a pair.

    pip install pillow
    python data/pothole_pairs/qc.py

Each photo in data/private/raw/ gets one status:
  ok           a real photo used by only this case
  placeholder  the provider's logo the app stores when no photo was taken: the same file
               shared by many cases, or an image that is almost all black
  reused       the exact same file attached to several different cases (a crew sending one
               photo for many repairs): it cannot prove what happened at THIS place, so it is
               not used as a pair, but it is kept as a real example of photo reuse

Writes:
  data/private/photo_qc.csv      photo, sha256, status, cases sharing it
  data/pothole_pairs/pairs.csv       public index updated with before_status, after_status, usable_as
                                 (pair, before_only, after_only, none)
  data/private/raw.zip           rebuilt without placeholders, ready for the anonymization notebook
"""

from __future__ import annotations

import csv
import hashlib
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "data" / "private"
RAW = PRIVATE / "raw"
PAIRS = ROOT / "data" / "pothole_pairs" / "pairs.csv"

PLACEHOLDER_MIN_CASES = 20  # one file shared by this many cases is the app's stand-in, not a photo
DARK_LEVEL, DARK_FRACTION = 35, 0.75


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mostly_black(path: Path) -> bool:
    with Image.open(path) as im:
        pixels = im.convert("L").resize((64, 64)).tobytes()
    return sum(p < DARK_LEVEL for p in pixels) / len(pixels) >= DARK_FRACTION


def main() -> int:
    photos = sorted(p for p in RAW.iterdir() if p.is_file())
    if not photos:
        print("no photos in data/private/raw; run download.py first", file=sys.stderr)
        return 2

    digest = {p.name: sha256(p) for p in photos}
    cases_by_hash: dict[str, set[str]] = defaultdict(set)
    for name, h in digest.items():
        cases_by_hash[h].add(name.split("_")[1])

    status: dict[str, str] = {}
    for p in photos:
        shared = len(cases_by_hash[digest[p.name]])
        if shared >= PLACEHOLDER_MIN_CASES or mostly_black(p):
            status[p.name] = "placeholder"
        elif shared > 1:
            status[p.name] = "reused"
        else:
            status[p.name] = "ok"

    with (PRIVATE / "photo_qc.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["photo", "sha256", "status", "cases_sharing_file"])
        for p in photos:
            w.writerow([p.name, digest[p.name], status[p.name], len(cases_by_hash[digest[p.name]])])

    # pair_id_side -> status, whatever the file extension
    by_side = {p.stem: status[p.name] for p in photos}
    rows = list(csv.DictReader(PAIRS.open(encoding="utf-8")))
    base_cols = [c for c in rows[0] if c not in ("before_status", "after_status", "usable_as")]
    usable_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        for side in ("before", "after"):
            r[f"{side}_status"] = by_side.get(f"{r['pair_id']}_{side}", "missing")
        b_ok, a_ok = r["before_status"] == "ok", r["after_status"] == "ok"
        r["usable_as"] = "pair" if b_ok and a_ok else "before_only" if b_ok else "after_only" if a_ok else "none"
        usable_counts[r["usable_as"]] += 1
    with PAIRS.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[*base_cols, "before_status", "after_status", "usable_as"])
        w.writeheader()
        w.writerows(rows)

    keep = [p for p in photos if status[p.name] != "placeholder"]
    with zipfile.ZipFile(PRIVATE / "raw.zip", "w", zipfile.ZIP_STORED) as zf:
        for p in keep:
            zf.write(p, p.name)

    counts: dict[str, int] = defaultdict(int)
    for s in status.values():
        counts[s] += 1
    print("photos:", dict(counts))
    print("cases usable as:", dict(usable_counts))
    print(f"raw.zip rebuilt with {len(keep)} photos (placeholders left out)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
