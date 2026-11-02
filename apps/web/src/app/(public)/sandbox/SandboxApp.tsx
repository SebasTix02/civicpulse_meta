"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/errors";
import { TIMELINE, intakeMessage, t, type Key, type Lang } from "@/lib/i18n";
import { resizeToJpeg } from "@/lib/photoQuality";
import { publicStorageBase, sandboxClient } from "@/lib/supabase";
import { BriefText, inLang, type Brief } from "@/app/BriefText";
import { CENTER } from "@/app/ClusterMap";
import { BackIcon } from "@/app/icons";
import { VoiceButton } from "@/app/VoiceButton";
import { Lightbox, PhotoFrame, type Shot } from "@/app/Lightbox";
import { Timeline, ago, timelineStep } from "../report/ReportStatus";
import { PinMap, type LatLon } from "./PinMap";

export type Sample = {
  id: string;
  category_id: string | null;
  title: Partial<Record<Lang, string>>;
  suggested_text: Partial<Record<Lang, string>>;
  before_path: string;
  after_path: string | null;
};
type Quota = {
  camera_reports_left: number;
  camera_closures_left: number;
  samples_left: number;
  global_reports_open: boolean;
  global_closures_open: boolean;
};
type SbReport = {
  id: string;
  created_at: string;
  status: string;
  intake_status: string | null;
  category_id: string | null;
  category_name: Partial<Record<Lang, string>> | null;
  cluster_id: string | null;
  cluster_status: string | null;
  photo_public_path: string | null;
  sample_id: string | null;
};
type RegionCheck = { region_id: number; figure_paths?: { before?: string | null; after_aligned?: string | null } };
type SbResolution = {
  id: string;
  ai_status: string;
  outcome: "open" | "resolved" | "rejected";
  ai_overridden: boolean;
  verification: { evidence?: string; location?: { distance_m?: number }; region_checks?: RegionCheck[] } | null;
  after_public_path: string | null;
  before_public_path: string | null;
  review_note: string | null;
};
type SbCase = {
  id: string;
  status: string;
  report_count: number;
  distinct_devices: number;
  weighted_support: number;
  simulated_reports: number;
  dispatch: { stakeholder_name: string; status: string; brief: Brief | null } | null;
  resolution: SbResolution | null;
};
type State = { session: { id: string; city_id: string; expires_at: string } | null; quota?: Quota; reports?: SbReport[]; cases?: SbCase[] };
type View = { kind: "home" } | { kind: "report"; sample: Sample | null; file: File | null } | { kind: "track"; reportId: string };

const CANDIDATE = "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW";
const POLL_MS = 4000;
const DEFAULT_QUOTA: Quota = { camera_reports_left: 5, camera_closures_left: 2, samples_left: 10, global_reports_open: true, global_closures_open: true };

/** public/<name> -> public URL (anonymized copies and samples only). */
function pub(path: string | null | undefined) {
  return path?.startsWith("public/") && publicStorageBase ? `${publicStorageBase}/${path.slice(7)}` : null;
}

function name(lang: Lang, n: Partial<Record<Lang, string>> | null | undefined) {
  return n?.[lang] ?? n?.es ?? n?.en ?? "";
}

/** Anything still moving on the server, so the page keeps asking. */
function moving(r: SbReport | undefined, c: SbCase | undefined) {
  if (!r) return true;
  if (r.status !== "clustered" && r.status !== "failed") return true;
  if (!c) return r.status === "clustered";
  if (c.status === "candidate") return true;
  if (c.dispatch && !c.dispatch.brief) return true;
  if (c.status === "validated" && !c.dispatch) return true;
  return c.resolution?.outcome === "open" && c.resolution.ai_status === "pending";
}

