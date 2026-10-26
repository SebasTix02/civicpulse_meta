import { t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";

export default async function Sandbox() {
  const lang = await currentLang();
  return (
    <>
      <h1>{t(lang, "sandboxTitle")}</h1>
      <p>{t(lang, "sandboxBody")}</p>
      <p className="muted">{t(lang, "comingSoon")}</p>
    </>
  );
}
