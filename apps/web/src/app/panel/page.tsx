import { t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";
import { Panel } from "./Panel";

export default async function PanelPage() {
  const lang = await currentLang();
  return (
    <>
      <h1>{t(lang, "panelTitle")}</h1>
      <Panel lang={lang} />
    </>
  );
}