// C9: welcome with samples and quota, the report with a hand-placed pin, then the whole cycle on
// one screen. The visitor's sandbox device is a second anonymous session (sandboxClient).
export function SandboxApp({ lang, samples }: { lang: Lang; samples: Sample[] }) {
  const [state, setState] = useState<State | null>(null);
  const [view, setView] = useState<View>({ kind: "home" });
  const [error, setError] = useState("");
  const [inputKey, setInputKey] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    const sb = sandboxClient();
    const { data: s } = await sb.auth.getSession();
    if (!s.session) return setState({ session: null });
    const { data, error: e } = await sb.rpc("sandbox_state");
    if (e) {
      setError(errorMessage(lang, e));
      return setState((old) => old ?? { session: null });
    }
    setState(data as State);
  }, [lang]);

  useEffect(() => { void refresh(); }, [refresh]);

  // Signs in (anonymous, sandbox storage) and opens the session the first time something is sent.
  const ensureSession = useCallback(async () => {
    const sb = sandboxClient();
    const { data: s } = await sb.auth.getSession();
    if (!s.session) {
      const { error: e } = await sb.auth.signInAnonymously();
      if (e) throw e;
    }
    const { data, error: e } = await sb.rpc("start_sandbox_session", { p_city: "ambato" });
    if (e) throw e;
    setState(data as State);
  }, []);

  const quota = state?.quota ?? DEFAULT_QUOTA;
  const capped = !quota.global_reports_open;

  if (view.kind === "report")
    return (
      <SbReportForm lang={lang} sample={view.sample} file={view.file} ensureSession={ensureSession}
        onBack={() => setView({ kind: "home" })}
        onSent={(id) => { setView({ kind: "track", reportId: id }); void refresh(); window.scrollTo({ top: 0 }); }} />
    );

  if (view.kind === "track") {
    const report = state?.reports?.find((r) => r.id === view.reportId);
    const kase = state?.cases?.find((c) => c.id === report?.cluster_id);
    return (
      <SbTrack lang={lang} reportId={view.reportId} report={report} kase={kase} quota={quota}
        sample={samples.find((s) => s.id === report?.sample_id) ?? null} refresh={refresh}
        onHome={() => { setView({ kind: "home" }); window.scrollTo({ top: 0 }); }} />
    );
  }

  return (
    <>
      <h1>{t(lang, "sandboxTitle")}</h1>
      <p>{t(lang, "sandboxBody")}</p>
      <p className="muted small">{t(lang, "sbOnlyHere")}</p>

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", marginTop: 0 }}>
          <span><span className="muted">{t(lang, "sbCity")}:</span> <b>Ambato</b></span>
          <span className="muted small">{t(lang, "sbQuotaToday")}</span>
        </div>
        <div className="stats sb-quota">
          <div><b>{quota.camera_reports_left}</b><span>{t(lang, "sbQuotaReports")}</span></div>
          <div><b>{quota.camera_closures_left}</b><span>{t(lang, "sbQuotaClosures")}</span></div>
          <div><b>{quota.samples_left}</b><span>{t(lang, "sbQuotaSamples")}</span></div>
        </div>
        {capped ? (
          <p className="notice warn">
            <b>{t(lang, "sbCapNotice")}</b> <Link href="/demo">{t(lang, "navDemo")}</Link>
          </p>
        ) : null}
      </div>

      <h2>{t(lang, "sbSamples")} <span className="muted small">· {t(lang, "sbSamplesHint")}</span></h2>
      {samples.length ? (
        <div className="sb-samples">
          {samples.map((s) => (
            <button key={s.id} className="sb-sample" disabled={capped || quota.samples_left <= 0}
              onClick={() => setView({ kind: "report", sample: s, file: null })}>
              {pub(s.before_path) ? <img src={pub(s.before_path)!} alt="" loading="lazy" /> : <span className="ph" />}
              <b>{name(lang, s.title)}</b>
              {s.after_path ? <span className="badge sandbox">{t(lang, "sbPair")}</span> : null}
            </button>
          ))}
          <CameraCard lang={lang} disabled={capped || quota.camera_reports_left <= 0} onClick={() => fileInput.current?.click()} />
        </div>
      ) : (
        <>
          <p className="muted">{t(lang, "sbNoSamples")}</p>
          <div className="sb-samples">
            <CameraCard lang={lang} disabled={capped || quota.camera_reports_left <= 0} onClick={() => fileInput.current?.click()} />
          </div>
        </>
      )}
      <input ref={fileInput} key={inputKey} type="file" accept="image/*" capture="environment" className="file-hidden"
        tabIndex={-1} aria-hidden="true"
        onChange={(e) => {
          const f = e.target.files?.[0] ?? null;
          setInputKey((k) => k + 1);
          if (f) setView({ kind: "report", sample: null, file: f });
        }} />
      <p className="muted small">{t(lang, "sbHowItWorks")}</p>
      {error ? <p className="notice warn" role="alert">{error}</p> : null}

      {state?.reports?.length ? (
        <>
          <h2>{t(lang, "sbMyTests")}</h2>
          <div className="case-list">
            {state.reports.map((r) => {
              const thumb = pub(r.photo_public_path) ?? pub(samples.find((x) => x.id === r.sample_id)?.before_path);
              return (
                <button key={r.id} className="case-card" onClick={() => setView({ kind: "track", reportId: r.id })}>
                  {thumb ? <img className="thumb" src={thumb} alt="" /> : <span className="thumb" aria-hidden="true" />}
                  <span>
                    <b>{name(lang, r.category_name) || name(lang, samples.find((x) => x.id === r.sample_id)?.title) || t(lang, "statusYourReport")}</b>
                    <span className="small sb-step">{timelineLabel(lang, r)}</span>
                    <span className="muted small">{ago(lang, r.created_at)}</span>
                  </span>
                </button>
              );
            })}
          </div>
          {state.session ? (
            <p className="muted small">
              {t(lang, "sbExpires").replace("{when}", new Intl.RelativeTimeFormat(lang).format(
                Math.max(1, Math.round((Date.parse(state.session.expires_at) - Date.now()) / 3600e3)), "hour"))}
            </p>
          ) : null}
        </>
      ) : null}
    </>
  );
}

