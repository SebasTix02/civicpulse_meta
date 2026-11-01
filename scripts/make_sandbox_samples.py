"""Add (or update) a sample photo for the judges' sandbox (/sandbox, migration 20).

    pip install -e services/orchestrator
    python scripts/make_sandbox_samples.py --id pothole_pair --category pothole \
        --before data/private/anon/<pair>_before.jpg --after data/private/anon/<pair>_after.jpg \
        --title-es "Bache (antes y después)" --title-en "Pothole (before and after)" \
        --text-es "Bache grande en el carril derecho" --text-en "Large pothole in the right lane" --sort 1
    python scripts/make_sandbox_samples.py --list
    python scripts/make_sandbox_samples.py --id pothole_pair --deactivate

Every photo is anonymized again on this computer (faces, people and close vehicles blurred, the
same detectors as the orchestrator, models in data/private/models/) and rebuilt from pixels, so no
EXIF or GPS metadata survives. Only that copy is uploaded, to the public bucket at
samples/<id>/before.jpg (and after.jpg for a before/after pair). Look at the photos yourself before
running this: they are shown to anyone who opens the sandbox.

A sample with --after is a pair: in the sandbox its AFTER photo can close the case its BEFORE
photo opened. Uses SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from .env (never printed).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
sys.path.insert(0, str(ROOT / "scripts"))

from civicpulse.db import Db
from smoke_models import load_env

MODELS = ROOT / "data" / "private" / "models"
ID = re.compile(r"^[a-z0-9_]{1,40}$")


def anonymized(path: Path) -> bytes:
    from civicpulse.anonymize import Anonymizer

    if not (MODELS / "yunet.onnx").exists():
        raise SystemExit(f"detector models missing in {MODELS}: run data/pothole_pairs/prefilter.py once")
    anon = Anonymizer(MODELS)(path.read_bytes())
    print(f"  {path.name}: {anon.width}x{anon.height}, blurred {anon.faces} faces, {anon.people} people, "
          f"{anon.vehicles} vehicles")
    return anon.jpeg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="lowercase letters, digits and _ (also the folder name)")
    ap.add_argument("--category", help="category id from the catalog, e.g. pothole")
    ap.add_argument("--before", type=Path)
    ap.add_argument("--after", type=Path, help="makes it a before/after pair")
    ap.add_argument("--title-es")
    ap.add_argument("--title-en")
    ap.add_argument("--text-es", default="", help="suggested report text")
    ap.add_argument("--text-en", default="")
    ap.add_argument("--sort", type=int, default=0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--deactivate", action="store_true")
    args = ap.parse_args()

    load_env()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing in .env", file=sys.stderr)
        return 1
    db = Db(url, key)

    if args.list:
        for row in db.select("sandbox_samples", {"select": "id,category_id,title,after_path,sort,active", "order": "sort"}):
            kind = "pair" if row["after_path"] else "photo"
            print(f"{row['id']:<24} {row['category_id'] or '-':<16} {kind:<6} sort {row['sort']:<3} "
                  f"{'active' if row['active'] else 'off'}  {row['title'].get('en', '')}")
        return 0
    if not args.id or not ID.match(args.id):
        ap.error("--id is required: lowercase letters, digits and _")
    if args.deactivate:
        db.update("sandbox_samples", {"active": False}, id=args.id)
        print(f"{args.id}: deactivated (files stay; reports already made keep working)")
        return 0
    if not (args.before and args.category and args.title_es and args.title_en):
        ap.error("--before, --category, --title-es and --title-en are required")
    for p in (args.before, args.after):
        if p and not p.is_file():
            ap.error(f"{p} not found")
    if not db.select("categories", {"select": "id", "id": f"eq.{args.category}"}):
        ap.error(f"unknown category {args.category}")

    base = f"public/samples/{args.id}"
    db.upload(f"{base}/before.jpg", anonymized(args.before))
    after_path = None
    if args.after:
        after_path = f"{base}/after.jpg"
        db.upload(after_path, anonymized(args.after))

    row = {"id": args.id, "category_id": args.category, "title": {"es": args.title_es, "en": args.title_en},
           "suggested_text": {"es": args.text_es, "en": args.text_en}, "before_path": f"{base}/before.jpg",
           "after_path": after_path, "sort": args.sort, "active": True}
    resp = db.client.post(f"{db.base}/sandbox_samples", json=row,
                          headers=db.headers | {"Prefer": "resolution=merge-duplicates,return=minimal"})
    resp.raise_for_status()
    print(f"{args.id}: {'pair' if after_path else 'photo'} ready in the sandbox")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
