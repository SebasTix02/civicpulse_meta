"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import * as maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { t, type Lang } from "@/lib/i18n";
import type { OfficialStop, PublicCluster } from "@/lib/supabase";

// Public map of the validated cases (plan §3.8, wireframe C1). Free vector tiles from OpenFreeMap,
// no key. The rows come from the server (public_clusters), so the map needs no second request.
export const STYLE = "https://tiles.openfreemap.org/styles/liberty";
export const CENTER: [number, number] = [-78.6197, -1.2417]; // Ambato
// The pilot city's boundary box (supabase/seed/ambato.sql). The database has the last word:
// a report outside cities.boundary is refused with outside_service_area, in the sandbox too.
export const CITY_BOUNDS: [[number, number], [number, number]] = [[-78.80, -1.42], [-78.45, -1.08]];
// One colour per case status, the same in the legend, the pills and the panel (wireframes).
export const STATUS_COLOR: Record<string, string> = {
  validated: "#1F5FBF",
  acknowledged: "#8A4B00",
  resolution_pending: "#6A3FA0",
  resolved: "#1E7A46",
};

// De facto bus stop (migration 22): an orange ring around the case; official stops: small dots.
export const DE_FACTO_COLOR = "#E07B00";
export const STOP_COLOR = "#4A4A45";

export function initMaplibre() {
  maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs"); // copied by scripts/copy-maplibre-worker.mjs
}

type Props = {
  lang: Lang;
  clusters: PublicCluster[];
  stops?: OfficialStop[];
  category: string | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
};

export function ClusterMap({ lang, clusters, stops = [], category, selected, onSelect }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const select = useRef(onSelect);
  select.current = onSelect;

  useEffect(() => {
    if (!box.current) return;
    initMaplibre();
    const map = new maplibregl.Map({ container: box.current, style: STYLE, center: CENTER, zoom: 13, attributionControl: { compact: true } });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: true } }), "top-right");
    const data = {
      type: "FeatureCollection" as const,
      features: clusters.map((c) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [c.lon, c.lat] },
        properties: { id: c.id, status: c.status, reports: c.report_count, category: c.category_id, deFacto: !!c.de_facto_stop },
      })),
    };
    map.on("load", () => {
      map.resize(); // the container may have changed size while the style loaded
      if (stops.length) {
        map.addSource("stops", {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: stops.map((st) => ({ type: "Feature", geometry: { type: "Point", coordinates: [st.lon, st.lat] }, properties: { name: st.name ?? "" } })),
          },
        });
        map.addLayer({
          id: "stops", type: "circle", source: "stops", minzoom: 13,
          paint: { "circle-radius": 4, "circle-color": STOP_COLOR, "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5 },
        });
      }
      map.addSource("cases", { type: "geojson", data });
      map.addLayer({
        id: "cases-defacto", type: "circle", source: "cases", filter: ["==", ["get", "deFacto"], true],
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["get", "reports"], 1, 24, 10, 28, 30, 32],
          "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": DE_FACTO_COLOR, "circle-stroke-width": 4,
        },
      });
      map.addLayer({
        id: "cases",
        type: "circle",
        source: "cases",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["get", "reports"], 1, 16, 10, 20, 30, 24],
          "circle-color": ["match", ["get", "status"],
            "validated", STATUS_COLOR.validated, "acknowledged", STATUS_COLOR.acknowledged,
            "resolution_pending", STATUS_COLOR.resolution_pending, "resolved", STATUS_COLOR.resolved, "#6B6B66"],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 3,
        },
      });
      // The count inside each circle, like the wireframe. Needs the style's glyphs; without them
      // the circles still show.
      map.addLayer({
        id: "cases-count",
        type: "symbol",
        source: "cases",
        layout: { "text-field": ["to-string", ["get", "reports"]], "text-size": 14, "text-font": ["Noto Sans Bold"], "text-allow-overlap": true },
        paint: { "text-color": "#ffffff" },
      });
      if (clusters.length > 1) {
        const b = new maplibregl.LngLatBounds();
        clusters.forEach((c) => b.extend([c.lon, c.lat]));
        map.fitBounds(b, { padding: 60, maxZoom: 16, duration: 0 });
      } else if (clusters.length === 1) {
        map.jumpTo({ center: [clusters[0].lon, clusters[0].lat], zoom: 16 });
      }
    });
    map.on("mouseenter", "cases", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "cases", () => (map.getCanvas().style.cursor = ""));
    map.on("click", (e) => {
      const hit = map.getLayer("cases") ? map.queryRenderedFeatures(e.point, { layers: ["cases"] }) : [];
      select.current((hit[0]?.properties?.id as string | undefined) ?? null);
    });
    return () => {
      mapRef.current = null;
      map.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stops come with the same server render as clusters
  }, [clusters]);

  // Category chips: filter the layers instead of rebuilding the map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      const f: maplibregl.FilterSpecification | null = category ? ["==", ["get", "category"], category] : null;
      for (const id of ["cases", "cases-count"]) if (map.getLayer(id)) map.setFilter(id, f);
      const ring: maplibregl.FilterSpecification = ["==", ["get", "deFacto"], true];
      if (map.getLayer("cases-defacto")) map.setFilter("cases-defacto", f ? ["all", ring, f] : ring);
    };
    if (map.loaded() && map.getLayer("cases")) apply();
    else map.once("idle", apply);
  }, [category, clusters]);

  // Bring the chosen case into view (also when it is picked from the list).
  useEffect(() => {
    const c = clusters.find((x) => x.id === selected);
    if (c && mapRef.current) mapRef.current.easeTo({ center: [c.lon, c.lat], zoom: Math.max(mapRef.current.getZoom(), 15) });
  }, [selected, clusters]);

  return <div ref={box} className="map" role="region" aria-label={t(lang, "mapTitle")} />;
}
