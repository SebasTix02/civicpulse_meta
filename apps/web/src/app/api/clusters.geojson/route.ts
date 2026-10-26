// Open feed of validated and resolved clusters (API contract: getClustersGeoJSON).
import { publicServerClient, type PublicCluster } from "@/lib/supabase";

export const revalidate = 60;

export async function GET() {
  const supabase = publicServerClient();
  const headers = {
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "public, s-maxage=60, stale-while-revalidate=300",
    "Content-Type": "application/geo+json; charset=utf-8",
  };
  if (!supabase) {
    return new Response(JSON.stringify({ type: "FeatureCollection", features: [] }), { headers });
  }
  const { data, error } = await supabase.from("public_clusters").select("*").limit(5000);
  if (error) {
    return Response.json({ code: "feed_unavailable" }, { status: 503, headers: { "Access-Control-Allow-Origin": "*" } });
  }
  const features = (data as PublicCluster[]).map(({ lat, lon, ...props }) => ({
    type: "Feature",
    geometry: { type: "Point", coordinates: [lon, lat] },
    properties: props,
  }));
  return new Response(JSON.stringify({ type: "FeatureCollection", features }), { headers });
}
