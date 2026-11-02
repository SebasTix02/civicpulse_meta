"use client";

import { useEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/errors";
import { t, type Lang } from "@/lib/i18n";
import { MAX_SECONDS, startRecording, transcribe, voiceSupported, type Recording } from "@/lib/voice";
import { MicIcon } from "./icons";

type State = "idle" | "recording" | "working";

// Dictate the report instead of typing it (Muse Voice Transcribe). The text lands in the box for the
// citizen to read and fix before sending; the audio is not kept.
export function VoiceButton({ lang, getToken, onText }: {
  lang: Lang; getToken: () => Promise<string>; onText: (text: string) => void;
}) {
  const [state, setState] = useState<State>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState("");
  const [supported, setSupported] = useState(false);
  const rec = useRef<Recording | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => setSupported(voiceSupported()), []);
  useEffect(() => () => { rec.current?.cancel(); abort.current?.abort(); if (timer.current) clearInterval(timer.current); }, []);

  async function start() {
    setError("");
    try {
      rec.current = await startRecording();
    } catch {
      setError(t(lang, "voiceNoMic"));
      return;
    }
    setSeconds(0);
    setState("recording");
    timer.current = setInterval(() => setSeconds((s) => s + 1), 1000);
  }

  async function stop() {
    if (timer.current) clearInterval(timer.current);
    const r = rec.current;
    rec.current = null;
    if (!r) return;
    setState("working");
    const ctl = new AbortController();
    abort.current = ctl;
    try {
      const wav = await r.stop();
      if (ctl.signal.aborted) return;
      const text = await transcribe(wav, await getToken(), ctl.signal);
      if (ctl.signal.aborted) return;
      if (text) onText(text);
      else setError(t(lang, "voiceEmpty"));
    } catch (e) {
      if (!ctl.signal.aborted) setError(errorMessage(lang, e));
    } finally {
      if (abort.current === ctl) {
        abort.current = null;
        setState("idle");
      }
    }
  }

  // Throw away the clip: while recording nothing is sent; while transcribing the text is ignored.
  function cancel() {
    if (timer.current) clearInterval(timer.current);
    rec.current?.cancel();
    rec.current = null;
    abort.current?.abort();
    abort.current = null;
    setError("");
    setState("idle");
  }

  useEffect(() => {
    if (state === "recording" && seconds >= MAX_SECONDS) void stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stop only reads refs
  }, [seconds, state]);

  if (!supported) return null;
  return (
    <div className="voice">
      {state === "recording" ? (
        <button type="button" className="secondary voice-on" onClick={stop}>
          <span className="rec-dot" aria-hidden="true" /> {t(lang, "voiceStop")} · {seconds}s / {MAX_SECONDS}s
        </button>
      ) : (
        <button type="button" className="secondary" onClick={start} disabled={state === "working"}>
          <MicIcon /> {state === "working" ? t(lang, "voiceWorking") : t(lang, "voiceStart")}
        </button>
      )}
      {state !== "idle" ? (
        <button type="button" className="link voice-cancel" onClick={cancel}>{t(lang, "voiceCancel")}</button>
      ) : null}
      <div className="hint">{t(lang, "voicePrivacy")}</div>
      {error ? <div className="notice warn" role="status">{error}</div> : null}
    </div>
  );
}
