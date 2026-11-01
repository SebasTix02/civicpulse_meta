import Link from "next/link";
import { t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";

// C9 (sandbox): the welcome screen. Sessions, quota and samples arrive with start_sandbox_session.
export default async function Sandbox() {
  const lang = await currentLang();
  return (
    <div className="narrow">
      <p className="band" style={{ borderRadius: 8 }}>{t(lang, "sandboxBand")}</p>
      <h1>{t(lang, "sandboxTitle")}</h1>
      <p>{t(lang, "sandboxBody")}</p>
      <div className="notice">
        <b>{t(lang, "comingSoon")}</b>
        <p>{t(lang, "sandboxMeanwhile")} <Link href="/demo">{t(lang, "navDemo")}</Link></p>
      </div>
    </div>
  );
}
