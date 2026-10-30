"use client";

import { useEffect, useMemo, useState } from "react";
import { TIMELINE, intakeMessage, t, type Key, type Lang } from "@/lib/i18n";
import { BriefText, inLang, type Brief } from "../BriefText";

// Plays back a real run recorded by scripts/record_replay.py (riesgos §4.3). Everything comes
// from /replay/*.json and the anonymized photos next to it, so it works with Supabase down
// (level 4). Times between steps are shortened for watching; the log keeps the real ones.

type Answer = { label: string; confidence: number } | null;
type Candidate = { candidate_id: number; prompt: string; sam_score: number; box_xyxy: number[]; classifier?: Answer; crop?: string | null };
type Stage = { stage: string; status: string; provider: string | null; model: string | null; latency_ms: number | null; started_at: string };
type Report = {
  ref: string; created_at: string; device_no: number; simulated: boolean; lang: string | null; status: string;
  severity: number | null; moderation_status: string; moderation_reason: string | null; text: string | null;
  summary_en: string | null; intake_status: string | null; photo: string | null;
  intake: { image_size?: { width: number; height: number }; candidates?: Candidate[] } | null;
  stages: Stage[];
};
type Resolution = {
  closure_path: string; created_at: string; ai_status: string; outcome: string; ai_overridden: boolean;
  decided_at: string | null; after_photo: string | null; stages: Stage[];
  reviews: { verdict: string; note: string | null; at: string }[];
  verification: {
    evidence?: string; location?: { distance_m: number | null } | null;
    region_checks: { region_id: number; status: string; figure_paths?: { before: string | null; after_aligned: string | null } }[];
  } | null;
};
type Scenario = {
  name: string; title: Record<Lang, string>; run_started_at: string; recorded_at: string;
  cluster: { category_id: string; category_name: Record<Lang, string>; is_sandbox: boolean; distinct_devices: number; report_count: number };
  events: { at: string; from: string | null; to: string }[];
  reports: Report[];
  dispatches: { stakeholder_name: string; created_at: string; brief: Brief; brief_stage: { model: string | null; latency_ms: number | null; started_at: string } | null }[];
  resolutions: Resolution[];
};
type IndexEntry = { name: string; title: Record<Lang, string>; run_started_at: string; is_sandbox: boolean };

type Step =
  | { at: number; kind: "report"; r: number }
  | { at: number; kind: "stage"; r: number; s: Stage }
  | { at: number; kind: "cluster"; to: string }
  | { at: number; kind: "brief"; d: number }
  | { at: number; kind: "res_sent"; n: number }
  | { at: number; kind: "res_checked"; n: number; s: Stage | null }
  | { at: number; kind: "res_review"; n: number; verdict: string };

const MIN_GAP_MS = 450;
const MAX_GAP_MS = 1800;
const ms = (iso: string) => new Date(iso).getTime();
const done = (s: { started_at: string; latency_ms: number | null }) => ms(s.started_at) + (s.latency_ms ?? 0);

function steps(sc: Scenario): Step[] {
  const out: Step[] = [];
  sc.reports.forEach((r, i) => {
    out.push({ at: ms(r.created_at), kind: "report", r: i });
    r.stages.filter((s) => s.status === "ok").forEach((s) => out.push({ at: done(s), kind: "stage", r: i, s }));
  });
  sc.events.forEach((e) => out.push({ at: ms(e.at), kind: "cluster", to: e.to }));
  sc.dispatches.forEach((d, i) => out.push({ at: d.brief_stage ? done(d.brief_stage) : ms(d.created_at), kind: "brief", d: i }));
  sc.resolutions.forEach((res, n) => {
    out.push({ at: ms(res.created_at), kind: "res_sent", n });
    const check = res.stages.find((s) => s.stage === "closure" && s.status === "ok") ?? null;
    if (res.ai_status !== "pending") out.push({ at: check ? done(check) : ms(res.created_at) + 1, kind: "res_checked", n, s: check });
    res.reviews.forEach((v) => out.push({ at: ms(v.at), kind: "res_review", n, verdict: v.verdict }));
  });
  return out.sort((a, b) => a.at - b.at);
}

function secs(msValue: number) {
  return msValue < 60_000 ? `${(msValue / 1000).toFixed(1)} s` : `${Math.round(msValue / 60_000)} min`;
}

function fmtDay(lang: Lang, iso: string) {
  return new Date(iso).toLocaleDateString(lang === "es" ? "es-EC" : "en-US", { day: "numeric", month: "long", year: "numeric" });
}

function stageName(lang: Lang, s: Stage) {
  const name = t(lang, `replayStage_${s.stage}` as Key);
  return s.model ? `${name} · ${s.model.replace(/^[^:]+:/, "")}` : name;
}

