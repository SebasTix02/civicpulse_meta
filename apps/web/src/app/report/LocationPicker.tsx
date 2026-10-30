"use client";

import * as maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { STYLE, initMaplibre } from "../ClusterMap";

export type LatLon = { lat: number; lon: number };
export const PIN_MAX_M = 15; // submit_report refuses a pin farther than this from the GPS fix (pin_too_far)

const R = 6_371_000;
const rad = (d: number) => (d * Math.PI) / 180;

export function distanceM(a: LatLon, b: LatLon) {
  const x = rad(b.lon - a.lon) * Math.cos(rad((a.lat + b.lat) / 2));
  const y = rad(b.lat - a.lat);
  return Math.hypot(x, y) * R;
}

// Keeps the pin inside the allowed circle around the fix (a little inside, for rounding).
function clamp(fix: LatLon, p: LatLon): LatLon {
  const d = distanceM(fix, p);
  const max = PIN_MAX_M - 0.5;
  if (d <= max) return p;
  const k = max / d;
  return { lat: fix.lat + (p.lat - fix.lat) * k, lon: fix.lon + (p.lon - fix.lon) * k };
}

function circle(c: LatLon, radiusM: number) {
  const ring: [number, number][] = [];
  for (let i = 0; i <= 48; i++) {
    const a = (i / 48) * 2 * Math.PI;
    ring.push([c.lon + ((radiusM * Math.cos(a)) / (R * Math.cos(rad(c.lat)))) * (180 / Math.PI), c.lat + ((radiusM * Math.sin(a)) / R) * (180 / Math.PI)]);
  }
  return { type: "Feature" as const, properties: {}, geometry: { type: "Polygon" as const, coordinates: [ring] } };
}

// C4 "Ubicación": a small map with the GPS accuracy circle and a pin the citizen can drag up to 15 m.
export function LocationPicker({ fix, accuracy, pin, onPin, label }: {
  fix: LatLon; accuracy: number; pin: LatLon; onPin: (p: LatLon) => void; label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const marker = useRef<maplibregl.Marker | null>(null);
  const fixRef = useRef(fix);
  fixRef.current = fix;
  const onPinRef = useRef(onPin);
  onPinRef.current = onPin;

  useEffect(() => {
    if (!box.current) return;
    initMaplibre();
    const m = new maplibregl.Map({ container: box.current, style: STYLE, center: [pin.lon, pin.lat], zoom: 17, attributionControl: false });
    map.current = m;
    const el = document.createElement("div");
    el.className = "pin";
    marker.current = new maplibregl.Marker({ element: el, draggable: true }).setLngLat([pin.lon, pin.lat]).addTo(m);
    marker.current.on("dragend", () => {
      const ll = marker.current!.getLngLat();
      const p = clamp(fixRef.current, { lat: ll.lat, lon: ll.lng });
      marker.current!.setLngLat([p.lon, p.lat]);
      onPinRef.current(p);
    });
    m.on("load", () => {
      m.addSource("acc", { type: "geojson", data: circle(fixRef.current, accuracy) });
      m.addLayer({ id: "acc", type: "fill", source: "acc", paint: { "fill-color": "#1F5FBF", "fill-opacity": 0.15 } });
      m.addLayer({ id: "acc-line", type: "line", source: "acc", paint: { "line-color": "#1F5FBF", "line-width": 1 } });
    });
    return () => {
      map.current = null;
      m.remove();
    };
    // The map is built once; later fixes move it in the effect below.
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    (m.getSource("acc") as maplibregl.GeoJSONSource | undefined)?.setData(circle(fix, accuracy));
    marker.current?.setLngLat([pin.lon, pin.lat]);
    m.easeTo({ center: [pin.lon, pin.lat] });
  }, [fix, accuracy, pin]);

  return <div ref={box} className="mini-map" role="application" aria-label={label} />;
}
