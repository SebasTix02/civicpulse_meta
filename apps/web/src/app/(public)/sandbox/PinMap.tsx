"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import * as maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { STYLE, initMaplibre } from "@/app/ClusterMap";

export type LatLon = { lat: number; lon: number };

// Sandbox pin (UX flows §3.4): placed by hand, no GPS. Tap the map or drag the pin.
export function PinMap({ pin, onPin, label }: { pin: LatLon; onPin: (p: LatLon) => void; label: string }) {
  const box = useRef<HTMLDivElement>(null);
  const marker = useRef<maplibregl.Marker | null>(null);
  const move = useRef(onPin);
  move.current = onPin;

  useEffect(() => {
    if (!box.current) return;
    initMaplibre();
    const map = new maplibregl.Map({ container: box.current, style: STYLE, center: [pin.lon, pin.lat], zoom: 15,
      attributionControl: { compact: true } });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    const m = new maplibregl.Marker({ draggable: true, color: "#b35a00" }).setLngLat([pin.lon, pin.lat]).addTo(map);
    m.on("dragend", () => {
      const p = m.getLngLat();
      move.current({ lat: p.lat, lon: p.lng });
    });
    map.on("click", (e) => {
      m.setLngLat(e.lngLat);
      move.current({ lat: e.lngLat.lat, lon: e.lngLat.lng });
    });
    map.on("load", () => map.resize());
    marker.current = m;
    return () => map.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the map is built once; the pin moves itself
  }, []);

  return <div ref={box} className="mini-map pin-map" role="application" aria-label={label} />;
}
