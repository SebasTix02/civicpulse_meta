"""Load the official bus stops of the pilot city from OpenStreetMap, for de facto stop detection.

    python scripts/import_osm_stops.py --dry-run     # fetch and count, write nothing
    python scripts/import_osm_stops.py               # upsert into geo_features, refresh distances

Asks the Overpass API for highway=bus_stop and bus platforms inside Ambato's bounding box, then
calls upsert_official_stops (migration 22), which also recomputes, for every transit case, the
distance to the nearest official stop. A validated informal_stop / crowded_stop case more than
200 m from any stop shows on the public map as a de facto stop.

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

OVERPASS = "https://overpass-api.de/api/interpreter"
BBOX = (-1.33, -78.72, -1.17, -78.55)  # south, west, north, east: Ambato and its urban parishes
QUERY = """[out:json][timeout:90];
(
  node["highway"="bus_stop"]({s},{w},{n},{e});
  node["public_transport"="platform"]["bus"="yes"]({s},{w},{n},{e});
);
out body;"""


def fetch(client: httpx.Client) -> list[dict]:
    s, w, n, e = BBOX
    resp = client.post(OVERPASS, data={"data": QUERY.format(s=s, w=w, n=n, e=e)},
                       headers={"User-Agent": "CivicPulse hackathon (github.com/SebasTix02/civicpulse_meta)"})
    resp.raise_for_status()
    rows = []
    for el in resp.json().get("elements", []):
        if el.get("type") == "node" and "lat" in el and "lon" in el:
            tags = el.get("tags") or {}
            rows.append({"ref": f"osm:node/{el['id']}", "name": tags.get("name") or "",
                         "lat": el["lat"], "lon": el["lon"]})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="ambato")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        rows = fetch(client)
    named = sum(1 for r in rows if r["name"])
    print(f"OpenStreetMap: {len(rows)} bus stops in the box ({named} with a name)")
    if args.dry_run or not rows:
        return 0
    load_env()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing in .env", file=sys.stderr)
        return 1
    out = Db(url, key).rpc("upsert_official_stops", {"p_city": args.city, "p_source": "osm", "p_rows": rows})
    print(f"written {out['stops']} stops; distances refreshed for {out['transit_cases']} transit cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
