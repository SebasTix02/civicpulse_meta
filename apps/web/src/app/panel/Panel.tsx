"use client";

import { useCallback, useEffect, useState } from "react";
import { t, type Key, type Lang } from "@/lib/i18n";
import { photoIssue, resizeToJpeg, type PhotoIssue } from "@/lib/photoQuality";
import { browserClient, signInLinkClient } from "@/lib/supabase";
import { errorMessage } from "@/lib/errors";
import { BriefText, inLang, type Brief } from "../BriefText";
import { StatusPill } from "../HomeMap";
import { Lightbox, PhotoFrame, type Shot } from "../Lightbox";

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

// A2: tabs by dispatch status, plus the closures waiting for a decision.
const TABS: { key: Key; match: (r: InboxRow) => boolean }[] = [
  { key: "panelTabNew", match: (r) => r.dispatch_status === "sent" || r.dispatch_status === "seen" },
  { key: "panelTabAccepted", match: (r) => r.dispatch_status === "accepted" },
  { key: "panelTabReview", match: (r) => r.latest_resolution?.outcome === "open" },
  { key: "panelTabDone", match: (r) => r.dispatch_status === "closed" },
  { key: "panelTabDeclined", match: (r) => r.dispatch_status === "declined" },
];

function fmtDate(lang: Lang, iso: string) {
  return new Date(iso).toLocaleString(lang === "es" ? "es-EC" : "en-US", { dateStyle: "medium", timeStyle: "short" });
}

