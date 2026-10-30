"use client";

import { useRouter } from "next/navigation";
import { LANGS, type Lang } from "@/lib/i18n";

// ES / EN, always visible (UX flows: conventions). The choice lives in a cookie the server reads.
export function LangSwitch({ lang }: { lang: Lang }) {
  const router = useRouter();
  return (
    <div className="segmented" role="group" aria-label="Idioma / Language">
      {LANGS.map((l) => (
        <button
          key={l}
          aria-pressed={l === lang}
          lang={l}
          onClick={() => {
            if (l === lang) return;
            document.cookie = `lang=${l}; path=/; max-age=31536000; samesite=lax`;
            router.refresh();
          }}
        >
          {l.toUpperCase()}
        </button>
      ))}
    </div>
  );
}
