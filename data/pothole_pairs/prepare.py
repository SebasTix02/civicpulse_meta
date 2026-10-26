"""Turn the pothole repair export (Ambato, cases with before/after photos)
into an anonymized pair index for evaluation and seed data.

Used with permission of the public entity that collected them. The raw export holds citizen
names and phone numbers: it never enters git (see .gitignore) and nothing personal leaves
this script.

    python data/pothole_pairs/prepare.py path/to/Admin.xlsx

Writes:
  data/pothole_pairs/pairs.csv                  public: anonymized index, committed
  data/private/pairs_manifest.csv       private: pair_id -> photo URLs, git-ignored

Anonymization:
  - drops citizen name and phone, the source case id and the internal row key
  - pair_id is a sequential id; the link to the source id stays in the private manifest
  - coordinates rounded to 3 decimals (about 110 m) in the public index; exact ones only in
    the private manifest, for the downloader
  - dates kept as calendar days (no time of day)
  - test rows ("Prueba ...", gibberish names) removed
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_OUT = ROOT / "data" / "pothole_pairs" / "pairs.csv"
PRIVATE_OUT = ROOT / "data" / "private" / "pairs_manifest.csv"

STATUS = {"ATENDIDO": "attended", "REASIGNADO": "reassigned", "INGRESADO": "received", "EN PROCESO": "in_progress"}
ZONE = {"URBANA": "urban", "RURAL": "rural"}
TEST_NAME = re.compile(r"prueba|test|^[^aeiou\s]{5,}$", re.I)
IMAGE_EXT = re.compile(r"\.(jpe?g|png|webp)$", re.I)

PUBLIC_COLUMNS = ["pair_id", "status", "has_before", "has_after", "parish", "zone", "institution", "crew",
                  "lat_approx", "lon_approx", "reported_on", "attended_on", "days_to_attend"]


def clean(value) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def day(value) -> str:
    return value.date().isoformat() if isinstance(value, datetime) else ""


def image_url(value) -> str:
    url = clean(value)
    return url if IMAGE_EXT.search(url) else ""  # some rows only carry the folder URL


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("xlsx", type=Path)
    args = parser.parse_args()

    sheet = openpyxl.load_workbook(args.xlsx, read_only=True).active
    rows = sheet.iter_rows(values_only=True)
    header = [clean(h) for h in next(rows)]
    col = {name: i for i, name in enumerate(header)}
    need = ["ID", "Estado", "Nombres y Apellidos del ciudadano", "Parroquia", "Rural / Urbana", "Cuadrilla Responsable",
            "Institución responsable", "Latitud", "Longitud", "Fecha ingreso", "Fecha Atencion",
            "Link Imagen Antes", "Link Imagen Despues"]
    missing = [n for n in need if n not in col]
    if missing:
        print(f"unexpected export format, missing columns: {missing}", file=sys.stderr)
        return 2

    public, private, skipped = [], [], 0
    records = sorted(rows, key=lambda r: (r[col["Fecha ingreso"]] or datetime.min, r[col["ID"]] or 0))
    for r in records:
        if TEST_NAME.search(clean(r[col["Nombres y Apellidos del ciudadano"]])):
            skipped += 1
            continue
        pair_id = f"bach_{len(public) + 1:04d}"
        before, after = image_url(r[col["Link Imagen Antes"]]), image_url(r[col["Link Imagen Despues"]])
        reported, attended = r[col["Fecha ingreso"]], r[col["Fecha Atencion"]]
        lat, lon = float(r[col["Latitud"]]), float(r[col["Longitud"]])
        public.append({
            "pair_id": pair_id,
            "status": STATUS.get(clean(r[col["Estado"]]), "other"),
            "has_before": int(bool(before)),
            "has_after": int(bool(after)),
            "parish": clean(r[col["Parroquia"]]).title(),
            "zone": ZONE.get(clean(r[col["Rural / Urbana"]]), "unknown"),
            "institution": clean(r[col["Institución responsable"]]) or "unknown",
            "crew": clean(r[col["Cuadrilla Responsable"]]).title() or "unknown",
            "lat_approx": round(lat, 3),
            "lon_approx": round(lon, 3),
            "reported_on": day(reported),
            "attended_on": day(attended),
            "days_to_attend": (attended - reported).days if isinstance(attended, datetime) and isinstance(reported, datetime) else "",
        })
        private.append({"pair_id": pair_id, "source_id": r[col["ID"]], "lat": lat, "lon": lon,
                        "before_url": before, "after_url": after})

    PUBLIC_OUT.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_OUT.parent.mkdir(parents=True, exist_ok=True)
    with PUBLIC_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PUBLIC_COLUMNS)
        w.writeheader()
        w.writerows(public)
    with PRIVATE_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(private[0]))
        w.writeheader()
        w.writerows(private)

    both = sum(1 for p in public if p["has_before"] and p["has_after"])
    print(f"{len(public)} cases ({both} with both photos), {skipped} test rows dropped")
    print(f"public index  -> {PUBLIC_OUT.relative_to(ROOT)}")
    print(f"private links -> {PRIVATE_OUT.relative_to(ROOT)} (git-ignored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
