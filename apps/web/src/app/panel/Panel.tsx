"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { t, type Key, type Lang } from "@/lib/i18n";
import { photoIssue, resizeToJpeg, type PhotoIssue } from "@/lib/photoQuality";
import { browserClient, signInLinkClient } from "@/lib/supabase";

type Citation = { id: string; kind: "report" | "geo_feature" | "cluster" | "metric"; ref: string };
type BriefVersion = { title: string; body_markdown: string; actions: Brief["actions"] };
type Brief = {
  lang: Lang;
  translations?: Partial<Record<Lang, BriefVersion>>;
  title: string;
  body_markdown: string;
  actions: { action_id: string; rationale: string; citations: string[] }[];
  citations: Citation[];
  model: { provider: string | null; model: string | null };
};
type InboxRow = {
  cluster_id: string;
  stakeholder_id: string;
  stakeholder_name: string;
  is_sandbox: boolean;
  dispatch_status: "sent" | "seen" | "accepted" | "declined" | "closed";
  dispatched_at: string;
  note: string | null;
  brief: Brief;
  priority_score: number;
  latest_resolution: Resolution | null;
  can_submit_resolution: boolean;
  cluster: {
    category_name: Record<Lang, string>;
    lat: number;
    lon: number;
    report_count: number;
    distinct_devices: number;
    status: string;
  };
};
type RegionCheck = {
  region_id: number;
  status: string;
  figure_paths?: { before: string | null; after_aligned: string | null };
};
type Resolution = {
  id: string;
  ai_status: string;
  outcome: "open" | "resolved" | "rejected";
  ai_overridden: boolean;
  created_at: string;
  decided_at: string | null;
  after_public_path: string | null;
  verification: {
    region_checks: RegionCheck[];
    evidence?: "aligned" | "location" | "none";
    location?: { ok: boolean; distance_m: number | null } | null;
  } | null;
  can_review: boolean;
  review_note: string | null;
};
type ReportRow = {
  report_id: string;
  short_ref: string;
  summary_en: string | null;
  raw_text_redacted: string | null;
  severity: number | null;
  intake_status: string | null;
  photo_public_path: string | null;
  created_at: string;
};

const TABS: { key: Key; statuses: InboxRow["dispatch_status"][] }[] = [
  { key: "panelTabNew", statuses: ["sent", "seen"] },
  { key: "panelTabAccepted", statuses: ["accepted"] },
  { key: "panelTabDone", statuses: ["declined", "closed"] },
];

// The brief in the reader's language when its translation exists (same citations, checked by
// the orchestrator), otherwise as written.
function inLang(brief: Brief, lang: Lang): BriefVersion {
  return (brief.lang !== lang && brief.translations?.[lang]) || brief;
}

function fmtDate(lang: Lang, iso: string) {
  return new Date(iso).toLocaleString(lang === "es" ? "es-EC" : "en-US", { dateStyle: "medium", timeStyle: "short" });
}

function photoUrl(path: string | null) {
  if (!path) return null;
  const [bucket, ...rest] = path.split("/");
  return browserClient().storage.from(bucket).getPublicUrl(rest.join("/")).data.publicUrl;
}

// The brief's markdown is plain text plus citations: paragraphs, "- " lists and **bold**. It is
// rendered as React text (never as HTML), with every [id] turned into a chip.
function BriefText({ text, cites, inline: oneLine = false }: { text: string; cites: Map<string, Citation>; inline?: boolean }) {
  const inline = (line: string) =>
    line.split(/(\[[^\]]+\]|\*\*[^*]+\*\*)/g).map((part, i) => {
      if (part.startsWith("**") && part.endsWith("**")) return <b key={i}>{part.slice(2, -2)}</b>;
      if (!part.startsWith("[")) return <Fragment key={i}>{part}</Fragment>;
      const ids = part.slice(1, -1).split(/[,;\s]+/).filter(Boolean);
      return ids.map((id) => {
        const c = cites.get(id);
        return c?.kind === "report" ? (
          <a key={`${i}-${id}`} className="cite" href={`#${id}`}>{id}</a>
        ) : (
          <span key={`${i}-${id}`} className="cite" title={c?.ref}>{id}</span>
        );
      });
    });
  if (oneLine) return <>{inline(text.replace(/\s+/g, " "))}</>;
  const blocks = text.split(/\n\s*\n/);
  return (
    <>
      {blocks.map((block, b) => {
        const lines = block.split("\n").filter((l) => l.trim());
        if (lines.length && lines.every((l) => /^\s*[-*] /.test(l))) {
          return <ul key={b}>{lines.map((l, i) => <li key={i}>{inline(l.replace(/^\s*[-*] /, ""))}</li>)}</ul>;
        }
        return <p key={b}>{inline(lines.join(" "))}</p>;
      })}
    </>
  );
}

