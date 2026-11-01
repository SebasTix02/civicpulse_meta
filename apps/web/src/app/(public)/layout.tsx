import { currentLang } from "@/lib/lang-server";
import { PublicShell } from "../SiteShells";

// The public site: map, report, sandbox, recorded demo. No account; actors have their own area.
export default async function PublicLayout({ children }: { children: React.ReactNode }) {
  const lang = await currentLang();
  return <PublicShell lang={lang}>{children}</PublicShell>;
}
