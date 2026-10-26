"use client";

import { useRouter } from "next/navigation";
import type { Lang } from "@/lib/i18n";

export function LangSwitch({ lang }: { lang: Lang }) {
  const router = useRouter();
  const other: Lang = lang === "es" ? "en" : "es";
  return (
    <button
      className="lang"
      onClick={() => {
        document.cookie = `lang=${other}; path=/; max-age=31536000; samesite=lax`;
        router.refresh();
      }}
      aria-label={other === "en" ? "Switch to English" : "Cambiar a español"}
    >
      {other.toUpperCase()}
    </button>
  );
}
