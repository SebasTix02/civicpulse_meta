"""Voice reports: a short WAV clip from the citizen -> text, with Muse Voice Transcribe (Meta Model API).

The audio is only held in memory for the call; nothing is stored. The text goes back to the
citizen, who reads and edits it before sending the report; the spec stage then redacts it like any
typed text. Cost is capped per device and per day by take_voice_quota (migration 21), which runs
with the citizen's own session token so the database knows who is asking.
"""

from __future__ import annotations

import io
import json
import os
import wave
from dataclasses import dataclass

import httpx

MAX_BYTES = 2_500_000          # 60 s of 16 kHz mono 16-bit PCM is about 1.9 MB
RATES = (16_000, 24_000)       # what the transcribe endpoint accepts
MODEL = "muse-voice-transcribe-1.0"


class VoiceError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Clip:
    seconds: float
    rate: int


def check_wav(data: bytes) -> Clip:
    """RIFF/WAVE, PCM 16-bit, mono, 16 or 24 kHz, as the transcribe endpoint requires."""
    if len(data) > MAX_BYTES:
        raise VoiceError("voice_too_long", 413)
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError) as exc:
        raise VoiceError("bad_audio", 422) from exc
    if channels != 1 or width != 2 or rate not in RATES or frames == 0:
        raise VoiceError("bad_audio", 422)
    return Clip(round(frames / rate, 2), rate)


def _db_error(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message") or "")
    except ValueError:
        return ""


def take_quota(supabase_url: str, service_key: str, user_token: str, seconds: float,
               client: httpx.Client) -> int:
    """Counts the clip against the caller's quota. The citizen's token decides who the caller is."""
    resp = client.post(f"{supabase_url.rstrip('/')}/rest/v1/rpc/take_voice_quota", json={"p_seconds": seconds},
                       headers={"apikey": service_key, "Authorization": f"Bearer {user_token}"})
    if resp.status_code in (401, 403):
        raise VoiceError("not_signed_in", 401)
    if resp.status_code >= 400:
        code = _db_error(resp)
        if code in ("voice_quota_exceeded", "voice_daily_cap"):
            raise VoiceError(code, 429)
        if code in ("voice_too_long", "not_signed_in"):
            raise VoiceError(code, 422 if code == "voice_too_long" else 401)
        raise VoiceError("quota_unavailable", 503)
    return int((resp.json() or {}).get("left", 0))


def transcribe(data: bytes, client: httpx.Client, keywords: list[str] | None = None) -> str:
    """One clip through Muse Voice Transcribe. Spanish and English are hinted, not forced."""
    key = os.environ.get("META_MODEL_API_KEY")
    if not key:
        raise VoiceError("voice_not_configured", 503)
    base = os.environ.get("META_API_BASE", "https://api.meta.ai/v1").rstrip("/")
    settings: dict = {"model": MODEL, "audioEncoding": "WAV", "languageBias": ["Spanish", "English"]}
    if keywords:
        settings["keywords"] = keywords
    for attempt in range(2):
        files = {"request": (None, json.dumps(settings), "application/json"),
                 "audio": ("clip.wav", data, "audio/wav")}
        try:
            resp = client.post(f"{base}/asr/transcribe", files=files, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            raise VoiceError("voice_unavailable", 502) from exc
        if resp.status_code == 400 and attempt == 0 and "keywords" in settings:
            settings.pop("keywords")  # an unsupported hint must not cost the citizen the transcript
            continue
        if resp.status_code >= 400:
            raise VoiceError("voice_unavailable", 502)
        return str((resp.json() or {}).get("transcript") or "").strip()
    raise VoiceError("voice_unavailable", 502)


def keywords_from_catalog(categories: dict[str, dict], limit: int = 60) -> list[str]:
    """Spanish catalog words first (most reports are Spanish), then English, without repeats."""
    out: list[str] = []
    for lang in ("es", "en"):
        for cat in categories.values():
            for word in (cat.get("keywords") or {}).get(lang, [])[:2]:
                if word not in out:
                    out.append(word)
    return out[:limit]
