// The pilot city's boundary in the browser (view public_city_boundaries, simplified ~30 m).
// The database checks every report against the full polygon; this only keeps the pin inside.

export type CityArea = { boundary: GeoJSON.Polygon | GeoJSON.MultiPolygon; bbox: [number, number, number, number] };

function inRing(lon: number, lat: number, ring: GeoJSON.Position[]) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Inside the outer ring and outside its holes, for any of the polygons. */
export function inArea(p: { lat: number; lon: number }, area: CityArea) {
  const polys = area.boundary.type === "Polygon" ? [area.boundary.coordinates] : area.boundary.coordinates;
  return polys.some(([outer, ...holes]) => inRing(p.lon, p.lat, outer) && !holes.some((h) => inRing(p.lon, p.lat, h)));
}

/** The box the map may pan in: the boundary plus a small margin. */
export function areaBounds(area: CityArea, pad = 0.02): [[number, number], [number, number]] {
  const [w, s, e, n] = area.bbox;
  return [[w - pad, s - pad], [e + pad, n + pad]];
}
