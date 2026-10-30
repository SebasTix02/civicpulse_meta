"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { TIMELINE, intakeMessage, t, type Key, type Lang } from "@/lib/i18n";
import { browserClient } from "@/lib/supabase";
import { CheckIcon } from "../icons";
import { Lightbox } from "../Lightbox";

type Row = {
  status: string;
  intake_status: string | null;
  cluster_status?: string | null;
  category_id?: string | null;
  created_at?: string;
};

const FINAL = new Set(["clustered", "failed"]);
const POLL_MS = 5000;
const GIVE_UP_MS = 3 * 60 * 1000;
const PUBLIC_CASE = new Set(["validated", "acknowledged", "resolution_pending", "resolved"]);
const NOT_FOUND = "NOT_VERIFIABLE_ISSUE_NOT_FOUND_IN_PHOTO";

/** One step of the 8-step timeline: the report's own status until it joins a cluster, then the cluster's. */
export function timelineStep(r: Row): string {
  if (r.status !== "clustered" || !r.cluster_status) return r.status;
  return TIMELINE.some((s) => s.id === r.cluster_status) ? r.cluster_status : "clustered"; // candidate = on the map
}

export function ago(lang: Lang, iso: string) {
  const min = Math.round((Date.now() - Date.parse(iso)) / 60000);
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  if (min < 60) return rtf.format(-Math.max(min, 0), "minute");
  if (min < 48 * 60) return rtf.format(-Math.round(min / 60), "hour");
  return rtf.format(-Math.round(min / 1440), "day");
}

// C5 / C6: follows one report live. Realtime on reports (own_reports RLS), plus polling in case the
// socket cannot connect on a mobile network; after the report joins a case the timeline follows
// the case (validated, being handled, checking the fix, resolved).
export function ReportStatus({ lang, reportId }: { lang: Lang; reportId: string }) {
  const [row, setRow] = useState<Row>({ status: "received", intake_status: null, cluster_status: null });
  const [category, setCategory] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);
  const [viewing, setViewing] = useState(false);

  useEffect(() => {
    const supabase = browserClient();
    let done = false;
    const apply = (r: Partial<Row> | null) => {
      if (!r?.status) return;
      setRow((old) => ({
        ...old,
        status: r.status!,
        intake_status: r.intake_status ?? null,
        cluster_status: r.cluster_status !== undefined ? r.cluster_status : old.cluster_status,
        category_id: r.category_id ?? old.category_id,
        created_at: r.created_at ?? old.created_at,
      }));
      if (FINAL.has(r.status)) done = true;
    };
    const fetchRow = async () => {
      const { data } = await supabase.from("my_reports")
        .select("status,intake_status,cluster_status,category_id,created_at").eq("id", reportId).maybeSingle();
      apply(data);
    };
    const channel = supabase
      .channel(`report-${reportId}`)
      .on("postgres_changes", { event: "UPDATE", schema: "public", table: "reports", filter: `id=eq.${reportId}` },
        (payload) => {
          apply(payload.new as Row);
          void fetchRow(); // the case's status lives on the cluster, not in this row
        })
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

  useEffect(() => {
    if (!row.category_id) return;
    void browserClient().from("categories").select("display_name").eq("id", row.category_id).maybeSingle()
      .then(({ data }) => setCategory((data?.display_name as Record<Lang, string> | undefined)?.[lang] ?? null));
  }, [row.category_id, lang]);

  const step = timelineStep(row);
  const current = row.status === "failed" ? -1 : TIMELINE.findIndex((s) => s.id === step);
  const checked = row.status === "vision_done" || row.status === "clustered";
  const photoUrl = checked
    ? browserClient().storage.from("public").getPublicUrl(`reports/${reportId}/v1.jpg`).data.publicUrl
    : null;
  const confirmed = row.intake_status === "REPORT_CONFIRMED_BY_PHOTO";

  return (
    <div aria-live="polite">
      <div className="card status-head">
        {photoUrl ? (
          <button className="thumb-btn" onClick={() => setViewing(true)} aria-label={t(lang, "photoExpand")}>
            <img className="thumb" src={photoUrl} alt={t(lang, "reportPublicPhoto")} />
          </button>
        ) : <span className="thumb" aria-hidden="true" />}
        <div>
          <b>{category ?? t(lang, "statusYourReport")}</b>
          {row.created_at ? <div className="muted small">{ago(lang, row.created_at)}</div> : null}
          {checked && row.intake_status ? (
            <div className={confirmed ? "intake-ok" : "intake-no"}>{intakeMessage(lang, row.intake_status)}</div>
          ) : !checked && row.status !== "failed" ? <div className="muted small">{t(lang, "reportReceived")}</div> : null}
        </div>
      </div>

      {checked && row.intake_status === NOT_FOUND ? (
        <div className="notice warn">
          <b>{t(lang, "notFoundTitle")}</b>
          <p>{t(lang, "notFoundBody")}</p>
          <ul className="tips">
            <li>{t(lang, "notFoundTip1")}</li>
            <li>{t(lang, "notFoundTip2")}</li>
          </ul>
        </div>
      ) : null}
      {row.status === "failed" ? <p className="notice warn">{t(lang, "reportCheckFailed")}</p> : null}

      <ol className="card vtimeline" aria-label={t(lang, "statusTimeline")}>
        {TIMELINE.map((s, i) => {
          const state = i < current || (step === "resolved" && i === current) ? "done" : i === current ? "now" : "";
          return (
            <li key={s.id} className={state} aria-current={state === "now" ? "step" : undefined}>
              <span className="rail">
                <span className="mark">{state === "done" ? <CheckIcon /> : null}</span>
                <span className="line" />
              </span>
              <span className="label">
                {s[lang]}
                {state === "now" ? <span className="detail">{t(lang, `stepDetail_${s.id}` as Key)}</span> : null}
              </span>
            </li>
          );
        })}
      </ol>
      {!checked && slow ? <p className="muted small">{t(lang, "reportStillChecking")}</p> : null}
      {viewing && photoUrl ? (
        <Lightbox lang={lang} start={0} onClose={() => setViewing(false)}
          shots={[{ src: photoUrl, caption: t(lang, "reportPublicPhoto") }]} />
      ) : null}
      {PUBLIC_CASE.has(step) ? <p><Link href="/">{t(lang, "statusSeeOnMap")}</Link></p> : null}
    </div>
  );
}