function photoUrl(path: string | null) {
  if (!path) return null;
  const [bucket, ...rest] = path.split("/");
  return browserClient().storage.from(bucket).getPublicUrl(rest.join("/")).data.publicUrl;
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
      setError(/signups not allowed|not found|otp_disabled/i.test(e.message) ? t(lang, "panelNotInvited") : errorMessage(lang, e));
      setState("error");
    } else setState("sent");
  }

  return (
    <div className="card login">
      <h1>{t(lang, "panelLoginTitle")}</h1>
      <p className="muted small">{t(lang, "panelLoginFor")}</p>
      <div className="field">
        <label htmlFor="panel-email">{t(lang, "panelEmail")}</label>
        <input id="panel-email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && email.includes("@") && send()} />
      </div>
      <button className="primary block" disabled={!email.includes("@") || state === "sending"} onClick={send}>
        {t(lang, "panelSendLink")}
      </button>
      {state === "sent" ? <p className="notice ok" role="status">{t(lang, "panelLinkSent")}</p> : null}
      {state === "error" ? <p className="notice warn" role="alert">{error}</p> : null}
      <p className="muted small" style={{ marginTop: 16 }}>{t(lang, "panelBody")}</p>
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
  const [overriding, setOverriding] = useState(false);
  const [viewing, setViewing] = useState<number | null>(null);
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
      setError(errorMessage(lang, e));
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
    if (e) return setError(errorMessage(lang, e));
    setNote("");
    onChanged();
  }

  const override = res ? res.ai_status !== CANDIDATE : false;
  const checks = res?.verification?.region_checks ?? [];
  const afterUrl = res?.after_public_path ? photoUrl(res.after_public_path) : null;
  const verdict = !res ? null : pending ? "wait" : res.ai_status === CANDIDATE ? "ok" : "warn";
  return (
    <div className={canSend && !res ? "card dashed" : "card"}>
      <h2>{t(lang, res ? "resReviewTitle" : "resTitle")}</h2>
      {res ? (
        <>
          <p className="muted small">{fmtDate(lang, res.created_at)}</p>
          <div className={`verdict ${verdict}`} role="status">
            <b>{pending ? t(lang, "resChecking") : t(lang, res.ai_status === CANDIDATE ? "resAiSeesFixed" : "resAiNotConfirmed")}</b>
            {pending ? null : <span className="small">{t(lang, `ai_${res.ai_status}` as Key)}</span>}
          </div>
          {res.verification?.evidence === "location" ? (
            <p className="muted small">
              {t(lang, "resByLocation")} ({Math.round(res.verification.location?.distance_m ?? 0)} m)
            </p>
          ) : null}
          {beforeUrl || afterUrl ? (
            <div className="pair">
              <figure><figcaption>{t(lang, "resBefore")}</figcaption>
                {beforeUrl ? <PhotoFrame lang={lang} src={beforeUrl} alt="" onOpen={() => setViewing(0)} /> : <span />}</figure>
              <figure><figcaption>{t(lang, "resAfterRaw")}</figcaption>
                {afterUrl ? <PhotoFrame lang={lang} src={afterUrl} alt="" onOpen={() => setViewing(beforeUrl ? 1 : 0)} /> : <span />}</figure>
            </div>
          ) : null}
          {checks.some((c) => c.figure_paths?.before || c.figure_paths?.after_aligned) ? (
            <>
              <h3>{t(lang, "resRegions")}</h3>
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
              </div>
            </>
          ) : null}
          <p className="muted small">{t(lang, "resDisclaimer")}</p>
          {res.outcome !== "open" ? (
            <p className={res.outcome === "resolved" ? "notice ok" : "notice"}>
              {t(lang, `resOutcome_${res.outcome}` as Key)}
              {res.ai_overridden ? ` (${t(lang, "resOverridden")})` : ""}
              {res.review_note ? ` · ${res.review_note}` : ""}
            </p>
          ) : res.can_review ? (
            override ? (
              // UX §4.2: approving against the AI is a secondary link with a required note, not a twin button.
              <>
                <div className="row">
                  <button className="primary" disabled={busy} onClick={() => review("reject")}>{t(lang, "resReject")}</button>
                  {!pending && !overriding ? (
                    <button className="link" onClick={() => setOverriding(true)}>{t(lang, "resApproveAnyway")}</button>
                  ) : null}
                </div>
                {overriding ? (
                  <div className="notice warn override">
                    <label htmlFor="override-note">{t(lang, "resNoteOverride")}</label>
                    <textarea id="override-note" rows={3} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
                    <p className="small" style={{ marginTop: 8 }}>{t(lang, "resOverrideLogged")}</p>
                    <div className="row">
                      <button className="primary" disabled={busy || !note.trim()} onClick={() => review("approve")}>{t(lang, "resApprove")}</button>
                      <button className="secondary" onClick={() => setOverriding(false)}>{t(lang, "cancel")}</button>
                    </div>
                  </div>
                ) : null}
              </>
            ) : (
              <>
                <label htmlFor="review-note">{t(lang, "resNoteReject")}</label>
                <textarea id="review-note" rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
                <div className="row">
                  <button className="primary" disabled={busy || pending} onClick={() => review("approve")}>{t(lang, "resApprove")}</button>
                  <button className="secondary" disabled={busy} onClick={() => review("reject")}>{t(lang, "resReject")}</button>
                </div>
              </>
            )
          ) : (
            <p className="muted">{t(lang, "resWaitReviewer")}</p>
          )}
        </>
      ) : null}
      {canSend ? (
        <div className={res ? "override" : undefined}>
          <p className="muted">{t(lang, "resIntro")}</p>
          {/* Alignment needs the same framing: most failures in the eval were a different angle. */}
          {beforeUrl ? (
            <figure className="guide"><img src={beforeUrl} alt="" /><figcaption>{t(lang, "resFrameLikeThis")}</figcaption></figure>
          ) : null}
          <label className="secondary btn file-btn">
            {t(lang, photo ? "photoRetake" : "resPhoto")}
            <input key={inputKey} type="file" accept="image/*" capture="environment"
              onChange={(e) => choose(e.target.files?.[0] ?? null)} />
          </label>
          <p className="hint">{t(lang, "resGpsHint")}</p>
          {photo ? <img className="preview" src={URL.createObjectURL(photo)} alt="" style={{ maxHeight: 280 }} /> : null}
          {issue ? (
            <p className="notice warn" role="status">
              <b>{t(lang, issue === "dark" ? "photoDark" : issue === "bright" ? "photoBright" : "photoBlurry")}</b>
              {t(lang, "photoRetakeHint")}
            </p>
          ) : null}
          <p style={{ marginTop: 12 }}>
            <button className="dark btn" disabled={!photo || busy} onClick={send}>
              {busy ? t(lang, "resSending") : issue ? t(lang, "photoSendAnyway") : t(lang, "resSend")}
            </button>
          </p>
        </div>
      ) : null}
      {error ? <p className="notice warn" role="alert">{error}</p> : null}
      {viewing !== null ? (
        <Lightbox lang={lang} start={viewing} onClose={() => setViewing(null)} shots={[
          ...(beforeUrl ? [{ src: beforeUrl, caption: t(lang, "resBefore") }] : []),
          ...(afterUrl ? [{ src: afterUrl, caption: t(lang, "resAfterRaw") }] : []),
        ]} />
      ) : null}
    </div>
  );
}

