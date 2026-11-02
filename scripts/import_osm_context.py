"""Load the street context that raises a case's priority, from OpenStreetMap.

    python scripts/import_osm_context.py --dry-run            # fetch and count, write nothing
    python scripts/import_osm_context.py                      # replace the layers, recompute priorities
    python scripts/import_osm_context.py --layers roads,schools

Layers (geo_features, migration 27):
  roads           highway=trunk|primary|secondary|tertiary (and their _link), props.class without _link
  transit_routes  route=bus relations, as one MultiLineString each
  schools         amenity=school|college|kindergarten|university
  hospitals       amenity=hospital|clinic, healthcare=hospital|clinic|centre
  markets         amenity=marketplace

Each layer replaces what OSM had loaded before (replace_geo_layer, in batches; an empty download
leaves the layer as it was). At the end refresh_city_priorities recomputes every case of the city:
the bonuses and their weights are in app_config.priority_rules. Residential streets are not
loaded: "no major road within 25 m" means a local street.

Overpass is sometimes down (502/504): the script tries a second mirror. Run layers one at a time
with --layers if a big query times out.

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

MIRRORS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
HEADERS = {"User-Agent": "CivicPulse hackathon (github.com/SebasTix02/civicpulse_meta)"}
BBOX = (-1.33, -78.72, -1.17, -78.55)  # south, west, north, east: same box as the city boundary seed
BATCH = 200
ROAD_CLASSES = ("trunk", "primary", "secondary", "tertiary")

QUERIES = {
    "roads": 'way["highway"~"^(trunk|primary|secondary|tertiary)(_link)?$"]({bbox}); out geom;',
    "transit_routes": 'relation["route"="bus"]({bbox}); out geom;',
    "schools": 'nwr["amenity"~"^(school|college|kindergarten|university)$"]({bbox}); out center;',
    "hospitals": '(nwr["amenity"~"^(hospital|clinic)$"]({bbox}); nwr["healthcare"~"^(hospital|clinic|centre)$"]({bbox}););'
                 " out center;",
    "markets": 'nwr["amenity"="marketplace"]({bbox}); out center;',
}


def fetch(client: httpx.Client, layer: str) -> list[dict]:
    s, w, n, e = BBOX
    query = f"[out:json][timeout:120];{QUERIES[layer].format(bbox=f'{s},{w},{n},{e}')}"
    last: Exception | None = None
    for url in MIRRORS:
        try:
            resp = client.post(url, data={"data": query})
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except (httpx.HTTPError, ValueError) as exc:
            print(f"  {layer}: {url} failed ({exc}), trying the next mirror", file=sys.stderr)
            last = exc
    raise SystemExit(f"Overpass unavailable for {layer}: {last}")


def _line(geometry: list[dict] | None) -> list[list[float]] | None:
    pts = [[p["lon"], p["lat"]] for p in geometry or [] if "lat" in p and "lon" in p]
    return pts if len(pts) >= 2 else None


def _point(el: dict) -> list[float] | None:
    if "lat" in el and "lon" in el:
        return [el["lon"], el["lat"]]
    c = el.get("center") or {}
    return [c["lon"], c["lat"]] if "lat" in c and "lon" in c else None


def to_rows(layer: str, elements: list[dict]) -> list[dict]:
    """Overpass elements -> replace_geo_layer rows: {ref, name, props, geojson}."""
    rows: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        ref = f"{el.get('type')}/{el.get('id')}"
        if ref in seen:
            continue
        tags = el.get("tags") or {}
        name = tags.get("name") or ""
        if layer == "roads":
            line = _line(el.get("geometry"))
            klass = (tags.get("highway") or "").removesuffix("_link")
            if el.get("type") != "way" or not line or klass not in ROAD_CLASSES:
                continue
            row = {"props": {"class": klass}, "geojson": {"type": "LineString", "coordinates": line}}
        elif layer == "transit_routes":
            lines = [ln for m in el.get("members") or [] if m.get("type") == "way" and (ln := _line(m.get("geometry")))]
            if el.get("type") != "relation" or not lines:
                continue
            row = {"props": {"ref": tags.get("ref") or ""}, "geojson": {"type": "MultiLineString", "coordinates": lines}}
        else:
            pt = _point(el)
            if not pt:
                continue
            kind = tags.get("amenity") or tags.get("healthcare") or ""
            row = {"props": {"kind": kind}, "geojson": {"type": "Point", "coordinates": pt}}
        seen.add(ref)
        rows.append({"ref": ref, "name": name, **row})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="ambato")
    ap.add_argument("--layers", default=",".join(QUERIES), help="comma-separated: " + ", ".join(QUERIES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    layers = [x.strip() for x in args.layers.split(",") if x.strip()]
    unknown = [x for x in layers if x not in QUERIES]
    if unknown:
        print(f"unknown layers: {', '.join(unknown)}", file=sys.stderr)
        return 2

    fetched: dict[str, list[dict]] = {}
    with httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0), headers=HEADERS) as client:
        for layer in layers:
            rows = to_rows(layer, fetch(client, layer))
            named = sum(1 for r in rows if r["name"])
            extra = ""
            if layer == "roads":
                counts = {k: sum(1 for r in rows if r["props"]["class"] == k) for k in ROAD_CLASSES}
                extra = " (" + ", ".join(f"{k} {v}" for k, v in counts.items()) + ")"
            print(f"{layer}: {len(rows)} features, {named} with a name{extra}")
            fetched[layer] = rows
    if args.dry_run:
        return 0

    load_env()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing in .env", file=sys.stderr)
        return 1
    db = Db(url, key)
    for layer, rows in fetched.items():
        if not rows:
            print(f"{layer}: nothing downloaded, layer left as it was")
            continue
        written = removed = 0
        for i in range(0, len(rows), BATCH):
            out = db.rpc("replace_geo_layer", {"p_city": args.city, "p_layer": layer, "p_source": "osm",
                                               "p_rows": rows[i:i + BATCH], "p_first": i == 0})
            written += out["rows"]
            removed += out["removed"]
        print(f"{layer}: wrote {written}, replaced {removed} from the previous import")
    n = db.rpc("refresh_city_priorities", {"p_city": args.city})
    print(f"priorities recomputed for {n} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
