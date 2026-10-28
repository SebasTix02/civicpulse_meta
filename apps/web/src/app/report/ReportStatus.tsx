"use client";

import { useEffect, useState } from "react";
import { TIMELINE, intakeMessage, t, type Lang } from "@/lib/i18n";
import { browserClient } from "@/lib/supabase";

type Row = { status: string; intake_status: string | null };

const STEPS = TIMELINE.slice(0, 4); // received -> understood -> photo checked -> on the map (clustered); the rest follows the cluster
const FINAL = new Set(["clustered", "failed"]);
const POLL_MS = 5000;
const GIVE_UP_MS = 3 * 60 * 1000;

// Follows one report live: Realtime on reports (own_reports RLS), plus polling in case the
// socket cannot connect on a mobile network. Stops once the report joined a cluster.
export function ReportStatus({ lang, reportId }: { lang: Lang; reportId: string }) {
  const [row, setRow] = useState<Row>({ status: "received", intake_status: null });
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    const supabase = browserClient();
    let done = false;
    const apply = (r: Partial<Row> | null) => {
      if (!r?.status) return;
      setRow({ status: r.status, intake_status: r.intake_status ?? null });
      if (FINAL.has(r.status)) done = true;
    };
    const fetchRow = async () => {
      const { data } = await supabase.from("my_reports").select("status,intake_status").eq("id", reportId).maybeSingle();
      apply(data);
    };
    const channel = supabase
      .channel(`report-${reportId}`)
      .on("postgres_changes", { event: "UPDATE", schema: "public", table: "reports", filter: `id=eq.${reportId}` },
        (payload) => apply(payload.new as Row))
      .subscribe();
    void fetchRow();
    const started = Date.now();
    const timer = setInterval(() => {
      if (done) return clearInterval(timer);
      if (Date.now() - started > GIVE_UP_MS) {
        setSlow(true);
        return clearInterval(timer);
      }
      void fetchRow();
    }, POLL_MS);
    return () => {
      clearInterval(timer);
      void supabase.removeChannel(channel);
    };
  }, [reportId]);

  const current = row.status === "failed" ? -1 : STEPS.findIndex((s) => s.id === row.status);
  const checked = row.status === "vision_done" || row.status === "clustered";
  const photoUrl = checked
    ? browserClient().storage.from("public").getPublicUrl(`reports/${reportId}/v1.jpg`).data.publicUrl
    : null;

  return (
    <div className="card" aria-live="polite">
      <p>{t(lang, "reportReceived")}</p>
      <ol className="timeline">
        {STEPS.map((s, i) => (
          <li key={s.id} className={i < current || row.status === "clustered" ? "done" : i === current ? "now" : ""}>
            {s[lang]}
          </li>
        ))}
      </ol>
      {row.status === "failed" ? <p>{t(lang, "reportCheckFailed")}</p> : null}
      {checked && row.intake_status ? <p><strong>{intakeMessage(lang, row.intake_status)}</strong></p> : null}
      {!checked && slow ? <p className="muted">{t(lang, "reportStillChecking")}</p> : null}
      {photoUrl ? (
        <>
          <img className="preview" src={photoUrl} alt={t(lang, "reportPublicPhoto")} />
          <p className="muted">{t(lang, "reportPublicPhoto")}</p>
        </>
      ) : null}
    </div>
  );
}
