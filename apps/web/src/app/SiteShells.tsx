import Link from "next/link";
import { t, type Lang } from "@/lib/i18n";
import { DemoBanner } from "./DemoBanner";
import { LangSwitch } from "./LangSwitch";
import { NavLinks } from "./NavLinks";

// Two separate areas (UX flows §1): the public site for neighbours and visitors, and the panel for
// the teams that handle cases. Each has its own header, so nobody wonders what the other one sees.

export function PublicShell({ lang, children }: { lang: Lang; children: React.ReactNode }) {
  return (
    <>
      <header className="top">
        <div className="top-inner">
          <Link href="/" className="brand">CivicPulse <small>· Ambato</small></Link>
          <NavLinks links={[
            { href: "/", label: t(lang, "navMap") },
            { href: "/report", label: t(lang, "navReport") },
            { href: "/sandbox", label: t(lang, "navSandbox") },
            { href: "/demo", label: t(lang, "navDemo") },
          ]} />
          <LangSwitch lang={lang} />
        </div>
      </header>
      <main>
        <DemoBanner lang={lang} />
        {children}
      </main>
      <footer className="site-foot">
        <span>{t(lang, "footActors")}</span> <Link href="/panel">{t(lang, "footActorsLink")} →</Link>
      </footer>
    </>
  );
}

export function PanelShell({ lang, children }: { lang: Lang; children: React.ReactNode }) {
  return (
    <div className="panel-area">
      <header className="top top-panel">
        <div className="top-inner">
          <Link href="/panel" className="brand">CivicPulse <span className="area-tag">{t(lang, "panelArea")}</span></Link>
          <nav aria-label="Panel">
            <Link href="/" target="_blank">{t(lang, "panelPublicSite")} ↗</Link>
          </nav>
          <LangSwitch lang={lang} />
        </div>
      </header>
      <main>{children}</main>
    </div>
  );
}