function PhotoWithBoxes({ src, report }: { src: string; report: Report }) {
  const size = report.intake?.image_size;
  const cands = report.intake?.candidates ?? [];
  return (
    <div className="boxed">
      <img src={src} alt="" />
      {size ? (
        <svg viewBox={`0 0 ${size.width} ${size.height}`} preserveAspectRatio="none" aria-hidden="true">
          {cands.map((c) => {
            const [x1, y1, x2, y2] = c.box_xyxy;
            const ok = c.classifier?.label === "issue_present";
            return <rect key={c.candidate_id} x={x1} y={y1} width={x2 - x1} height={y2 - y1}
              className={ok ? "box ok" : "box"} vectorEffect="non-scaling-stroke" />;
          })}
        </svg>
      ) : null}
    </div>
  );
}

export function Replay({ lang }: { lang: Lang }) {
  const [index, setIndex] = useState<IndexEntry[] | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [sc, setSc] = useState<Scenario | null>(null);
  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  useEffect(() => {
    fetch("/replay/index.json").then((r) => (r.ok ? r.json() : { scenarios: [] }))
      .then((d: { scenarios: IndexEntry[] }) => {
        setIndex(d.scenarios);
        if (d.scenarios.length) setName(d.scenarios[0].name);
      })
      .catch(() => setIndex([]));
  }, []);

  useEffect(() => {
    if (!name) return;
    setSc(null);
    setPos(0);
    setPlaying(false);
    fetch(`/replay/${name}/scenario.json`).then((r) => r.json()).then((d: Scenario) => {
      setSc(d);
      setPlaying(true);
    });
  }, [name]);

  const list = useMemo(() => (sc ? steps(sc) : []), [sc]);

  useEffect(() => {
    if (!playing || pos >= list.length) return;
    const gap = pos === 0 ? MIN_GAP_MS : Math.min(MAX_GAP_MS, Math.max(MIN_GAP_MS, list[pos].at - list[pos - 1].at));
    const id = setTimeout(() => setPos((p) => p + 1), gap / speed);
    return () => clearTimeout(id);
  }, [playing, pos, list, speed]);

  if (index === null) return <p className="muted">…</p>;
  if (!index.length) return <p className="muted">{t(lang, "replayNone")}</p>;

  const base = `/replay/${name}/`;
  const shown = list.slice(0, pos);
  const start = list[0]?.at ?? 0;
  const seen = new Set(shown.filter((s) => s.kind === "report").map((s) => (s as { r: number }).r));
  const stagesOf = (r: number) => shown.filter((s): s is Extract<Step, { kind: "stage" }> => s.kind === "stage" && s.r === r).map((s) => s.s.stage);
  const clusterStatus = [...shown].reverse().find((s): s is Extract<Step, { kind: "cluster" }> => s.kind === "cluster")?.to ?? "candidate";
  const briefs = shown.filter((s): s is Extract<Step, { kind: "brief" }> => s.kind === "brief").map((s) => s.d);
  const resSent = new Set(shown.filter((s) => s.kind === "res_sent").map((s) => (s as { n: number }).n));
  const resChecked = new Set(shown.filter((s) => s.kind === "res_checked").map((s) => (s as { n: number }).n));
  const resReviewed = new Set(shown.filter((s) => s.kind === "res_review").map((s) => (s as { n: number }).n));
  const clusterSteps = TIMELINE.slice(3);
  const clusterAt = clusterSteps.findIndex((s) => s.id === clusterStatus);

  function describe(s: Step): string {
    if (!sc) return "";
    switch (s.kind) {
      case "report": {
        const r = sc.reports[s.r];
        return `${t(lang, "replayLogReport")} ${r.ref} (${t(lang, "replayDevice")} ${r.device_no}${r.simulated ? `, ${t(lang, "replaySimulated")}` : ""})`;
      }
      case "stage":
        return `${sc.reports[s.r].ref}: ${stageName(lang, s.s)}${s.s.latency_ms ? ` (${secs(s.s.latency_ms)})` : ""}`;
      case "cluster":
        return `${t(lang, "replayLogCluster")}: ${TIMELINE.find((x) => x.id === s.to)?.[lang] ?? s.to}`;
      case "brief":
        return `${t(lang, "replayLogBrief")} ${sc.dispatches[s.d].stakeholder_name}`;
      case "res_sent":
        return t(lang, "replayLogResSent");
      case "res_checked":
        return `${t(lang, "replayLogResChecked")}${s.s?.latency_ms ? ` (${secs(s.s.latency_ms)})` : ""}`;
      case "res_review":
        return t(lang, s.verdict === "approve" ? "replayLogApproved" : "replayLogRejected");
    }
  }

  return (
    <>
      {sc ? (
        <p className="banner" role="status">
          {t(lang, "replayBanner").replace("{date}", fmtDay(lang, sc.run_started_at))}
          {sc.cluster.is_sandbox ? ` ${t(lang, "replaySandboxNote")}` : ""}
        </p>
      ) : null}
      <div className="row">
        {index.length > 1 ? (
          <select value={name ?? ""} onChange={(e) => setName(e.target.value)} aria-label={t(lang, "replayScenario")}>
            {index.map((s) => <option key={s.name} value={s.name}>{s.title[lang]}</option>)}
          </select>
        ) : null}
        <button className="primary" disabled={!sc} onClick={() => (pos >= list.length ? (setPos(0), setPlaying(true)) : setPlaying(!playing))}>
          {pos >= list.length ? t(lang, "replayAgain") : playing ? t(lang, "replayPause") : t(lang, "replayPlay")}
        </button>
        <button className="secondary" disabled={!sc || pos >= list.length} onClick={() => { setPlaying(false); setPos((p) => p + 1); }}>
          {t(lang, "replayStep")}
        </button>
        {[1, 2, 4].map((v) => (
          <button key={v} className={v === speed ? "tab on" : "tab"} onClick={() => setSpeed(v)}>×{v}</button>
        ))}
      </div>
      {!sc ? <p className="muted">…</p> : (
        <>
          <h2>{sc.title[lang]}</h2>
          <div className="card">
            <p className="muted small">
              {sc.cluster.category_name[lang]} · {seen.size}/{sc.reports.length} {t(lang, "panelReports")}
            </p>
            <ol className="timeline">
              {clusterSteps.map((s, i) => (
                <li key={s.id} className={i < clusterAt ? "done" : i === clusterAt ? "now" : ""}>{s[lang]}</li>
              ))}
            </ol>
          </div>

          <div className="replay-grid">
            {sc.reports.map((r, i) => {
              if (!seen.has(i)) return null;
              const st = stagesOf(i);
              const checked = st.includes("classify") || st.includes("sam_ground") || (r.simulated && r.intake_status);
              const hidden = r.moderation_status !== "visible";
              return (
                <div className="card" id={r.ref} key={r.ref}>
                  <p className="muted small">
                    <span className="cite">{r.ref}</span> {t(lang, "replayDevice")} {r.device_no}
                    {r.simulated ? <span className="badge">{t(lang, "replaySimulated")}</span> : null}
                  </p>
                  {hidden ? <p className="notice">{t(lang, "replayHidden")}: {r.moderation_reason}</p> : null}
                  {r.photo && checked ? <PhotoWithBoxes src={base + r.photo} report={r} /> : null}
                  {r.photo && !checked ? <p className="muted small">{t(lang, "replayChecking")}</p> : null}
                  {st.includes("spec") || r.simulated ? <p>{lang === "en" || !r.text ? r.summary_en : r.text}</p> : null}
                  {checked && r.intake_status ? <p><b>{intakeMessage(lang, r.intake_status)}</b></p> : null}
                </div>
              );
            })}
          </div>

          {briefs.map((d) => {
            const b = sc.dispatches[d].brief;
            const text = inLang(b, lang);
            return (
              <div className="card" key={`b${d}`}>
                <p className="muted small">{t(lang, "replayBriefFor")} {sc.dispatches[d].stakeholder_name}</p>
                <h3>{text.title}</h3>
                <BriefText text={text.body_markdown} cites={new Map(b.citations.map((c) => [c.id, c]))} />
              </div>
            );
          })}

          {sc.resolutions.map((res, n) => {
            if (!resSent.has(n)) return null;
            const checks = res.verification?.region_checks ?? [];
            return (
              <div className="card" key={`res${n}`}>
                <h3>{t(lang, "resTitle")}</h3>
                <div className="figures">
                  {checks.flatMap((c) => [
                    c.figure_paths?.before ? <figure key={`${c.region_id}b`}><img src={base + c.figure_paths.before} alt="" /><figcaption>{t(lang, "resBefore")}</figcaption></figure> : null,
                    c.figure_paths?.after_aligned ? <figure key={`${c.region_id}a`}><img src={base + c.figure_paths.after_aligned} alt="" /><figcaption>{t(lang, "resAfter")}</figcaption></figure> : null,
                  ])}
                  {!checks.some((c) => c.figure_paths?.after_aligned) && res.after_photo ? (
                    <figure><img src={base + res.after_photo} alt="" /><figcaption>{t(lang, "resAfterRaw")}</figcaption></figure>
                  ) : null}
                </div>
                {resChecked.has(n) ? (
                  <p className={res.ai_status === "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW" ? "notice ok" : "notice"}>
                    <b>{t(lang, "resAi")}:</b> {t(lang, `ai_${res.ai_status}` as Key)}
                  </p>
                ) : <p className="muted">{t(lang, "resChecking")}</p>}
                {resChecked.has(n) && res.verification?.evidence === "location" ? (
                  <p className="muted small">{t(lang, "resByLocation")} ({Math.round(res.verification.location?.distance_m ?? 0)} m)</p>
                ) : null}
                {resReviewed.has(n) ? (
                  <p className="notice">
                    {t(lang, res.outcome === "resolved" ? "resOutcome_resolved" : "resOutcome_rejected")}
                    {res.ai_overridden ? ` (${t(lang, "resOverridden")})` : ""}
                  </p>
                ) : null}
              </div>
            );
          })}

          <h3>{t(lang, "replayLog")}</h3>
          <ol className="replay-log">
            {shown.map((s, i) => (
              <li key={i}><span className="muted small">+{secs(s.at - start)}</span> {describe(s)}</li>
            ))}
          </ol>
        </>
      )}
    </>
  );
}
