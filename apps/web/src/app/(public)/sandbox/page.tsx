import { t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";
import { publicServerClient, supabaseConfigured } from "@/lib/supabase";
import type { CityArea } from "@/lib/geo";
import { SandboxApp, type Sample } from "./SandboxApp";

export const dynamic = "force-dynamic";

// C9 (sandbox): the whole cycle on test data, with sample photos or the visitor's camera.
export default async function Sandbox() {
  const lang = await currentLang();
  const db = publicServerClient();
  const { data } = db
    ? await db.from("sandbox_samples").select("id,category_id,title,suggested_text,before_path,after_path").order("sort")
    : { data: null };
  const { data: city } = db
    ? await db.from("public_city_boundaries").select("boundary,bbox").eq("id", "ambato").maybeSingle()
    : { data: null };
  return (
    <div className="narrow sandbox">
      <p className="band sandbox-band">{t(lang, "sandboxBand")}</p>
      {supabaseConfigured ? (
        <SandboxApp lang={lang} samples={(data ?? []) as Sample[]} area={(city as CityArea | null) ?? null} />
      ) : (
        <p className="notice warn">{t(lang, "sbNotConfigured")}</p>
      )}
    </div>
  );
}
