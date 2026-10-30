import { TIMELINE, t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";
import { publicServerClient, publicStorageBase, type PublicCluster } from "@/lib/supabase";
import { ClusterMap, STATUS_COLOR } from "./ClusterMap";

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
      {error ? <p className="muted">{t(lang, "mapUnavailable")}</p> : <ClusterMap lang={lang} clusters={clusters} photoBase={publicStorageBase} />}
      <p className="legend">
        {Object.entries(STATUS_COLOR).map(([status, color]) => (
          <span key={status}><i style={{ background: color }} /> {TIMELINE.find((s) => s.id === status)?.[lang]}</span>
        ))}
      </p>
      {!error && clusters.length === 0 ? <p className="muted">{t(lang, "mapEmpty")}</p> : null}
      {clusters.map((c) => (
        <div className="card" key={c.id}>
          <strong>{c.category_name?.[lang] ?? c.category_id}</strong> ·{" "}
          <span style={{ color: STATUS_COLOR[c.status] }}>{TIMELINE.find((s) => s.id === c.status)?.[lang] ?? c.status}</span> ·{" "}
          {c.report_count} {t(lang, "panelReports")}
          <div className="muted">{c.public_summary?.[lang] ?? c.public_summary?.en ?? ""}</div>
        </div>
      ))}
      <p className="muted">
        <a href="/api/clusters.geojson">GeoJSON</a>
      </p>
    </>
  );
}
