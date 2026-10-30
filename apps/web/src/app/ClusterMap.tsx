"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import * as maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { TIMELINE, t, type Lang } from "@/lib/i18n";
import type { PublicCluster } from "@/lib/supabase";

// Public map of the validated cases (plan §3.8). Free vector tiles from OpenFreeMap, no key.
// The rows come from the server (public_clusters), so the map needs no second request.
const STYLE = "https://tiles.openfreemap.org/styles/liberty";
const CENTER: [number, number] = [-78.6197, -1.2417]; // Ambato
export const STATUS_COLOR: Record<string, string> = {
  validated: "#d97706",
  acknowledged: "#2563eb",
  resolution_pending: "#7c3aed",
  resolved: "#16a34a",
};

function esc(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export function ClusterMap({ lang, clusters, photoBase }: { lang: Lang; clusters: PublicCluster[]; photoBase: string | null }) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!box.current) return;
    maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs"); // copied by scripts/copy-maplibre-worker.mjs
    const map = new maplibregl.Map({ container: box.current, style: STYLE, center: CENTER, zoom: 13, attributionControl: { compact: true } });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    const data = {
      type: "FeatureCollection" as const,
      features: clusters.map((c) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [c.lon, c.lat] },
        properties: { id: c.id, status: c.status, reports: c.report_count },
      })),
    };
    map.on("load", () => {
      map.addSource("cases", { type: "geojson", data });
      map.addLayer({
        id: "cases",
        type: "circle",
        source: "cases",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["get", "reports"], 1, 7, 10, 14, 30, 20],
          "circle-color": ["match", ["get", "status"],
            "validated", STATUS_COLOR.validated, "acknowledged", STATUS_COLOR.acknowledged,
            "resolution_pending", STATUS_COLOR.resolution_pending, "resolved", STATUS_COLOR.resolved, "#64748b"],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
          "circle-opacity": 0.9,
        },
      });
      if (clusters.length > 1) {
        const b = new maplibregl.LngLatBounds();
        clusters.forEach((c) => b.extend([c.lon, c.lat]));
        map.fitBounds(b, { padding: 50, maxZoom: 16, duration: 0 });
      } else if (clusters.length === 1) {
        map.jumpTo({ center: [clusters[0].lon, clusters[0].lat], zoom: 16 });
      }
    });
    map.on("mouseenter", "cases", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "cases", () => (map.getCanvas().style.cursor = ""));
    map.on("click", "cases", (e) => {
      const id = e.features?.[0]?.properties?.id;
      const c = clusters.find((x) => x.id === id);
      if (!c) return;
      const status = TIMELINE.find((s) => s.id === c.status)?.[lang] ?? c.status;
      const summary = c.public_summary?.[lang] ?? c.public_summary?.en ?? "";
      const photo = c.photo_public_path && photoBase ? `${photoBase}/${c.photo_public_path.replace(/^public\//, "")}` : null;
      new maplibregl.Popup({ maxWidth: "260px" })
        .setLngLat([c.lon, c.lat])
        .setHTML(
          `<b>${esc(c.category_name?.[lang] ?? c.category_id)}</b><br>` +
          `<span style="color:${STATUS_COLOR[c.status] ?? "#64748b"}">● ${esc(status)}</span> · ` +
          `${c.report_count} ${esc(t(lang, "panelReports"))}, ${c.distinct_devices} ${esc(t(lang, "panelDevices"))}` +
          (summary ? `<br>${esc(summary)}` : "") +
          (photo ? `<br><img src="${esc(photo)}" alt="" style="width:100%;margin-top:6px;border-radius:6px">` : ""),
        )
        .addTo(map);
    });
    return () => map.remove();
  }, [clusters, lang, photoBase]);

  return <div ref={box} className="map" role="region" aria-label={t(lang, "mapTitle")} />;
}
