"""Load the pilot city's real boundary (the Ambato canton) from OpenStreetMap.

    python scripts/import_city_boundary.py --dry-run          # list what Nominatim finds, write nothing
    python scripts/import_city_boundary.py                    # load the first match
    python scripts/import_city_boundary.py --osm-id R123456   # load a specific relation

Asks Overpass for the administrative boundaries (relations) around the city centre, keeps the canton
level (admin_level 6 in Ecuador) named like the city, fetches its polygon from Nominatim, then calls
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
OVERPASS = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "CivicPulse hackathon (github.com/SebasTix02/civicpulse_meta)"}
CENTRE = {"lat": -1.2417, "lon": -78.6197}  # Ambato's central park
NAME = "Ambato"
LEVELS = ("6", "7", "8")  # canton first; parish levels only shown in the list, never picked
# Every administrative relation whose area contains the city centre.
AREAS = """[out:json][timeout:60];
is_in({lat},{lon})->.a;
rel(pivot.a)["boundary"="administrative"];
out tags;"""


def candidates(client: httpx.Client, osm_id: str | None) -> list[dict]:
    """Administrative relations around the centre, the canton named like the city first."""
    if osm_id:
        ids = [osm_id]
    else:
        resp = client.post(OVERPASS, data={"data": AREAS.format(**CENTRE)})
        resp.raise_for_status()
        rels = [e for e in resp.json().get("elements", []) if e.get("type") == "relation"]
        for e in rels:
            tags = e.get("tags") or {}
            print(f"  R{e['id']:<10} admin_level {tags.get('admin_level', '?'):<3} {tags.get('name', '')}")
        rels = [e for e in rels if (e.get("tags") or {}).get("admin_level") in LEVELS
                and NAME.lower() in (e.get("tags") or {}).get("name", "").lower()]
        rels.sort(key=lambda e: LEVELS.index(e["tags"]["admin_level"]))
        ids = [f"R{e['id']}" for e in rels[:1] if e["tags"]["admin_level"] == LEVELS[0]]
        if not ids:
            return []
    resp = client.get(f"{NOMINATIM}/lookup", params={"osm_ids": ",".join(ids), "format": "jsonv2", "polygon_geojson": 1})
    resp.raise_for_status()
    return [r for r in resp.json() if (r.get("geojson") or {}).get("type") in ("Polygon", "MultiPolygon")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="ambato")
    ap.add_argument("--osm-id", help="for example R123456 (a relation), from the --dry-run list")
    ap.add_argument("--lat", type=float, default=CENTRE["lat"])
    ap.add_argument("--lon", type=float, default=CENTRE["lon"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    CENTRE.update(lat=args.lat, lon=args.lon)

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), headers=HEADERS) as client:
        rows = candidates(client, args.osm_id)
    if not rows:
        print(f"no admin_level {LEVELS[0]} boundary named {NAME} contains the centre; pick one from the list "
              "above with --osm-id R<id>", file=sys.stderr)
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