function timelineLabel(lang: Lang, r: SbReport) {
  const step = timelineStep(r);
  return TIMELINE.find((x) => x.id === step)?.[lang] ?? step;
}

function CameraCard({ lang, disabled, onClick }: { lang: Lang; disabled: boolean; onClick: () => void }) {
  return (
    <button type="button" className="sb-sample camera" disabled={disabled} onClick={onClick}>
      <span className="shutter" aria-hidden="true"><span /></span>
      <b>{t(lang, "sbCamera")}</b>
      <span className="small">{t(lang, "sbCameraHint")}</span>
    </button>
  );
}

// Report: the photo (sample or camera), suggested text and a hand-placed pin.
function SbReportForm({ lang, sample, file, ensureSession, onBack, onSent }: {
  lang: Lang; sample: Sample | null; file: File | null; ensureSession: () => Promise<void>;
  onBack: () => void; onSent: (id: string) => void;
}) {
  const [text, setText] = useState(sample ? name(lang, sample.suggested_text) : "");
  // Somewhere in central Ambato, so two tests of the same kind do not always land on each other.
  const [pin, setPin] = useState<LatLon>(() => ({
    lat: CENTER[1] + (Math.random() - 0.5) * 0.006, lon: CENTER[0] + (Math.random() - 0.5) * 0.006,
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<string | null>(sample ? pub(sample.before_path) : null);

  useEffect(() => {
    if (!file) return;
    const u = URL.createObjectURL(file);
    setPreview(u);
    return () => URL.revokeObjectURL(u);
  }, [file]);

  async function send() {
    setBusy(true);
    setError("");
    try {
      await ensureSession();
      const sb = sandboxClient();
      const id = crypto.randomUUID();
      const base = { p_report_id: id, p_location: { lat: pin.lat, lon: pin.lon }, p_lang: lang, p_raw_text: text };
      if (sample) {
        const { error: e } = await sb.rpc("submit_report", { ...base, p_sample_id: sample.id });
        if (e) throw e;
      } else if (file) {
        const { data: n, error: e1 } = await sb.rpc("issue_capture_nonce", { p_purpose: "report" });
        if (e1) throw e1;
        const { blob, width, height } = await resizeToJpeg(file);
        const { error: e2 } = await sb.storage.from(n.bucket).upload(n.upload_path, blob, { contentType: "image/jpeg" });
        if (e2) throw e2;
        const { error: e3 } = await sb.rpc("submit_report", {
          ...base, p_nonce: n.nonce, p_photo_path: n.upload_path, p_width: width, p_height: height,
          p_captured_at: new Date().toISOString(),
        });
        if (e3) throw e3;
      }
      onSent(id);
    } catch (e) {
      setError(errorMessage(lang, e));
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <button className="icon-btn" aria-label={t(lang, "sbBack")} onClick={onBack}><BackIcon /></button>
        <h1>{t(lang, "sbReportTitle")}</h1>
      </div>
      <div className="card">
        <div className="photo-ready">
          {preview ? <img src={preview} alt="" /> : null}
          <div>
            <b>{sample ? name(lang, sample.title) : t(lang, "reportPhotoReady")}</b>
            {sample?.after_path ? <div><span className="badge sandbox">{t(lang, "sbPair")}</span></div> : null}
          </div>
        </div>
        <div className="field">
          <label htmlFor="sb-text">{t(lang, "sbReportText")} <span className="opt">({t(lang, "optional")})</span></label>
          <textarea id="sb-text" rows={3} maxLength={2000} value={text} placeholder={t(lang, "reportTextPlaceholder")}
            onChange={(e) => setText(e.target.value)} />
          <VoiceButton lang={lang} onText={(said) => setText((old) => (old.trim() ? `${old.trim()} ${said}` : said))}
            getToken={async () => {
              await ensureSession();
              const { data } = await sandboxClient().auth.getSession();
              if (!data.session) throw new Error("not_signed_in");
              return data.session.access_token;
            }} />
        </div>
        <div className="field">
          <div className="label">{t(lang, "sbPinTitle")}</div>
          <PinMap pin={pin} onPin={setPin} label={t(lang, "sbPinTitle")} />
          <div className="hint">{t(lang, "sbPinHint")}</div>
        </div>
        {error ? <p className="notice warn" role="alert">{error}</p> : null}
      </div>
      <div className="send-bar">
        <button className="primary btn-lg block" disabled={busy} onClick={send}>
          {busy ? t(lang, "resSending") : t(lang, "sbSend")}
        </button>
      </div>
    </>
  );
}

// The whole cycle for one report: check, neighbours, brief, accept, closure, review.
function SbTrack({ lang, reportId, report, kase, sample, quota, refresh, onHome }: {
  lang: Lang; reportId: string; report: SbReport | undefined; kase: SbCase | undefined; sample: Sample | null;
  quota: Quota; refresh: () => Promise<void>; onHome: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [overriding, setOverriding] = useState(false);
  const [viewing, setViewing] = useState<{ shots: Shot[]; i: number } | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const asked = useRef(false);
  const keepPolling = moving(report, kase);

  useEffect(() => {
    if (!keepPolling) return;
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [keepPolling, refresh]);

  // Once the report is on the map, the simulated neighbours confirm it (UX flows §3.4).
  useEffect(() => {
    if (asked.current || report?.status !== "clustered" || !kase || kase.simulated_reports > 0) return;
    asked.current = true;
    void sandboxClient().rpc("sandbox_simulate_neighbours", { p_report_id: reportId }).then(({ error: e }) => {
      if (e) setError(errorMessage(lang, e));
      void refresh();
    });
  }, [report?.status, kase, reportId, lang, refresh]);

  async function act(fn: () => PromiseLike<{ error: unknown }>) {
    setBusy(true);
    setError("");
    try {
      const { error: e } = await fn();
      if (e) throw e;
      setNote("");
      setOverriding(false);
      await refresh();
    } catch (e) {
      setError(errorMessage(lang, e));
    } finally {
      setBusy(false);
    }
  }

  async function closeWithCamera(file: File) {
    await act(async () => {
      const sb = sandboxClient();
      const { data: n, error: e1 } = await sb.rpc("issue_capture_nonce", { p_purpose: "community_resolution" });
      if (e1) return { error: e1 };
      const { blob, width, height } = await resizeToJpeg(file);
      const { error: e2 } = await sb.storage.from(n.bucket).upload(n.upload_path, blob, { contentType: "image/jpeg" });
      if (e2) return { error: e2 };
      return sb.rpc("sandbox_submit_closure", {
        p_cluster_id: kase!.id, p_nonce: n.nonce, p_photo_path: n.upload_path, p_width: width, p_height: height,
        p_captured_at: new Date().toISOString(),
      });
    });
  }

  const head = (
    <div className="page-head">
      <button className="icon-btn" aria-label={t(lang, "sbBack")} onClick={onHome}><BackIcon /></button>
      <h1>{t(lang, "statusTitle")}</h1>
    </div>
  );
  if (!report)
    return (<>{head}<p className="muted" role="status">{t(lang, "reportReceived")}</p></>);

  const checked = report.status === "clustered" || report.status === "vision_done";
  const photo = pub(report.photo_public_path) ?? (sample ? pub(sample.before_path) : null);
  const step = timelineStep(report);
  const d = kase?.dispatch;
  const res = kase?.resolution;
  const pending = res?.outcome === "open" && res.ai_status === "pending";
  const canClose = d?.status === "accepted" && kase?.status === "acknowledged" && res?.outcome !== "open";
  const override = res ? res.ai_status !== CANDIDATE : false;
  const brief = d?.brief ? inLang(d.brief, lang) : null;
  const cites = new Map((d?.brief?.citations ?? []).map((c) => [c.id, c]));
  const before = pub(res?.before_public_path) ?? pub(report.photo_public_path);
  const after = pub(res?.after_public_path) ?? (res && sample?.after_path ? pub(sample.after_path) : null);
  const figures = (res?.verification?.region_checks ?? []).flatMap((c) => [
    { src: pub(c.figure_paths?.before), caption: `${t(lang, "resBefore")} #${c.region_id}` },
    { src: pub(c.figure_paths?.after_aligned), caption: `${t(lang, "resAfter")} #${c.region_id}` },
  ]).filter((x): x is { src: string; caption: string } => !!x.src);

  return (
    <div aria-live="polite">
      {head}
      <div className="card status-head">
        {photo ? (
          <button className="thumb-btn" onClick={() => setViewing({ shots: [{ src: photo }], i: 0 })} aria-label={t(lang, "photoExpand")}>
            <img className="thumb" src={photo} alt={t(lang, "reportPublicPhoto")} />
          </button>
        ) : <span className="thumb" aria-hidden="true" />}
        <div>
          <b>{name(lang, report.category_name) || (sample ? name(lang, sample.title) : t(lang, "statusYourReport"))}</b>
          <div className="muted small">{ago(lang, report.created_at)} · <span className="badge sandbox">Sandbox</span></div>
          {checked && report.intake_status ? (
            <div className={report.intake_status === "REPORT_CONFIRMED_BY_PHOTO" ? "intake-ok" : "intake-no"}>
              {intakeMessage(lang, report.intake_status)}
            </div>
          ) : report.status !== "failed" ? <div className="muted small">{t(lang, "reportReceived")}</div> : null}
        </div>
      </div>
      {report.status === "failed" ? <p className="notice warn">{t(lang, "reportCheckFailed")}</p> : null}

      <Timeline lang={lang} step={step} failed={report.status === "failed"} />

      {report.status === "clustered" ? (
        <div className={kase && kase.simulated_reports > 0 ? "notice ok" : "notice"} role="status">
          {kase && kase.simulated_reports > 0 ? (
            <>
              <b>{t(lang, "sbNeighbours")}</b>
              <p>{t(lang, "sbNeighboursDetail").replace("{d}", String(kase.distinct_devices))
                .replace("{s}", kase.weighted_support.toLocaleString(lang === "es" ? "es-EC" : "en-US", { maximumFractionDigits: 2 }))}</p>
            </>
          ) : <b>{t(lang, "sbNeighboursWaiting")}</b>}
        </div>
      ) : !checked && report.status !== "failed" ? <p className="muted small">{t(lang, "sbWaitingMap")}</p> : null}

      {d ? (
        <div className="card">
          <h2>{t(lang, "sbBriefTitle")}</h2>
          {brief ? (
            <>
              <h3>{brief.title}</h3>
              <BriefText text={brief.body_markdown} cites={cites} />
              {brief.actions.length ? (
                <>
                  <h3>{t(lang, "panelActions")}</h3>
                  <ul>
                    {brief.actions.map((a) => (
                      <li key={a.action_id}>
                        <b>{a.action_id.replace(/_/g, " ")}</b>: <BriefText inline text={`${a.rationale} [${a.citations.join("] [")}]`} cites={cites} />
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
              <div className="sb-actor">
                <p className="small"><b>{t(lang, "sbActorIntro").replace("{name}", d.stakeholder_name)}</b></p>
                {d.status === "sent" || d.status === "seen" ? (
                  <button className="primary" disabled={busy}
                    onClick={() => act(() => sandboxClient().rpc("sandbox_accept", { p_cluster_id: kase!.id }))}>
                    {t(lang, "sbAccept")}
                  </button>
                ) : <p className="muted small">{t(lang, "sbAccepted")}</p>}
              </div>
            </>
          ) : <p className="muted" role="status">{t(lang, "sbBriefWriting")}</p>}
        </div>
      ) : null}

      {canClose ? (
        <div className="card dashed">
          <h2>{t(lang, "sbCloseTitle")}</h2>
          <p className="muted small">{t(lang, "sbCloseHint")}</p>
          <div className="row">
            {sample?.after_path ? (
              <button className="primary" disabled={busy || quota.samples_left <= 0 || !quota.global_closures_open}
                onClick={() => act(() => sandboxClient().rpc("sandbox_submit_closure", { p_cluster_id: kase!.id, p_sample_id: sample.id }))}>
                {t(lang, "sbClosePair")}
              </button>
            ) : null}
            <button className={sample?.after_path ? "secondary" : "primary"}
              disabled={busy || quota.camera_closures_left <= 0 || !quota.global_closures_open}
              onClick={() => fileInput.current?.click()}>
              {t(lang, "sbCloseCamera")} ({quota.camera_closures_left})
            </button>
          </div>
          {!sample?.after_path ? <p className="muted small">{t(lang, "sbNoPair")}</p> : null}
          <input ref={fileInput} key={inputKey} type="file" accept="image/*" capture="environment" className="file-hidden"
            tabIndex={-1} aria-hidden="true"
            onChange={(e) => {
              const f = e.target.files?.[0];
              setInputKey((k) => k + 1);
              if (f) void closeWithCamera(f);
            }} />
        </div>
      ) : null}

      {res ? (
        <div className="card">
          <h2>{t(lang, "resReviewTitle")}</h2>
          <div className={`verdict ${pending ? "wait" : res.ai_status === CANDIDATE ? "ok" : "warn"}`} role="status">
            <b>{pending ? t(lang, "resChecking") : t(lang, res.ai_status === CANDIDATE ? "resAiSeesFixed" : "resAiNotConfirmed")}</b>
            <span className="small">{pending ? t(lang, "sbWaitAi") : t(lang, `ai_${res.ai_status}` as Key)}</span>
          </div>
          {res.verification?.evidence === "location" ? (
            <p className="muted small">{t(lang, "resByLocation")} ({Math.round(res.verification.location?.distance_m ?? 0)} m)</p>
          ) : null}
          {before || after ? (
            <div className="pair">
              <figure><figcaption>{t(lang, "resBefore")}</figcaption>
                {before ? <PhotoFrame lang={lang} src={before} alt="" onOpen={() => setViewing({ shots: [{ src: before, caption: t(lang, "resBefore") }, ...(after ? [{ src: after, caption: t(lang, "resAfterRaw") }] : [])], i: 0 })} /> : <span />}</figure>
              <figure><figcaption>{t(lang, "resAfterRaw")}</figcaption>
                {after ? <PhotoFrame lang={lang} src={after} alt="" onOpen={() => setViewing({ shots: [...(before ? [{ src: before, caption: t(lang, "resBefore") }] : []), { src: after, caption: t(lang, "resAfterRaw") }], i: before ? 1 : 0 })} /> : <span />}</figure>
            </div>
          ) : null}
          {figures.length ? (
            <>
              <h3>{t(lang, "resRegions")}</h3>
              <div className="figures">
                {figures.map((f, i) => (
                  <figure key={f.src}>
                    <button className="thumb-btn" onClick={() => setViewing({ shots: figures, i })} aria-label={t(lang, "photoExpand")}>
                      <img src={f.src} alt="" />
                    </button>
                    <figcaption>{f.caption}</figcaption>
                  </figure>
                ))}
              </div>
            </>
          ) : null}
          <p className="muted small">{t(lang, "resDisclaimer")}</p>

          {res.outcome !== "open" ? (
            <p className={res.outcome === "resolved" ? "notice ok" : "notice"}>
              {t(lang, `resOutcome_${res.outcome}` as Key)}
              {res.ai_overridden ? ` · ${t(lang, "resOverridden")}` : ""}
              {res.review_note ? ` · ${res.review_note}` : ""}
            </p>
          ) : !pending ? (
            <div className="sb-review">
              {override && overriding ? (
                <div className="field">
                  <label htmlFor="sb-note">{t(lang, "resNoteOverride")}</label>
                  <textarea id="sb-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
                </div>
              ) : null}
              <div className="row">
                {override ? (
                  <>
                    <button className="primary" disabled={busy}
                      onClick={() => act(() => sandboxClient().rpc("sandbox_review_closure", { p_resolution_id: res.id, p_verdict: "reject", p_note: null }))}>
                      {t(lang, "resReject")}
                    </button>
                    {overriding ? (
                      <button className="secondary" disabled={busy || !note.trim()}
                        onClick={() => act(() => sandboxClient().rpc("sandbox_review_closure", { p_resolution_id: res.id, p_verdict: "approve", p_note: note }))}>
                        {t(lang, "resApproveAnyway")}
                      </button>
                    ) : (
                      <button className="link" onClick={() => setOverriding(true)}>{t(lang, "resApproveAnyway")}</button>
                    )}
                  </>
                ) : (
                  <>
                    <button className="primary" disabled={busy}
                      onClick={() => act(() => sandboxClient().rpc("sandbox_review_closure", { p_resolution_id: res.id, p_verdict: "approve", p_note: null }))}>
                      {t(lang, "resApprove")}
                    </button>
                    <button className="secondary" disabled={busy}
                      onClick={() => act(() => sandboxClient().rpc("sandbox_review_closure", { p_resolution_id: res.id, p_verdict: "reject", p_note: null }))}>
                      {t(lang, "resReject")}
                    </button>
                  </>
                )}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {kase?.status === "resolved" ? (
        <div className="notice ok">
          <b>{t(lang, "sbDone")}</b>
          <p>{t(lang, "sbDoneBody")}</p>
        </div>
      ) : null}
      {error ? <p className="notice warn" role="alert">{error}</p> : null}
      <button className="secondary block" onClick={onHome}>{kase?.status === "resolved" ? t(lang, "sbAgain") : t(lang, "sbBack")}</button>

      {viewing ? <Lightbox lang={lang} shots={viewing.shots} start={viewing.i} onClose={() => setViewing(null)} /> : null}
    </div>
  );
}
