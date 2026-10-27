"""Download the pothole pair photos listed in the private manifest and pack them for anonymization.

Run on your own computer (the source server refuses cloud IPs):

    pip install httpx
    python data/pothole_pairs/download.py [--limit 50]

Writes, all under git-ignored data/private/:
  raw/<pair_id>_before.<ext>, raw/<pair_id>_after.<ext>   originals, never uploaded to any API
  raw.zip                                                  the same files, to upload to your own
                                                           Google Drive for the anonymization notebook

Next step: data/pothole_pairs/anonymize_sam31_colab.ipynb (SAM 3.1 open weights on a free Colab GPU).
Only its output (data/private/anon/) is ever sent to the Meta Model API.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import zipfile
from pathlib import Path

import httpx
from banned import banned_stems

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "data" / "private"
MANIFEST = PRIVATE / "pairs_manifest.csv"
RAW = PRIVATE / "raw"


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
        time.sleep(2**attempt)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="only the first N pairs (0 = all)")
    args = parser.parse_args()
    if not MANIFEST.exists():
        print("run data/pothole_pairs/prepare.py first", file=sys.stderr)
        return 2
    RAW.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(MANIFEST.open(encoding="utf-8")))
    if args.limit:
        rows = rows[: args.limit]

    banned = banned_stems()
    ok, failed = [], []
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for i, row in enumerate(rows, 1):
            for side in ("before", "after"):
                url = row[f"{side}_url"]
                if not url:
                    continue
                dest = RAW / f"{row['pair_id']}_{side}.{url.rsplit('.', 1)[-1].lower()}"
                if dest.stem in banned:
                    continue
                (ok if download(client, url, dest) else failed).append(dest.name)
            if i % 50 == 0:
                print(f"{i}/{len(rows)} pairs")

    with zipfile.ZipFile(PRIVATE / "raw.zip", "w", zipfile.ZIP_STORED) as zf:
        for name in sorted(ok):
            zf.write(RAW / name, name)
    print(f"{len(ok)} photos downloaded, {len(failed)} failed")
    if failed:
        (PRIVATE / "download_failed.txt").write_text("\n".join(failed), encoding="utf-8")
        print("failed list -> data/private/download_failed.txt")
    print("next: upload data/private/raw.zip to your Google Drive and run anonymize_sam31_colab.ipynb")
    return 0


if __name__ == "__main__":
    sys.exit(main())
