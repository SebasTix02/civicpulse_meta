"""Load the official bus stops of the pilot city from OpenStreetMap, for de facto stop detection.

    python scripts/import_osm_stops.py --dry-run     # fetch and count, write nothing
    python scripts/import_osm_stops.py               # upsert into geo_features, refresh distances

Asks the Overpass API for highway=bus_stop and bus platforms inside Ambato's bounding box, merges
nodes closer than 15 m (a pole and its platform mapped as two nodes are one stop; the two sides of
a street stay apart), then calls upsert_official_stops (migrations 22 and 23). That replaces the
city's OSM stops with this set and recomputes, for every transit case, the distance to the nearest
stop. A validated informal_stop / crowded_stop case between 200 m and 1 km from the nearest stop
shows on the public map as a de facto stop; farther than 1 km, the area is probably just unmapped.

OSM is a community map, not the transit office's register: in the first import (759 nodes, 195
named) it was dense downtown and sparse in the rural parishes. The box covers the whole canton.

Data © OpenStreetMap contributors, ODbL. Uses SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from .env.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
sys.path.insert(0, str(ROOT / "scripts"))

from civicpulse.db import Db

from smoke_models import load_env

MERGE_M = 15.0
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
                         "lat": el["lat"], "lon": el["lon"], "pole": tags.get("highway") == "bus_stop"})
    return rows


def _meters(a: dict, b: dict) -> float:
    lat = math.radians((a["lat"] + b["lat"]) / 2)
    dy = (a["lat"] - b["lat"]) * 111_320
    dx = (a["lon"] - b["lon"]) * 111_320 * math.cos(lat)
    return math.hypot(dx, dy)


def merge_close(rows: list[dict], within_m: float = MERGE_M) -> list[dict]:
    """One row per physical stop: nodes closer than within_m collapse into the best one (a named
    node first, then the highway=bus_stop pole), keeping a name from any of them."""
    ranked = sorted(rows, key=lambda r: (not r["name"], not r["pole"], r["ref"]))
    kept: list[dict] = []
    cell = within_m / 111_320  # grid in degrees, about within_m on a side
    grid: dict[tuple[int, int], list[dict]] = {}
    for r in ranked:
        gx, gy = int(r["lon"] // cell), int(r["lat"] // cell)
        near = [k for dx in (-1, 0, 1) for dy in (-1, 0, 1) for k in grid.get((gx + dx, gy + dy), [])
                if _meters(k, r) < within_m]
        if near:
            continue
        kept.append(r)
        grid.setdefault((gx, gy), []).append(r)
    return [{"ref": r["ref"], "name": r["name"], "lat": r["lat"], "lon": r["lon"]} for r in kept]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="ambato")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        rows = fetch(client)
    stops = merge_close(rows)
    named = sum(1 for r in stops if r["name"])
    print(f"OpenStreetMap: {len(rows)} nodes in the box -> {len(stops)} stops after merging nodes closer "
          f"than {MERGE_M:.0f} m ({named} with a name)")
    rows = stops
    if args.dry_run or not rows:
        return 0
    load_env()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing in .env", file=sys.stderr)
        return 1
    out = Db(url, key).rpc("upsert_official_stops", {"p_city": args.city, "p_source": "osm", "p_rows": rows})
    print(f"written {out['stops']} stops, removed {out.get('removed', 0)} no longer in OSM; "
          f"distances refreshed for {out['transit_cases']} transit cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
