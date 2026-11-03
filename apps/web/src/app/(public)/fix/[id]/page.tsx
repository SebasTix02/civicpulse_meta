import { currentLang } from "@/lib/lang-server";
import { publicServerClient, type PublicCluster } from "@/lib/supabase";
import { FixFlow } from "./FixFlow";

export const dynamic = "force-dynamic";

// C7: "¿Ya lo arreglaron?" for one case of the public map.
export default async function Fix({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const lang = await currentLang();
  const db = publicServerClient();
  const ok = /^[0-9a-zA-Z-]{1,64}$/.test(id);
  const { data } = db && ok ? await db.from("public_clusters").select("*").eq("id", id).maybeSingle() : { data: null };
  return (
    <div className="narrow">
      <FixFlow lang={lang} kase={(data as PublicCluster | null) ?? null} />
    </div>
  );
}
