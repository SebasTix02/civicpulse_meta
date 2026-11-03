"use client";

// C7 "¿Ya lo arreglaron?" (UX flows §3.3, migration 30): a neighbour at the case sends a photo of
// the fix. The closure stage compares it with the BEFORE photo; other neighbours confirm it.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/errors";
import { t, type Lang } from "@/lib/i18n";
import { resizeToJpeg } from "@/lib/photoQuality";
import { browserClient, publicStorageBase, type PublicCluster } from "@/lib/supabase";
import { BackIcon } from "@/app/icons";
import { distanceM } from "../../report/LocationPicker";

const MAX_DISTANCE_M = 150; // app_config community_rules.max_distance_m
const MAX_ACCURACY_M = 50; // community_rules.max_accuracy_m
const POLL_MS = 5000;
const CANDIDATE = "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW";

type Fix = { lat: number; lon: number; accuracy: number };
type Mine = {
  id: string; ai_status: string; outcome: "open" | "resolved" | "rejected";
  votes: { approvals: number; rejections: number; needed: number };
};

function pub(path: string | null | undefined) {
  return path && publicStorageBase ? `${publicStorageBase}/${path.replace(/^public\//, "")}` : null;
}

const storeKey = (id: string) => `cp-fix-${id}`;
function remembered(id: string): string | null {
  try { return localStorage.getItem(storeKey(id)); } catch { return null; }
}

