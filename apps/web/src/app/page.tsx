import { t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";
import { publicServerClient, type PublicCluster } from "@/lib/supabase";

export const revalidate = 60;

export default async function Home() {
  const lang = await currentLang();
  const supabase = publicServerClient();
  const { data, error } = supabase
    ? await supabase.from("public_clusters").select("*").order("priority_score", { ascending: false }).limit(50)
    : { data: null, error: true };
  const clusters = (data ?? []) as PublicCluster[];

  return (
    <>
      <p className="muted">{t(lang, "appTagline")}</p>
      <h1>{t(lang, "mapTitle")}</h1>
      {/* Map layer (MapLibre) lands on day 6; the list reads the same public view. */}
      {error ? <p className="muted">{t(lang, "mapUnavailable")}</p> : null}
      {!error && clusters.length === 0 ? <p className="muted">{t(lang, "mapEmpty")}</p> : null}
      {clusters.map((c) => (
        <div className="card" key={c.id}>
          <strong>{c.category_id}</strong> · {c.status} · {c.report_count}
          <div className="muted">{c.public_summary?.[lang] ?? c.public_summary?.en ?? ""}</div>
        </div>
      ))}
      <p className="muted">
        <a href="/api/clusters.geojson">GeoJSON</a>
      </p>
    </>
  );
}
