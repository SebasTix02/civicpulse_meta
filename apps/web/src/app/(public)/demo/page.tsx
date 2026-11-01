import { t } from "@/lib/i18n";
import { currentLang } from "@/lib/lang-server";
import { Replay } from "./Replay";

// Level 4 of the backup demo (riesgos §4): no Supabase, only the recorded runs shipped in /replay.
export default async function Demo() {
  const lang = await currentLang();
  return (
    <>
      <h1>{t(lang, "replayTitle")}</h1>
      <p className="muted">{t(lang, "replayIntro")}</p>
      <Replay lang={lang} />
    </>
  );
}
