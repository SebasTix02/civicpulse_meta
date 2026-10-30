import { currentLang } from "@/lib/lang-server";
import { ReportForm } from "./ReportForm";

export default async function Report() {
  const lang = await currentLang();
  return (
    <div className="narrow">
      <ReportForm lang={lang} />
    </div>
  );
}
