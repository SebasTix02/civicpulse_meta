import type { Metadata, Viewport } from "next";
import Link from "next/link";
import { t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";
import { AuthForward } from "./AuthForward";
import { DemoBanner } from "./DemoBanner";
import { LangSwitch } from "./LangSwitch";
import { NavLinks } from "./NavLinks";
import "./globals.css";

export const metadata: Metadata = {
  title: "CivicPulse",
  description: "Citizen reports verified with Meta's SAM 3.1 and Muse Spark.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = { themeColor: "#ffffff", width: "device-width", initialScale: 1 };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const lang = await currentLang();
  return (
    <html lang={lang}>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet" />
      </head>
      <body>
        <AuthForward />
        <header className="top">
          <div className="top-inner">
            <Link href="/" className="brand">CivicPulse <small>· Ambato</small></Link>
            <NavLinks links={[
              { href: "/", label: t(lang, "navMap") },
              { href: "/report", label: t(lang, "navReport") },
              { href: "/sandbox", label: t(lang, "navSandbox") },
              { href: "/demo", label: t(lang, "navDemo") },
              { href: "/panel", label: t(lang, "navPanel") },
            ]} />
            <LangSwitch lang={lang} />
          </div>
        </header>
        <main>
          <DemoBanner lang={lang} />
          {children}
        </main>
      </body>
    </html>
  );
}
