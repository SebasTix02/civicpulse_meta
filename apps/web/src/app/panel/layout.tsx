import type { Metadata } from "next";
import { currentLang } from "@/lib/lang-server";
import { PanelShell } from "../SiteShells";

export const metadata: Metadata = { title: "CivicPulse · Panel", robots: { index: false } };

// The actors' area: inbox, cases, closures. Sign-in by invitation only.
export default async function PanelLayout({ children }: { children: React.ReactNode }) {
  const lang = await currentLang();
  return <PanelShell lang={lang}>{children}</PanelShell>;
}
