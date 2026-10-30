"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { t, type Key, type Lang } from "@/lib/i18n";
import { browserClient, supabaseConfigured } from "@/lib/supabase";

// The honest strip of the backup demo (riesgos §4): levels 1 to 3 say what is not live.
// Level 4 is the /demo page itself, which needs no Supabase; if Supabase does not answer here,
// the strip points there too.
export function DemoBanner({ lang }: { lang: Lang }) {
  const [level, setLevel] = useState(0);
  const path = usePathname();

  useEffect(() => {
    if (!supabaseConfigured) return setLevel(3);
    browserClient().rpc("get_demo_status").then(({ data, error }) => setLevel(error ? 3 : (data?.level ?? 0)));
  }, []);

  if (level < 1 || path === "/demo") return null;
  return (
    <p className="banner" role="status">
      {t(lang, `demoLevel_${Math.min(level, 3)}` as Key)}{" "}
      {level >= 2 ? <Link href="/demo">{t(lang, "navDemo")}</Link> : null}
    </p>
  );
}
