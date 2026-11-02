"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import * as maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { CITY_BOUNDS, STYLE, initMaplibre } from "@/app/ClusterMap";
import { areaBounds, inArea, type CityArea } from "@/lib/geo";

export type LatLon = { lat: number; lon: number };
export type Dot = LatLon & { color: string; title?: string };

export function inCity(p: LatLon, area: CityArea | null) {
  if (area) return inArea(p, area);
  const [[w, s], [e, n]] = CITY_BOUNDS;
  return p.lon >= w && p.lon <= e && p.lat >= s && p.lat <= n;
}

const OUTSIDE_COLOR = "#1a1a17";

/** A circle of radius_m around a point, as a polygon (64 sides is smooth at street zoom). */
function circle(c: LatLon, radiusM: number): GeoJSON.Feature<GeoJSON.Polygon> {
  const pts: [number, number][] = [];
  const dLat = radiusM / 111_320;
  const dLon = radiusM / (111_320 * Math.cos((c.lat * Math.PI) / 180));
  for (let i = 0; i <= 64; i++) {
    const a = (i / 64) * 2 * Math.PI;
    pts.push([c.lon + dLon * Math.cos(a), c.lat + dLat * Math.sin(a)]);
  }
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [pts] } };
}

// Sandbox pin (UX flows §3.4): placed by hand, no GPS. Tap the map or drag the pin; it stays inside
// the pilot city, whose boundary is drawn with everything outside it shaded. Optional: fixed dots (the visitor's report, neighbours already placed) and a ring
// (the case radius), for placing simulated neighbours.
export function PinMap({ pin, onPin, label, area, onOutside, dots = [], ring, color = "#b35a00", zoom = 15 }: {
  pin: LatLon; onPin: (p: LatLon) => void; label: string; area: CityArea | null; onOutside?: () => void;
  dots?: Dot[]; ring?: { center: LatLon; radius_m: number }; color?: string; zoom?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const marker = useRef<maplibregl.Marker | null>(null);
  const fixed = useRef<maplibregl.Marker[]>([]);
  const cb = useRef({ onPin, onOutside });
  cb.current = { onPin, onOutside };

  useEffect(() => {
    if (!box.current) return;
    initMaplibre();
    const m = new maplibregl.Map({ container: box.current, style: STYLE, center: [pin.lon, pin.lat], zoom,
      maxBounds: area ? areaBounds(area) : CITY_BOUNDS, attributionControl: { compact: true } });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    const mk = new maplibregl.Marker({ draggable: true, color }).setLngLat([pin.lon, pin.lat]).addTo(m);
    let last: LatLon = pin;
    const place = (p: LatLon) => {
      if (!inCity(p, area)) {
        mk.setLngLat([last.lon, last.lat]);
        cb.current.onOutside?.();
        return;
      }
      last = p;
      mk.setLngLat([p.lon, p.lat]);
      cb.current.onPin(p);
    };
    mk.on("dragend", () => { const p = mk.getLngLat(); place({ lat: p.lat, lon: p.lng }); });
    m.on("click", (e) => place({ lat: e.lngLat.lat, lon: e.lngLat.lng }));
    m.on("load", () => {
      m.resize();
      // Shade the world outside the city and outline the boundary.
      const [[w, so], [e, n]] = area ? areaBounds(area, 1) : [[CITY_BOUNDS[0][0] - 1, CITY_BOUNDS[0][1] - 1], [CITY_BOUNDS[1][0] + 1, CITY_BOUNDS[1][1] + 1]];
      const world: GeoJSON.Position[] = [[w, so], [e, so], [e, n], [w, n], [w, so]];
      const rings: GeoJSON.Position[][] = area
        ? (area.boundary.type === "Polygon" ? [area.boundary.coordinates[0]] : area.boundary.coordinates.map((p) => p[0]))
        : [[[CITY_BOUNDS[0][0], CITY_BOUNDS[0][1]], [CITY_BOUNDS[1][0], CITY_BOUNDS[0][1]], [CITY_BOUNDS[1][0], CITY_BOUNDS[1][1]],
            [CITY_BOUNDS[0][0], CITY_BOUNDS[1][1]], [CITY_BOUNDS[0][0], CITY_BOUNDS[0][1]]]];
      m.addSource("city-mask", { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [world, ...rings] } } });
      m.addLayer({ id: "city-mask", type: "fill", source: "city-mask", paint: { "fill-color": OUTSIDE_COLOR, "fill-opacity": 0.18 } });
      m.addSource("city-line", { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "MultiLineString", coordinates: rings } } });
      m.addLayer({ id: "city-line", type: "line", source: "city-line", paint: { "line-color": OUTSIDE_COLOR, "line-width": 1.5, "line-opacity": 0.6 } });
    });
    map.current = m;
    marker.current = mk;
    return () => { m.remove(); map.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the map is built once; the pin moves itself
  }, []);

  // Fixed dots, redrawn when they change.
  const dotsKey = JSON.stringify(dots);
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    fixed.current.forEach((x) => x.remove());
    fixed.current = dots.map((d) => {
      const el = document.createElement("span");
      el.className = "map-dot";
      el.style.background = d.color;
      if (d.title) el.title = d.title;
      return new maplibregl.Marker({ element: el }).setLngLat([d.lon, d.lat]).addTo(m);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the serialized dots
  }, [dotsKey]);

  // The case radius.
  const ringKey = ring ? `${ring.center.lat},${ring.center.lon},${ring.radius_m}` : "";
  useEffect(() => {
    const m = map.current;
    if (!m || !ring) return;
    const draw = () => {
      const data = circle(ring.center, ring.radius_m);
      const src = m.getSource("ring") as maplibregl.GeoJSONSource | undefined;
      if (src) return src.setData(data);
      m.addSource("ring", { type: "geojson", data });
      m.addLayer({ id: "ring-fill", type: "fill", source: "ring", paint: { "fill-color": "#1f6feb", "fill-opacity": 0.08 } });
      m.addLayer({ id: "ring-line", type: "line", source: "ring", paint: { "line-color": "#1f6feb", "line-width": 2, "line-dasharray": [2, 2] } });
    };
    if (m.isStyleLoaded()) draw(); else m.once("load", draw);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the ring's values
  }, [ringKey]);

  return <div ref={box} className="mini-map pin-map" role="application" aria-label={label} />;
}
