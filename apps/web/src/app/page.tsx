import Link from "next/link";
import { t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";
import { publicServerClient, publicStorageBase, type PublicCluster } from "@/lib/supabase";
import { HomeMap } from "./HomeMap";
import { CameraIcon } from "./icons";

export const revalidate = 60;

// C1: the public map. On phones the map fills the screen and the two actions float at the bottom.
export default async function Home() {
  const lang = await currentLang();
  const supabase = publicServerClient();
  const { data, error } = supabase
    ? await supabase.from("public_clusters").select("*").order("priority_score", { ascending: false }).limit(200)
    : { data: null, error: true };
  const clusters = (data ?? []) as PublicCluster[];

  return (
    <div className="home">
      <div className="home-head">
        <div>
          <h1>{t(lang, "mapTitle")}</h1>
          <p className="muted">{t(lang, "appTagline")}</p>
        </div>
        <div className="home-cta">
          <Link href="/report" className="primary"><CameraIcon /> {t(lang, "navReport")}</Link>
          <Link href="/sandbox" className="secondary">{t(lang, "tryIt")}</Link>
        </div>
      </div>
      {error ? (
        <p className="notice warn after-map">{t(lang, "mapUnavailable")} <Link href="/demo">{t(lang, "navDemo")}</Link></p>
      ) : (
        <HomeMap lang={lang} clusters={clusters} photoBase={publicStorageBase} />
      )}
      <p className="muted small after-map"><a href="/api/clusters.geojson">GeoJSON</a></p>
    </div>
  );
}
