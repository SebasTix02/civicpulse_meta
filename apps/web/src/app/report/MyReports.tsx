"use client";

import { useEffect, useState } from "react";
import { TIMELINE, t, type Lang } from "@/lib/i18n";
import { browserClient } from "@/lib/supabase";
import { ReportStatus, timelineStep } from "./ReportStatus";

type Mine = {
  id: string;
  status: string;
  intake_status: string | null;
  cluster_status: string | null;
  raw_text: string | null;
  created_at: string;
};

const SEEN_KEY = "cp_seen_steps"; // report id -> last step this browser showed (UX P6 "changed" dot)
const LIMIT = 20;

function readSeen(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(SEEN_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function writeSeen(seen: Record<string, string>) {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify(seen));
  } catch {
    /* private mode: the dot just does not persist */
  }
}

// The reports sent from this browser. The anonymous session (devices.id = auth.uid()) is kept by
// Supabase in this browser, so my_reports finds them again after the citizen leaves and comes back.
export function MyReports({ lang, exclude }: { lang: Lang; exclude?: string }) {
  const [rows, setRows] = useState<Mine[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [seen, setSeen] = useState<Record<string, string>>(() => (typeof window === "undefined" ? {} : readSeen()));

  useEffect(() => {
    const supabase = browserClient();
    supabase.auth.getSession().then(async ({ data }) => {
      if (!data.session?.user.is_anonymous) return; // no citizen session yet, or an entity account
      const { data: mine } = await supabase
        .from("my_reports")
        .select("id,status,intake_status,cluster_status,raw_text,created_at")
        .eq("is_sandbox", false)
        .order("created_at", { ascending: false })
        .limit(LIMIT);
      const list = (mine as Mine[] | null) ?? [];
      setRows(list);
      // First time a report is listed, remember its step so later changes get the dot.
      const known = readSeen();
      const fresh = list.filter((r) => known[r.id] === undefined);
      if (fresh.length) {
        for (const r of fresh) known[r.id] = timelineStep(r);
        writeSeen(known);
        setSeen(known);
      }
    });
  }, []);

  function toggle(r: Mine) {
    setOpen(open === r.id ? null : r.id);
    const next = { ...seen, [r.id]: timelineStep(r) };
    setSeen(next);
    writeSeen(next);
  }

  const shown = rows.filter((r) => r.id !== exclude);
  if (shown.length === 0) return null;
  return (
    <section>
      <h2>{t(lang, "myReportsTitle")}</h2>
      <p className="muted">{t(lang, "myReportsHint")}</p>
      {shown.map((r) => {
        const step = timelineStep(r);
        const label = r.status === "failed" ? "—" : TIMELINE.find((s) => s.id === step)?.[lang] ?? step;
        const changed = seen[r.id] !== undefined && seen[r.id] !== step;
        return (
          <div key={r.id}>
            <button className="card inbox" aria-expanded={open === r.id} onClick={() => toggle(r)}>
              <span>
                <b>{label}</b>
                {changed ? <span className="pill"> {t(lang, "myReportsChanged")}</span> : null}
              </span>
              <span>{r.raw_text?.trim() ? r.raw_text.slice(0, 90) : t(lang, "myReportsNoText")}</span>
              <span className="muted">
                {new Date(r.created_at).toLocaleString(lang === "es" ? "es-EC" : "en-US", { dateStyle: "medium", timeStyle: "short" })}
              </span>
            </button>
            {open === r.id ? <ReportStatus lang={lang} reportId={r.id} /> : null}
          </div>
        );
      })}
    </section>
  );
}