export function FixFlow({ lang, kase }: { lang: Lang; kase: PublicCluster | null }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [fix, setFix] = useState<Fix | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [resId, setResId] = useState<string | null>(null);
  const [mine, setMine] = useState<Mine | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { if (kase) setResId(remembered(kase.id)); }, [kase]);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  // Location from the start: the page says how far the case is before the photo is taken.
  useEffect(() => {
    if (resId || !("geolocation" in navigator)) return;
    const w = navigator.geolocation.watchPosition(
      (p) => setFix((old) => {
        const next = { lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy };
        return old && old.accuracy < next.accuracy - 5 ? old : next;
      }),
      () => undefined,
      { enableHighAccuracy: true, maximumAge: 10000, timeout: 30000 },
    );
    return () => navigator.geolocation.clearWatch(w);
  }, [resId]);

  const load = useCallback(async () => {
    if (!resId) return;
    const { data } = await browserClient().from("my_community_resolutions").select("id, ai_status, outcome, votes")
      .eq("id", resId).maybeSingle();
    if (data) setMine(data as Mine);
  }, [resId]);

  useEffect(() => {
    if (!resId) return;
    void load();
    const done = mine && (mine.outcome !== "open" || (mine.ai_status !== "pending" && mine.ai_status !== CANDIDATE));
    if (done) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [resId, load, mine]);

  const head = (
    <div className="page-head">
      <Link href="/" className="icon-btn" aria-label={t(lang, "backToMap")}><BackIcon /></Link>
      <h1>{t(lang, "fixTitle")}</h1>
    </div>
  );
  if (!kase) return (<>{head}<p className="notice warn">{errorMessage(lang, "cluster_not_found")}</p></>);

  const before = pub(kase.photo_public_path);
  const name = kase.category_name?.[lang] ?? kase.category_id;

  if (resId) {
    const st = mine?.ai_status ?? "pending";
    const left = mine ? Math.max(0, mine.votes.needed - mine.votes.approvals) : 2;
    const msg = mine?.outcome === "resolved" ? t(lang, "fixResolved")
      : mine?.outcome === "rejected" ? t(lang, "fixRejected")
      : st === "pending" ? t(lang, "fixPending")
      : st === CANDIDATE ? t(lang, "fixCandidate").replace("{n}", String(left))
      : st === "PERSISTENT_ISSUE" ? t(lang, "fixPersists")
      : t(lang, "fixNotVerifiable");
    const ok = mine?.outcome === "resolved" || (mine?.outcome === "open" && st === CANDIDATE);
    return (
      <div aria-live="polite">
        {head}
        <div className="card"><b>{name}</b></div>
        <p className={`notice ${ok ? "ok" : st === "pending" ? "" : "warn"}`} role="status">{msg}</p>
        <Link href="/" className="secondary block">{t(lang, "backToMap")}</Link>
      </div>
    );
  }

  if (kase.status !== "validated" && kase.status !== "acknowledged") {
    return (<>{head}<p className="notice">{t(lang, kase.status === "resolution_pending" ? "caseFixPending" : "fixNotOpen")}</p></>);
  }

  const dist = fix ? Math.round(distanceM(fix, { lat: kase.lat, lon: kase.lon })) : null;
  const near = dist !== null && dist <= MAX_DISTANCE_M && fix!.accuracy <= MAX_ACCURACY_M;

  async function send() {
    if (!file || !fix || !kase) return;
    setBusy(true);
    setError("");
    try {
      const sb = browserClient();
      const { data: s } = await sb.auth.getSession();
      if (!s.session) {
        const { error: e } = await sb.auth.signInAnonymously();
        if (e) throw e;
      }
      const { data: n, error: e1 } = await sb.rpc("issue_capture_nonce", { p_purpose: "community_resolution" });
      if (e1) throw e1;
      const { blob, width, height } = await resizeToJpeg(file);
      const { error: e2 } = await sb.storage.from(n.bucket).upload(n.upload_path, blob, { contentType: "image/jpeg" });
      if (e2) throw e2;
      const { data, error: e3 } = await sb.rpc("submit_community_resolution", {
        p_cluster_id: kase.id, p_nonce: n.nonce, p_photo_path: n.upload_path,
        p_gps_location: { lat: fix.lat, lon: fix.lon }, p_gps_accuracy_m: fix.accuracy,
        p_width: width, p_height: height, p_captured_at: new Date().toISOString(),
      });
      if (e3) throw e3;
      try { localStorage.setItem(storeKey(kase.id), data.resolution_id); } catch { /* the status still shows now */ }
      setResId(data.resolution_id);
    } catch (e) {
      setError(errorMessage(lang, e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {head}
      <p>{t(lang, "fixIntro")}</p>
      <div className="fix-pair">
        <figure>
          {before ? <img src={before} alt={t(lang, "fixBefore")} /> : <span className="thumb" aria-hidden="true" />}
          <figcaption>{t(lang, "fixBefore")}</figcaption>
        </figure>
        <figure>
          {preview ? (
            <img src={preview} alt={t(lang, "fixYours")} />
          ) : (
            <button type="button" className="capture fix-capture" onClick={() => input.current?.click()}
              style={before ? { backgroundImage: `linear-gradient(rgba(22,22,21,.6), rgba(22,22,21,.6)), url(${before})` } : undefined}>
              <span className="shutter" aria-hidden="true"><span /></span>
              <b>{t(lang, "fixTake")}</b>
            </button>
          )}
          <figcaption>{t(lang, "fixYours")}{preview ? <> · <button className="link" onClick={() => input.current?.click()}>{t(lang, "photoRetake")}</button></> : null}</figcaption>
        </figure>
      </div>
      <input ref={input} type="file" accept="image/*" capture="environment" className="file-hidden" tabIndex={-1}
        aria-hidden="true" onChange={(e) => {
          const f = e.target.files?.[0] ?? null;
          setFile(f);
          setPreview(f ? URL.createObjectURL(f) : null);
          e.target.value = "";
        }} />
      {fix && dist !== null ? (
        <p className={near ? "muted small" : "notice warn small"} role="status">
          {t(lang, "fixDistance").replace("{m}", String(dist)).replace("{a}", String(Math.round(fix.accuracy)))}
          {!near ? <><br />{t(lang, "fixTooFar").replace("{m}", String(MAX_DISTANCE_M))}</> : null}
        </p>
      ) : <p className="muted small" role="status">{t(lang, "reportGpsWaiting")}</p>}
      {error ? <p className="notice warn" role="alert">{error}</p> : null}
      <div className="send-bar">
        <button className="primary btn-lg block" disabled={!file || !near || busy} onClick={send}>
          {busy ? t(lang, "fixSending") : t(lang, "fixSend")}
        </button>
        <div className="hint">{t(lang, "reportPrivacy")}</div>
      </div>
    </>
  );
}