function Login({ lang }: { lang: Lang }) {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState("");

  async function send() {
    setState("sending");
    await browserClient().auth.signOut(); // a citizen's anonymous session must not carry over
    const { error: e } = await signInLinkClient().auth.signInWithOtp({
      email: email.trim(),
      // No query string: Supabase only honours a redirect that matches its allow list exactly.
      options: { shouldCreateUser: false, emailRedirectTo: `${window.location.origin}/auth/callback` },
    });
    if (e) {
      setError(/signups not allowed|not found|otp_disabled/i.test(e.message) ? t(lang, "panelNotInvited") : e.message);
      setState("error");
    } else setState("sent");
  }

  return (
    <div className="card">
      <p className="muted">{t(lang, "panelBody")}</p>
      <label>
        {t(lang, "panelEmail")}
        <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <p>
        <button className="primary" disabled={!email.includes("@") || state === "sending"} onClick={send}>
          {t(lang, "panelSendLink")}
        </button>
      </p>
      {state === "sent" ? <p role="status">{t(lang, "panelLinkSent")}</p> : null}
      {state === "error" ? <p role="alert">{error}</p> : null}
    </div>
  );
}

const CANDIDATE = "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW";

// Optional: the fix lets the AI compare photos taken from another angle (location path).
function tryFix(): Promise<{ lat: number; lon: number; accuracy: number } | null> {
  if (!("geolocation" in navigator)) return Promise.resolve(null);
  return new Promise((resolve) =>
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy }),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: 15000 },
    ),
  );
}