function Case({ lang, row, onBack, onChanged }: { lang: Lang; row: InboxRow; onBack: () => void; onChanged: () => void }) {
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [original, setOriginal] = useState<Record<string, boolean>>({});
  const [viewing, setViewing] = useState<number | null>(null);
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

  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [row.cluster_id]);

  async function setStatus(status: "accepted" | "declined" | "closed") {
    setBusy(true);
    setError("");
    const { error: e } = await browserClient().rpc("set_dispatch_status", {
      p_cluster_id: row.cluster_id, p_stakeholder_id: row.stakeholder_id, p_status: status, p_note: note || null,
    });
    setBusy(false);
    if (e) return setError(errorMessage(lang, e));
    setAsking(null);
    setNote("");
    onChanged();
  }

  const open = row.dispatch_status === "sent" || row.dispatch_status === "seen";
  const best = reports.find((r) => r.intake_status === "REPORT_CONFIRMED_BY_PHOTO" && r.photo_public_path)
    ?? reports.find((r) => r.photo_public_path);
  const beforeUrl = photoUrl(best?.photo_public_path ?? null);
  // Every report photo, the best one first, for the viewer.
  const withPhoto = [...(best ? [best] : []), ...reports.filter((r) => r.photo_public_path && r !== best)];
  const shots: Shot[] = withPhoto.map((r) => ({ src: photoUrl(r.photo_public_path)!, caption: `${r.short_ref} · ${fmtDate(lang, r.created_at)}` }));
  const shotIndex = (r: ReportRow) => withPhoto.indexOf(r);
  const osm = `https://www.openstreetmap.org/?mlat=${row.cluster.lat}&mlon=${row.cluster.lon}#map=18/${row.cluster.lat}/${row.cluster.lon}`;
  return (
    <>
      <p><button className="link" onClick={onBack}>{t(lang, "panelBack")}</button></p>
      <div className="case-top">
        <div className="title">
          {text.title}
          <div className="muted small" style={{ fontWeight: 400 }}>
            {row.cluster.category_name[lang]} · {t(lang, `panelStatus_${row.dispatch_status}` as Key)}
            {row.is_sandbox ? <span className="badge sandbox">{t(lang, "panelSandbox")}</span> : null}
          </div>
        </div>
        <StatusPill lang={lang} status={row.cluster.status} />
        <div className="actions">
          {open ? (
            <>
              <button className="secondary" disabled={busy} onClick={() => setAsking("declined")}>{t(lang, "panelDecline")}</button>
              <button className="primary" disabled={busy} onClick={() => setStatus("accepted")}>{t(lang, "panelAccept")}</button>
            </>
          ) : null}
          {row.dispatch_status === "accepted" ? (
            <button className="secondary" disabled={busy} onClick={() => setAsking("closed")}>{t(lang, "panelClose")}</button>
          ) : null}
        </div>
      </div>
      {asking ? (
        <div className="notice warn">
          <label htmlFor="dispatch-note">{t(lang, asking === "declined" ? "panelNoteDecline" : "panelNote")}</label>
          <textarea id="dispatch-note" rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="row">
            <button className="primary" disabled={busy || !note.trim()} onClick={() => setStatus(asking)}>{t(lang, "panelConfirm")}</button>
            <button className="secondary" onClick={() => setAsking(null)}>{t(lang, "cancel")}</button>
          </div>
        </div>
      ) : null}
      {error ? <p className="notice warn" role="alert">{error}</p> : null}

      <div className="case-grid">
        <section>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between", marginTop: 0 }}>
              <h2 style={{ margin: 0 }}>{t(lang, "panelBrief")}</h2>
              <span className="muted small">
                {row.brief.model.model ? `${t(lang, "panelBriefBy")} ${row.brief.model.model}` : t(lang, "panelBriefTemplate")}
              </span>
            </div>
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
            {row.note ? <p className="notice">{row.note}</p> : null}
          </div>

          <div className="card">
            <h2>{t(lang, "panelEvidence")} ({reports.length})</h2>
            {reports.map((r) => {
              const url = photoUrl(r.photo_public_path);
              return (
                <div className="report-item" id={r.short_ref} key={r.report_id}>
                  {url ? (
                    <button className="thumb-btn" onClick={() => setViewing(shotIndex(r))} aria-label={t(lang, "photoExpand")}>
                      <img src={url} alt="" loading="lazy" />
                    </button>
                  ) : <span className="thumb" />}
                  <div>
                    <p>{original[r.report_id] ? r.raw_text_redacted : r.summary_en}</p>
                    <p className="muted small"><span className="cite">{r.short_ref}</span> {fmtDate(lang, r.created_at)}
                      {r.severity ? ` · ${r.severity}/5` : ""}{r.intake_status === "REPORT_CONFIRMED_BY_PHOTO" ? ` · ${t(lang, "panelPhotoOk")}` : ""}</p>
                  </div>
                  {r.raw_text_redacted ? (
                    <button className="secondary" onClick={() => setOriginal((o) => ({ ...o, [r.report_id]: !o[r.report_id] }))}>
                      {original[r.report_id] ? t(lang, "panelSummary") : t(lang, "panelOriginal")}
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>

          <ResolutionBox lang={lang} row={row} onChanged={onChanged} beforeUrl={beforeUrl} />
          {!row.latest_resolution && !(row.dispatch_status === "accepted" && row.cluster.status === "acknowledged") && open ? (
            <div className="card dashed">
              <h2>{t(lang, "resTitle")} <span className="muted small" style={{ fontWeight: 400 }}>· {t(lang, "resAfterAccept")}</span></h2>
            </div>
          ) : null}
        </section>

        <aside>
          {beforeUrl ? (
            <PhotoFrame lang={lang} className="tall" src={beforeUrl} alt={t(lang, "reportPublicPhoto")} onOpen={() => setViewing(0)} />
          ) : null}
          {shots.length > 1 ? (
            <button className="link small" onClick={() => setViewing(0)}>{t(lang, "casePhotosCount").replace("{n}", String(shots.length))}</button>
          ) : null}
          <div className="card">
            <div className="stats" style={{ margin: 0 }}>
              <div><b>{row.priority_score.toFixed(2)}</b><span>{t(lang, "panelPriority")}</span></div>
              <div><b>{row.cluster.distinct_devices}</b><span>{t(lang, "caseNeighbours")}</span></div>
              <div><b>{row.cluster.report_count}</b><span>{t(lang, "panelReports")}</span></div>
            </div>
            <p className="small" style={{ marginTop: 12 }}>
              {t(lang, "panelReceived")} {fmtDate(lang, row.dispatched_at)}<br />
              <a href={osm} target="_blank" rel="noreferrer">{t(lang, "panelOnMap")} ↗</a>
            </p>
          </div>
        </aside>
      </div>
      {viewing !== null && shots.length ? <Lightbox lang={lang} shots={shots} start={viewing} onClose={() => setViewing(null)} /> : null}
    </>
  );
}

function InboxTable({ lang, rows, onOpen }: { lang: Lang; rows: InboxRow[]; onOpen: (r: InboxRow) => void }) {
  return (
    <>
      <div className="card table-card">
        <table className="inbox">
          <thead>
            <tr>
              <th>{t(lang, "panelPriority")}</th><th>{t(lang, "panelCase")}</th><th>{t(lang, "panelEvidenceCol")}</th>
              <th>{t(lang, "caseNeighbours")}</th><th>{t(lang, "panelReceived")}</th><th>{t(lang, "panelState")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.cluster_id}:${r.stakeholder_id}`} onClick={() => onOpen(r)}>
                <td className="prio">{r.priority_score.toFixed(2)}</td>
                <td>
                  <button className="link title" onClick={(e) => { e.stopPropagation(); onOpen(r); }}>{inLang(r.brief, lang).title}</button>
                  <div className="muted small">
                    {r.cluster.category_name[lang]}
                    {r.is_sandbox ? <span className="badge sandbox">{t(lang, "panelSandbox")}</span> : null}
                  </div>
                </td>
                <td>{r.cluster.report_count} {t(lang, "panelReports")}</td>
                <td>{r.cluster.distinct_devices}</td>
                <td className="muted">{fmtDate(lang, r.dispatched_at)}</td>
                <td>
                  <span className={r.dispatch_status === "sent" ? "pill new" : "pill outline"}>
                    {t(lang, `panelStatus_${r.dispatch_status}` as Key)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="inbox-cards">
        {rows.map((r) => (
          <button key={`${r.cluster_id}:${r.stakeholder_id}`} className="card inbox" onClick={() => onOpen(r)}>
            <span className="row" style={{ margin: 0, justifyContent: "space-between" }}>
              <span className="muted small">{r.cluster.category_name[lang]}</span>
              <span className={r.dispatch_status === "sent" ? "pill new" : "pill outline"}>{t(lang, `panelStatus_${r.dispatch_status}` as Key)}</span>
            </span>
            <b>{inLang(r.brief, lang).title}</b>
            <span className="muted small">
              {t(lang, "panelPriority")} {r.priority_score.toFixed(2)} · {r.cluster.report_count} {t(lang, "panelReports")} ·{" "}
              {r.cluster.distinct_devices} {t(lang, "caseNeighbours")} · {fmtDate(lang, r.dispatched_at)}
              {r.is_sandbox ? <span className="badge sandbox">{t(lang, "panelSandbox")}</span> : null}
            </span>
          </button>
        ))}
      </div>
    </>
  );
}

export function Panel({ lang }: { lang: Lang }) {
  const [phase, setPhase] = useState<"loading" | "login" | "norole" | "ready">("loading");
  const [rows, setRows] = useState<InboxRow[]>([]);
  const [tab, setTab] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [email, setEmail] = useState("");

  const load = useCallback(async () => {
    const supabase = browserClient();
    const { data: session } = await supabase.auth.getSession();
    const user = session.session?.user;
    if (!user || user.is_anonymous) return setPhase("login");
    setEmail(user.email ?? "");
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
      <div className="card login">
        <p className="notice warn">{t(lang, "panelNoRole")}</p>
        <button className="secondary" onClick={signOut}>{t(lang, "panelSignOut")}</button>
      </div>
    );
  }

  const current = rows.find((r) => `${r.cluster_id}:${r.stakeholder_id}` === selected);
  if (current) return <Case lang={lang} row={current} onBack={() => setSelected(null)} onChanged={load} />;

  const shown = rows.filter(TABS[tab].match);
  const actors = [...new Set(rows.map((r) => r.stakeholder_name))];
  return (
    <>
      <div className="panel-bar">
        <h1>{t(lang, "panelInbox")}</h1>
        <span className="muted small">{t(lang, "panelSorted")}</span>
        <span className="spacer" />
        <span className="who">{actors.join(", ")}{email ? ` · ${email}` : ""}</span>
        <button className="link" onClick={() => void load()}>{t(lang, "panelRefresh")}</button>
        <button className="link" onClick={signOut}>{t(lang, "panelSignOut")}</button>
      </div>
      <div className="tabs" role="tablist" aria-label={t(lang, "panelState")}>
        {TABS.map((tb, i) => {
          const n = rows.filter(tb.match).length;
          return (
            <button key={tb.key} role="tab" aria-selected={i === tab} className="tab" onClick={() => setTab(i)}>
              {t(lang, tb.key)}{n ? ` · ${n}` : ""}
            </button>
          );
        })}
      </div>
      {shown.length === 0 ? <p className="card muted">{t(lang, "panelEmpty")}</p> : (
        <InboxTable lang={lang} rows={shown} onOpen={(r) => setSelected(`${r.cluster_id}:${r.stakeholder_id}`)} />
      )}
    </>
  );
}
