import { t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";
import { ReportForm } from "./ReportForm";

export default async function Report() {
  const lang = await currentLang();
  return (
    <>
      <h1>{t(lang, "reportTitle")}</h1>
      <ReportForm lang={lang} />
    </>
  );
}
