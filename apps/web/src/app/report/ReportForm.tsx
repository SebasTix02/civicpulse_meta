"use client";

import { useState } from "react";
import { t, type Lang } from "@/lib/i18n";
import { browserClient } from "@/lib/supabase";

type Fix = { lat: number; lon: number; accuracy: number };
type Phase = "idle" | "sending" | "done" | "error";

// Longest side 1600 px, re-encoded through a canvas: this also drops EXIF (GPS, device).
async function resizeToJpeg(file: File): Promise<{ blob: Blob; width: number; height: number }> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, width, height);
  const blob = await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("encode_failed"))), "image/jpeg", 0.85),
  );
  return { blob, width, height };
}

function getFix(): Promise<Fix> {
  return new Promise((resolve, reject) =>
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy }),
      reject,
      { enableHighAccuracy: true, timeout: 15000 },
    ),
  );
}

export function ReportForm({ lang }: { lang: Lang }) {
  const [text, setText] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");

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
      try {
        fix = await getFix();
      } catch {
        throw new Error(t(lang, "reportNeedsGps"));
      }
      const { data: nonce, error: nonceError } = await supabase.rpc("issue_capture_nonce", { p_purpose: "report" });
      if (nonceError) throw nonceError;
      const { blob, width, height } = await resizeToJpeg(photo);
      const { error: uploadError } = await supabase.storage
        .from(nonce.bucket)
        .upload(nonce.upload_path, blob, { contentType: "image/jpeg" });
      if (uploadError) throw uploadError;
      // Pin = GPS fix until the draggable pin lands (day 4).
      const { error: submitError } = await supabase.rpc("submit_report", {
        p_report_id: crypto.randomUUID(),
        p_location: { lat: fix.lat, lon: fix.lon },
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
      setPhase("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  }

  if (phase === "done") return <div className="card">{t(lang, "reportReceived")}</div>;

  return (
    <div className="card">
      <label>
        {t(lang, "reportText")}
        <textarea rows={4} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <p>
        <label>
          {t(lang, "reportPhoto")}{" "}
          {/* capture="environment" opens the rear camera on phones: live photos only (UX flows). */}
          <input type="file" accept="image/*" capture="environment" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
        </label>
      </p>
      {photo ? <img className="preview" src={URL.createObjectURL(photo)} alt="" /> : null}
      <p>
        <button className="primary" disabled={!photo || phase === "sending"} onClick={submit}>
          {phase === "sending" ? t(lang, "reportSending") : t(lang, "reportSend")}
        </button>
      </p>
      {error ? <p role="alert">{error}</p> : null}
    </div>
  );
}
