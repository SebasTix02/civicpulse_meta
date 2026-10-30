import { currentLang } from "@/lib/lang-server";
import { Panel } from "./Panel";

export default async function PanelPage() {
  const lang = await currentLang();
  return <Panel lang={lang} />;
}