// Stakeholder closure (plan §5.4): upload the AFTER photo, see what the AI found, and let a member
// with resolutions.approve decide. The AI never closes a case on its own.
function ResolutionBox({ lang, row, beforeUrl, onChanged }: {
  lang: Lang; row: InboxRow; beforeUrl: string | null; onChanged: () => void;
}) {
  const res = row.latest_resolution;
  const [photo, setPhoto] = useState<File | null>(null);
  const [issue, setIssue] = useState<PhotoIssue | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = res?.outcome === "open" && res.ai_status === "pending";

  // While the closure stage runs (about a minute), refresh the case every 5 s.
  useEffect(() => {
    if (!pending) return;
    const id = setInterval(onChanged, 5000);
    return () => clearInterval(id);
  }, [pending, onChanged]);

  const canSend = row.dispatch_status === "accepted" && row.can_submit_resolution
    && row.cluster.status === "acknowledged" && res?.outcome !== "open";
  if (!res && !canSend) return null;

  async function choose(file: File | null) {
    setPhoto(file);
    setIssue(null);
    if (file) setIssue(await photoIssue(file).catch(() => null));
  }

  async function send() {
    if (!photo) return;
    setBusy(true);
    setError("");
    try {
      const supabase = browserClient();
      const [{ blob, width, height }, fix] = await Promise.all([resizeToJpeg(photo), tryFix()]);
      const { data: n, error: e1 } = await supabase.rpc("issue_resolution_nonce", {
        p_cluster_id: row.cluster_id, p_stakeholder_id: row.stakeholder_id,
      });
      if (e1) throw e1;
      const { error: e2 } = await supabase.storage.from(n.bucket).upload(n.upload_path, blob, { contentType: "image/jpeg" });
      if (e2) throw e2;
      const { error: e3 } = await supabase.rpc("submit_stakeholder_resolution", {
        p_cluster_id: row.cluster_id, p_stakeholder_id: row.stakeholder_id, p_nonce: n.nonce,
        p_photo_path: n.upload_path, p_width: width, p_height: height, p_captured_at: new Date(photo.lastModified).toISOString(),
        p_gps_location: fix ? { lat: fix.lat, lon: fix.lon } : null, p_gps_accuracy_m: fix ? Math.max(1, fix.accuracy) : null,
      });
      if (e3) throw e3;
      setPhoto(null);
      setIssue(null);
      setInputKey((k) => k + 1);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String((e as { message?: string }).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function review(verdict: "approve" | "reject") {
    if (!res) return;
    setBusy(true);
    setError("");
    const { error: e } = await browserClient().rpc("review_resolution", {
      p_resolution_id: res.id, p_verdict: verdict, p_note: note.trim() || null,
    });
    setBusy(false);
    if (e) return setError(e.message);
    setNote("");
    onChanged();
  }

  const override = res ? res.ai_status !== CANDIDATE : false;
  const checks = res?.verification?.region_checks ?? [];
  return (
    <div className="card">
      <h3>{t(lang, "resTitle")}</h3>
      {res ? (
        <>
          <p className="muted small">{fmtDate(lang, res.created_at)}</p>
          <p className={res.ai_status === CANDIDATE ? "notice ok" : "notice"}>
            <b>{t(lang, "resAi")}:</b> {t(lang, `ai_${res.ai_status}` as Key)}
          </p>
          {pending ? <p className="muted" role="status">{t(lang, "resChecking")}</p> : null}
          {res.verification?.evidence === "location" ? (
            <p className="muted small">
              {t(lang, "resByLocation")} ({Math.round(res.verification.location?.distance_m ?? 0)} m)
            </p>
          ) : null}
          <div className="figures">
            {checks.flatMap((c) => [
              c.figure_paths?.before ? (
                <figure key={`${c.region_id}b`}><img src={photoUrl(c.figure_paths.before)!} alt="" />
                  <figcaption>{t(lang, "resBefore")} #{c.region_id}</figcaption></figure>
              ) : null,
              c.figure_paths?.after_aligned ? (
                <figure key={`${c.region_id}a`}><img src={photoUrl(c.figure_paths.after_aligned)!} alt="" />
                  <figcaption>{t(lang, "resAfter")} #{c.region_id}</figcaption></figure>
              ) : null,
            ])}
            {!checks.some((c) => c.figure_paths?.after_aligned) && res.after_public_path ? (
              <figure><img src={photoUrl(res.after_public_path)!} alt="" /><figcaption>{t(lang, "resAfterRaw")}</figcaption></figure>
            ) : null}
          </div>
          <p className="muted small">{t(lang, "resDisclaimer")}</p>
          {res.outcome !== "open" ? (
            <p className="notice">
              {t(lang, `resOutcome_${res.outcome}` as Key)}
              {res.ai_overridden ? ` (${t(lang, "resOverridden")})` : ""}
              {res.review_note ? ` · ${res.review_note}` : ""}
            </p>
          ) : res.can_review ? (
            <div className="notice">
              <label>
                {t(lang, override ? "resNoteOverride" : "resNoteReject")}
                <textarea rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <div className="row">
                <button className="primary" disabled={busy || pending || (override && !note.trim())} onClick={() => review("approve")}>
                  {t(lang, "resApprove")}
                </button>
                <button className="secondary" disabled={busy} onClick={() => review("reject")}>{t(lang, "resReject")}</button>
              </div>
            </div>
          ) : (
            <p className="muted">{t(lang, "resWaitReviewer")}</p>
          )}
        </>
      ) : null}
      {canSend ? (
        <>
          <p className="muted">{t(lang, "resIntro")}</p>
          <p className="muted small">{t(lang, "resGpsHint")}</p>
          {/* Alignment needs the same framing: most failures in the eval were a different angle. */}
          {beforeUrl ? (
            <figure className="guide"><img src={beforeUrl} alt="" /><figcaption>{t(lang, "resFrameLikeThis")}</figcaption></figure>
          ) : null}
          <label>
            {t(lang, "resPhoto")}{" "}
            <input key={inputKey} type="file" accept="image/*" capture="environment"
              onChange={(e) => choose(e.target.files?.[0] ?? null)} />
          </label>
          {photo ? <img className="preview" src={URL.createObjectURL(photo)} alt="" /> : null}
          {issue ? (
            <p className="notice" role="status">
              <b>{t(lang, issue === "dark" ? "photoDark" : issue === "bright" ? "photoBright" : "photoBlurry")}</b>{" "}
              {t(lang, "photoRetakeHint")}
            </p>
          ) : null}
          <p>
            <button className="primary" disabled={!photo || busy} onClick={send}>
              {busy ? t(lang, "resSending") : issue ? t(lang, "photoSendAnyway") : t(lang, "resSend")}
            </button>
          </p>
        </>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
    </div>
  );
}

function Case({ lang, row, onBack, onChanged }: { lang: Lang; row: InboxRow; onBack: () => void; onChanged: () => void }) {
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [original, setOriginal] = useState<Record<string, boolean>>({});
  const [asking, setAsking] = useState<"declined" | "closed" | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const cites = new Map(row.brief.citations.map((c) => [c.id, c]));
  const text = inLang(row.brief, lang);

  useEffect(() => {
    const supabase = browserClient();
    void supabase.from("panel_cluster_reports").select("*").eq("cluster_id", row.cluster_id)
      .order("created_at", { ascending: false }).then(({ data }) => setReports((data as ReportRow[]) ?? []));
    if (row.dispatch_status === "sent") {
      void supabase.rpc("set_dispatch_status", { p_cluster_id: row.cluster_id, p_stakeholder_id: row.stakeholder_id, p_status: "seen" })
        .then(() => onChanged());
    }
  }, [row.cluster_id, row.stakeholder_id, row.dispatch_status, onChanged]);

  async function setStatus(status: "accepted" | "declined" | "closed") {
    setBusy(true);
    setError("");
    const { error: e } = await browserClient().rpc("set_dispatch_status", {
      p_cluster_id: row.cluster_id, p_stakeholder_id: row.stakeholder_id, p_status: status, p_note: note || null,
    });
    setBusy(false);
    if (e) return setError(e.message);
    setAsking(null);
    setNote("");
    onChanged();
  }

  const open = row.dispatch_status === "sent" || row.dispatch_status === "seen";
  return (
    <>
      <p><button className="link" onClick={onBack}>{t(lang, "panelBack")}</button></p>
      <div className="card">
        <p className="muted">
          {row.cluster.category_name[lang]} · {row.cluster.report_count} {t(lang, "panelReports")} ·{" "}
          {row.cluster.distinct_devices} {t(lang, "panelDevices")} · {t(lang, `panelStatus_${row.dispatch_status}` as Key)}
          {row.is_sandbox ? <span className="badge">{t(lang, "panelSandbox")}</span> : null}
        </p>
        <h2>{text.title}</h2>
        <BriefText text={text.body_markdown} cites={cites} />
        {text.actions.length ? (
          <>
            <h3>{t(lang, "panelActions")}</h3>
            <ul>
              {text.actions.map((a) => (
                <li key={a.action_id}>
                  <b>{a.action_id.replace(/_/g, " ")}</b>: <BriefText inline text={`${a.rationale} [${a.citations.join("] [")}]`} cites={cites} />
                </li>
              ))}
            </ul>
          </>
        ) : null}
        <p className="muted small">
          {row.brief.model.model ? `${t(lang, "panelBriefBy")} ${row.brief.model.model}` : t(lang, "panelBriefTemplate")} ·{" "}
          <a href={`https://www.openstreetmap.org/?mlat=${row.cluster.lat}&mlon=${row.cluster.lon}#map=18/${row.cluster.lat}/${row.cluster.lon}`}
            target="_blank" rel="noreferrer">{t(lang, "panelOnMap")}</a>
        </p>
        {row.note ? <p className="notice">{row.note}</p> : null}
        <div className="row">
          {open ? (
            <>
              <button className="primary" disabled={busy} onClick={() => setStatus("accepted")}>{t(lang, "panelAccept")}</button>
              <button className="secondary" disabled={busy} onClick={() => setAsking("declined")}>{t(lang, "panelDecline")}</button>
            </>
          ) : null}
          {row.dispatch_status === "accepted" ? (
            <button className="secondary" disabled={busy} onClick={() => setAsking("closed")}>{t(lang, "panelClose")}</button>
          ) : null}
        </div>
        {asking ? (
          <div className="notice">
            <label>
              {t(lang, "panelNote")}
              <textarea rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
            <button className="primary" disabled={busy || !note.trim()} onClick={() => setStatus(asking)}>
              {t(lang, "panelConfirm")}
            </button>
          </div>
        ) : null}
        {error ? <p role="alert">{error}</p> : null}
      </div>

      <ResolutionBox lang={lang} row={row} onChanged={onChanged}
        beforeUrl={photoUrl((reports.find((r) => r.intake_status === "REPORT_CONFIRMED_BY_PHOTO" && r.photo_public_path)
          ?? reports.find((r) => r.photo_public_path))?.photo_public_path ?? null)} />

      <h3>{t(lang, "panelEvidence")}</h3>
      {reports.map((r) => {
        const url = photoUrl(r.photo_public_path);
        return (
          <div className="card report" id={r.short_ref} key={r.report_id}>
            {url ? <img src={url} alt="" loading="lazy" /> : null}
            <div>
              <p className="muted small"><span className="cite">{r.short_ref}</span> {fmtDate(lang, r.created_at)}
                {r.severity ? ` · ${r.severity}/5` : ""}{r.intake_status === "REPORT_CONFIRMED_BY_PHOTO" ? ` · ${t(lang, "panelPhotoOk")}` : ""}</p>
              <p>{original[r.report_id] ? r.raw_text_redacted : r.summary_en}</p>
              {r.raw_text_redacted ? (
                <button className="link small" onClick={() => setOriginal((o) => ({ ...o, [r.report_id]: !o[r.report_id] }))}>
                  {original[r.report_id] ? t(lang, "panelSummary") : t(lang, "panelOriginal")}
                </button>
              ) : null}
            </div>
          </div>
        );
      })}
    </>
  );
}

export function Panel({ lang }: { lang: Lang }) {
  const [phase, setPhase] = useState<"loading" | "login" | "norole" | "ready">("loading");
  const [rows, setRows] = useState<InboxRow[]>([]);
  const [tab, setTab] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async () => {
    const supabase = browserClient();
    const { data: session } = await supabase.auth.getSession();
    const user = session.session?.user;
    if (!user || user.is_anonymous) return setPhase("login");
    const { data: mine } = await supabase.from("my_stakeholders").select("id");
    if (!mine?.length) return setPhase("norole");
    const { data } = await supabase.from("panel_inbox").select("*").order("priority_score", { ascending: false });
    setRows((data as InboxRow[]) ?? []);
    setPhase("ready");
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function signOut() {
    await browserClient().auth.signOut();
    setSelected(null);
    setPhase("login");
  }

  if (phase === "loading") return <p className="muted">…</p>;
  if (phase === "login") return <Login lang={lang} />;
  if (phase === "norole") {
    return (
      <div className="card">
        <p>{t(lang, "panelNoRole")}</p>
        <button className="secondary" onClick={signOut}>{t(lang, "panelSignOut")}</button>
      </div>
    );
  }

  const current = rows.find((r) => `${r.cluster_id}:${r.stakeholder_id}` === selected);
  if (current) return <Case lang={lang} row={current} onBack={() => setSelected(null)} onChanged={load} />;

  const shown = rows.filter((r) => TABS[tab].statuses.includes(r.dispatch_status));
  return (
    <>
      <div className="row">
        {TABS.map((tb, i) => (
          <button key={tb.key} className={i === tab ? "tab on" : "tab"} onClick={() => setTab(i)}>
            {t(lang, tb.key)} ({rows.filter((r) => tb.statuses.includes(r.dispatch_status)).length})
          </button>
        ))}
        <span style={{ flex: 1 }} />
        <button className="link" onClick={() => void load()}>{t(lang, "panelRefresh")}</button>
        <button className="link" onClick={signOut}>{t(lang, "panelSignOut")}</button>
      </div>
      {shown.length === 0 ? <p className="muted">{t(lang, "panelEmpty")}</p> : null}
      {shown.map((r) => (
        <button key={`${r.cluster_id}:${r.stakeholder_id}`} className="card inbox"
          onClick={() => setSelected(`${r.cluster_id}:${r.stakeholder_id}`)}>
          <span className="muted small">
            {r.cluster.category_name[lang]} · {r.cluster.report_count} {t(lang, "panelReports")} · {fmtDate(lang, r.dispatched_at)}
            {r.dispatch_status === "sent" ? <span className="badge new">{t(lang, "panelStatus_sent")}</span> : null}
            {r.is_sandbox ? <span className="badge">{t(lang, "panelSandbox")}</span> : null}
          </span>
          <b>{inLang(r.brief, lang).title}</b>
          <span className="muted small">{r.stakeholder_name}</span>
        </button>
      ))}
    </>
  );
}
