"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { errorCode, errorMessage } from "@/lib/errors";
import { t, type Lang } from "@/lib/i18n";
import { photoIssue, resizeToJpeg, type PhotoIssue } from "@/lib/photoQuality";
import { browserClient } from "@/lib/supabase";
import { BackIcon } from "../icons";
import { LocationPicker, distanceM, PIN_MAX_M, type LatLon } from "./LocationPicker";
import { MyReports } from "./MyReports";
import { ReportStatus } from "./ReportStatus";

type Fix = LatLon & { accuracy: number };
type Phase = "idle" | "sending" | "done" | "error";
type Gps = { state: "waiting" } | { state: "denied" } | { state: "ok"; fix: Fix };

// UX flows §3.1: over 50 m the location is "approximate"; over 500 m the database refuses it.
const GPS_FAIR_M = 50;
const GPS_MAX_M = 500;

function getFix(): Promise<Fix> {
  return new Promise((resolve, reject) =>
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy }),
      reject,
      { enableHighAccuracy: true, timeout: 15000 },
    ),
  );
}

// C3 + C4: take the photo (live camera, no gallery), then details and location, then send.
export function ReportForm({ lang }: { lang: Lang }) {
  const [text, setText] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const [reportId, setReportId] = useState("");
  const [issue, setIssue] = useState<PhotoIssue | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const [actor, setActor] = useState(false);
  const [gps, setGps] = useState<Gps>({ state: "waiting" });
  const [pin, setPin] = useState<LatLon | null>(null);
  const pinMoved = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);

  // An entity account signed in on this browser would be reused for the report and refused
  // (actors_cannot_capture), so say it before the citizen fills anything in.
  useEffect(() => {
    browserClient().auth.getUser().then(({ data }) => setActor(!!data.user && !data.user.is_anonymous));
  }, []);

  // Location starts with the photo, so it is usually ready by the time the citizen presses Send.
  useEffect(() => {
    if (!photo || !("geolocation" in navigator)) return;
    const id = navigator.geolocation.watchPosition(
      (p) => {
        const fix = { lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy };
        setGps((old) => (old.state === "ok" && old.fix.accuracy < fix.accuracy - 5 ? old : { state: "ok", fix }));
      },
      (e) => setGps((old) => (old.state === "ok" ? old : e.code === e.PERMISSION_DENIED ? { state: "denied" } : old)),
      { enableHighAccuracy: true, maximumAge: 10000, timeout: 30000 },
    );
    return () => navigator.geolocation.clearWatch(id);
  }, [photo]);

  // The pin follows the fix until the citizen moves it; a moved pin stays within 15 m of the new fix.
  useEffect(() => {
    if (gps.state !== "ok") return;
    setPin((p) => (!p || !pinMoved.current || distanceM(gps.fix, p) > PIN_MAX_M ? { lat: gps.fix.lat, lon: gps.fix.lon } : p));
  }, [gps]);

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  async function signOutActor() {
    await browserClient().auth.signOut();
    setActor(false);
    setError("");
    setPhase("idle");
  }

  async function choose(file: File | null) {
    setPhoto(file);
    setPreview(file ? URL.createObjectURL(file) : null);
    setIssue(null);
    setError("");
    if (file) setIssue(await photoIssue(file).catch(() => null)); // a failed check never blocks
  }

  function retake() {
    choose(null);
    setInputKey((k) => k + 1); // a fresh input, so the same camera flow opens again
  }

  async function submit() {
    if (!photo) return;
    setPhase("sending");
    setError("");
    try {
      const supabase = browserClient();
      const { data: session } = await supabase.auth.getSession();
      if (!session.session) {
        const { error: signInError } = await supabase.auth.signInAnonymously();
        if (signInError) throw signInError;
      }
      let fix: Fix;
      if (gps.state === "ok") fix = gps.fix;
      else {
        try {
          fix = await getFix();
        } catch {
          throw new Error(t(lang, "reportNeedsGps"));
        }
      }
      const place = pin && distanceM(fix, pin) <= PIN_MAX_M ? pin : fix;
      const { data: nonce, error: nonceError } = await supabase.rpc("issue_capture_nonce", { p_purpose: "report" });
      if (nonceError) throw nonceError;
      const { blob, width, height } = await resizeToJpeg(photo);
      const { error: uploadError } = await supabase.storage
        .from(nonce.bucket)
        .upload(nonce.upload_path, blob, { contentType: "image/jpeg" });
      if (uploadError) throw uploadError;
      const id = crypto.randomUUID();
      const { error: submitError } = await supabase.rpc("submit_report", {
        p_report_id: id,
        p_location: { lat: place.lat, lon: place.lon },
        p_gps_location: { lat: fix.lat, lon: fix.lon },
        p_gps_accuracy_m: fix.accuracy,
        p_lang: lang,
        p_nonce: nonce.nonce,
        p_photo_path: nonce.upload_path,
        p_width: width,
        p_height: height,
        p_captured_at: new Date().toISOString(),
        p_raw_text: text,
      });
      if (submitError) throw submitError;
      setReportId(id);
      setPhase("done");
      window.scrollTo({ top: 0 });
    } catch (e) {
      if (errorCode(e) === "actors_cannot_capture") setActor(true);
      else setError(errorMessage(lang, e));
      setPhase("error");
    }
  }

  function another() {
    setText("");
    retake();
    setPhase("idle");
    pinMoved.current = false;
    setPin(null);
  }

  if (phase === "done")
    return (
      <>
        <div className="page-head">
          <Link href="/" className="icon-btn" aria-label={t(lang, "backToMap")}><BackIcon /></Link>
          <h1>{t(lang, "statusTitle")}</h1>
        </div>
        <ReportStatus lang={lang} reportId={reportId} />
        <button className="secondary block" onClick={another}>{t(lang, "myReportsNew")}</button>
        <MyReports lang={lang} exclude={reportId} />
      </>
    );

  const accuracy = gps.state === "ok" ? gps.fix.accuracy : null;
  const tooFar = accuracy !== null && accuracy > GPS_MAX_M;
  const actorNotice = actor ? (
    <div className="notice warn" role="alert">
      <b>{t(lang, "reportActorSession")}</b>
      <p>{t(lang, "reportActorHint")}</p>
      <button className="primary" onClick={signOutActor}>{t(lang, "reportActorSignOut")}</button>
    </div>
  ) : null;

  if (!photo)
    return (
      <>
        <div className="page-head">
          <Link href="/" className="icon-btn" aria-label={t(lang, "backToMap")}><BackIcon /></Link>
          <h1>{t(lang, "reportTitle")} <span className="step">· {t(lang, "stepOf").replace("{n}", "1")}</span></h1>
        </div>
        {actorNotice}
        {/* capture="environment" opens the rear camera on phones: live photos only (UX flows).
            The card is a real button that clicks the input from the tap itself; the plain input
            below is the same control as before the redesign, kept as a fallback for any browser
            that still refuses. */}
        <button type="button" className="capture" disabled={actor} onClick={() => fileInput.current?.click()}>
          <span className="shutter" aria-hidden="true"><span /></span>
          <b>{t(lang, "reportPhoto")}</b>
          <span>{t(lang, "reportPhotoHint")}</span>
          <small>{t(lang, "reportLiveOnly")}</small>
        </button>
        <input ref={fileInput} key={inputKey} type="file" accept="image/*" capture="environment" disabled={actor}
          className="file-hidden" tabIndex={-1} aria-hidden="true" onChange={(e) => choose(e.target.files?.[0] ?? null)} />
        <label className="native-file">
          {t(lang, "reportPhotoFallback")}
          <input key={`n${inputKey}`} type="file" accept="image/*" capture="environment" disabled={actor}
            onChange={(e) => choose(e.target.files?.[0] ?? null)} />
        </label>
        {error ? <p className="notice warn" role="alert">{error}</p> : null}
        {actor ? null : <MyReports lang={lang} />}
      </>
    );

  return (
    <>
      <div className="page-head">
        <button className="icon-btn" aria-label={t(lang, "backToCamera")} onClick={retake}><BackIcon /></button>
        <h1>{t(lang, "reportTitle")} <span className="step">· {t(lang, "stepOf").replace("{n}", "2")}</span></h1>
      </div>
      {actorNotice}
      <div className="card">
        <div className="photo-ready">
          {preview ? <img src={preview} alt="" /> : null}
          <div>
            <b>{t(lang, "reportPhotoReady")}</b>
            <button className="link" onClick={retake}>{t(lang, "photoRetake")}</button>
          </div>
        </div>
        {issue ? (
          <div className="notice warn" role="status">
            <b>{t(lang, issue === "dark" ? "photoDark" : issue === "bright" ? "photoBright" : "photoBlurry")}</b>
            <p>{t(lang, "photoRetakeHint")}</p>
            <button className="secondary" onClick={retake}>{t(lang, "photoRetake")}</button>
          </div>
        ) : null}

        <div className="field">
          <label htmlFor="report-text">{t(lang, "reportText")} <span className="opt">({t(lang, "optional")})</span></label>
          <textarea id="report-text" rows={4} maxLength={2000} value={text} placeholder={t(lang, "reportTextPlaceholder")}
            onChange={(e) => setText(e.target.value)} />
          <div className="hint">{t(lang, "reportTextHint")}</div>
        </div>

        <div className="field">
          <div className="label">{t(lang, "reportLocation")}</div>
          {gps.state === "ok" && pin ? (
            <>
              <LocationPicker fix={gps.fix} accuracy={gps.fix.accuracy} pin={pin} label={t(lang, "reportLocation")}
                onPin={(p) => { pinMoved.current = true; setPin(p); }} />
              <div className="gps-line">
                <span className="muted">{t(lang, "reportPinHint")}</span>
                <span className={accuracy! <= 20 ? "gps-good" : accuracy! <= GPS_FAIR_M ? "gps-fair" : "gps-bad"}>
                  {t(lang, "reportAccuracy")} {Math.round(accuracy!)} m
                </span>
              </div>
              {accuracy! > GPS_FAIR_M && !tooFar ? (
                <p className="notice warn small">{t(lang, "reportApproximate").replace("{m}", String(Math.round(accuracy!)))}</p>
              ) : null}
              {tooFar ? <p className="notice warn small">{t(lang, "reportTooImprecise")}</p> : null}
            </>
          ) : gps.state === "denied" ? (
            <p className="notice warn small">{t(lang, "reportGpsDenied")}</p>
          ) : (
            <p className="muted small" role="status">{t(lang, "reportGpsWaiting")}</p>
          )}
        </div>
        {error ? <p className="notice warn" role="alert">{error}</p> : null}
      </div>

      <div className="send-bar">
        <button className="primary btn-lg block" disabled={actor || tooFar || phase === "sending"} onClick={submit}>
          {phase === "sending" ? t(lang, "reportSending") : issue ? t(lang, "photoSendAnyway") : t(lang, "reportSend")}
        </button>
        <div className="hint">{t(lang, "reportPrivacy")}</div>
      </div>
    </>
  );
}
