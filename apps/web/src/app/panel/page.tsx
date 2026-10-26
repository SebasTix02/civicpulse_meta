import { t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";

export default async function Panel() {
  const lang = await currentLang();
  return (
    <>
      <h1>{t(lang, "panelTitle")}</h1>
      <p>{t(lang, "panelBody")}</p>
      <p className="muted">{t(lang, "comingSoon")}</p>
    </>
  );
}
