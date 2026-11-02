"""Load the pilot city's real boundary (the Ambato canton) from OpenStreetMap.

    python scripts/import_city_boundary.py --dry-run          # list what Nominatim finds, write nothing
    python scripts/import_city_boundary.py                    # load the first match
    python scripts/import_city_boundary.py --osm-id R123456   # load a specific relation

Asks Nominatim for the administrative boundary of the canton with its polygon, then calls
set_city_boundary (migration 25), which refuses a shape that does not contain the city centre and
says how many real reports would fall outside. From then on every report, real or sandbox, must be
inside it, and the map draws it. Until this runs, the boundary is a box around the urban area.

Data © OpenStreetMap contributors, ODbL. Uses SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from .env.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
sys.path.insert(0, str(ROOT / "scripts"))

from civicpulse.db import Db

from smoke_models import load_env

NOMINATIM = "https://nominatim.openstreetmap.org"
HEADERS = {"User-Agent": "CivicPulse hackathon (github.com/SebasTix02/civicpulse_meta)"}
CENTRE = {"lat": -1.2417, "lon": -78.6197}  # Ambato's central park
QUERY = "Cantón Ambato, Tungurahua, Ecuador"


def candidates(client: httpx.Client, osm_id: str | None) -> list[dict]:
    if osm_id:
        resp = client.get(f"{NOMINATIM}/lookup", params={"osm_ids": osm_id, "format": "jsonv2", "polygon_geojson": 1})
    else:
        resp = client.get(f"{NOMINATIM}/search", params={"q": QUERY, "format": "jsonv2", "polygon_geojson": 1,
                                                          "countrycodes": "ec", "limit": 8})
    resp.raise_for_status()
    rows = resp.json()
    # Administrative areas with a real polygon, the canton level first (Nominatim calls it county).
    rows = [r for r in rows if (r.get("geojson") or {}).get("type") in ("Polygon", "MultiPolygon")]
    return sorted(rows, key=lambda r: (r.get("addresstype") != "county", r.get("category") != "boundary"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="ambato")
    ap.add_argument("--osm-id", help="for example R123456 (a relation), from the --dry-run list")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), headers=HEADERS) as client:
        rows = candidates(client, args.osm_id)
    if not rows:
        print("Nominatim found no polygon for that query", file=sys.stderr)
        return 1
    for r in rows:
        ref = f"{(r.get('osm_type') or '?')[:1].upper()}{r.get('osm_id')}"
        s, n, w, e = (float(x) for x in r.get("boundingbox", [0, 0, 0, 0]))
        print(f"{ref:>12}  {r.get('addresstype', '?'):<14} {r.get('display_name', '')[:70]}")
        print(f"{'':>12}  box lat {s:.3f}..{n:.3f}, lon {w:.3f}..{e:.3f}")
    pick = rows[0]
    print(f"\nusing {pick.get('osm_type')} {pick.get('osm_id')}: {pick.get('display_name')}")
    if args.dry_run:
        return 0
    load_env()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing in .env", file=sys.stderr)
        return 1
    out = Db(url, key).rpc("set_city_boundary", {"p_city": args.city, "p_geojson": pick["geojson"],
                                                  "p_must_contain": CENTRE})
    print(f"boundary set: {out['area_km2']} km², {out['points']} points, box {out['bbox']}; "
          f"real reports now outside: {out['reports_outside']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
