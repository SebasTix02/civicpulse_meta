import { currentLang } from "@/lib/lang-server";
import { PanelShell } from "../SiteShells";

// Sign-in links land here on their way to the panel.
export default async function AuthLayout({ children }: { children: React.ReactNode }) {
  const lang = await currentLang();
  return <PanelShell lang={lang}>{children}</PanelShell>;
}
