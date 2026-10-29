import type { Metadata, Viewport } from "next";
import Link from "next/link";
import { t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";
import { AuthForward } from "./AuthForward";
import { LangSwitch } from "./LangSwitch";
import "./globals.css";

export const metadata: Metadata = {
  title: "CivicPulse",
  description: "Citizen reports verified with Meta's SAM 3.1 and Muse Spark.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = { themeColor: "#0f172a", width: "device-width", initialScale: 1 };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const lang = await currentLang();
  return (
    <html lang={lang}>
      <body>
        <AuthForward />
        <header className="top">
          <Link href="/" className="brand">CivicPulse</Link>
          <nav>
            <Link href="/report">{t(lang, "navReport")}</Link>
            <Link href="/sandbox">{t(lang, "navSandbox")}</Link>
            <Link href="/panel">{t(lang, "navPanel")}</Link>
          </nav>
          <LangSwitch lang={lang} />
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
