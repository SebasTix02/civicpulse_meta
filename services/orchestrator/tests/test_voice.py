import io
import json
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from civicpulse.api import create_app
from civicpulse.config import Settings
from civicpulse.voice import VoiceError, check_wav, keywords_from_catalog


def wav(seconds=1.0, rate=16000, channels=1, width=2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate) * channels * width)
    return buf.getvalue()


def test_check_wav_accepts_16k_mono_and_measures_it():
    clip = check_wav(wav(2.5))
    assert clip.seconds == 2.5 and clip.rate == 16000


@pytest.mark.parametrize("data", [b"not audio", wav(rate=44100), wav(channels=2), wav(width=1), wav(0)])
def test_check_wav_refuses_other_audio(data):
    with pytest.raises(VoiceError) as e:
        check_wav(data)
    assert e.value.code == "bad_audio"


def test_check_wav_refuses_huge_body():
    with pytest.raises(VoiceError) as e:
        check_wav(b"0" * 3_000_000)
    assert e.value.code == "voice_too_long"


def test_keywords_spanish_first_no_repeats():
    cats = {"a": {"keywords": {"es": ["bache", "hueco", "x"], "en": ["pothole"]}},
            "b": {"keywords": {"es": ["bache", "basura"], "en": ["garbage"]}}}
    assert keywords_from_catalog(cats) == ["bache", "hueco", "basura", "pothole", "garbage"]


class Fake:
    """Supabase RPC + Meta transcribe, recorded."""

    def __init__(self, quota=(200, {"left": 9}), asr=(200, {"transcript": " hay un bache "}), keywords_ok=True):
        self.quota, self.asr, self.keywords_ok, self.calls = quota, asr, keywords_ok, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if req.url.path.endswith("/rpc/take_voice_quota"):
            return httpx.Response(self.quota[0], json=self.quota[1])
        body = req.content.decode("latin-1")
        if '"keywords"' in body and not self.keywords_ok:
            return httpx.Response(400, json={"error": "bad keywords"})
        return httpx.Response(self.asr[0], json=self.asr[1])


def app_with(fake, monkeypatch):
    monkeypatch.setenv("META_MODEL_API_KEY", "k")
    s = Settings(supabase_url="https://db.example", supabase_service_key="svc")
    return TestClient(create_app(lambda *_: None, s, voice_http=httpx.Client(transport=httpx.MockTransport(fake))))


def test_transcribe_uses_the_citizens_token_and_returns_text(monkeypatch):
    fake = Fake()
    r = app_with(fake, monkeypatch).post("/transcribe", content=wav(3), headers={"Authorization": "Bearer user-jwt"})
    assert r.status_code == 200 and r.json() == {"text": "hay un bache", "seconds": 3.0, "left": 9}
    quota, asr = fake.calls
    assert quota.headers["authorization"] == "Bearer user-jwt" and quota.headers["apikey"] == "svc"
    assert json.loads(quota.content) == {"p_seconds": 3.0}
    assert asr.url.path == "/v1/asr/transcribe" and asr.headers["authorization"] == "Bearer k"
    assert b"muse-voice-transcribe-1.0" in asr.content and b"Spanish" in asr.content


def test_transcribe_retries_without_keywords(monkeypatch):
    fake = Fake(keywords_ok=False)
    r = app_with(fake, monkeypatch).post("/transcribe", content=wav(1), headers={"Authorization": "Bearer u"})
    assert r.status_code == 200 and len(fake.calls) == 3


def test_transcribe_errors(monkeypatch):
    c = app_with(Fake(), monkeypatch)
    assert c.post("/transcribe", content=wav(1)).json() == {"error": "not_signed_in"}
    assert c.post("/transcribe", content=b"x", headers={"Authorization": "Bearer u"}).json() == {"error": "bad_audio"}
    over = app_with(Fake(quota=(429, {"message": "voice_quota_exceeded"})), monkeypatch)
    r = over.post("/transcribe", content=wav(1), headers={"Authorization": "Bearer u"})
    assert r.status_code == 429 and r.json() == {"error": "voice_quota_exceeded"}
    bad_token = app_with(Fake(quota=(401, {"message": "JWT expired"})), monkeypatch)
    assert bad_token.post("/transcribe", content=wav(1), headers={"Authorization": "Bearer u"}).status_code == 401
    down = app_with(Fake(asr=(500, {})), monkeypatch)
    r = down.post("/transcribe", content=wav(1), headers={"Authorization": "Bearer u"})
    assert r.status_code == 502 and r.json() == {"error": "voice_unavailable"}


def test_no_meta_key_means_not_configured(monkeypatch):
    c = app_with(Fake(), monkeypatch)
    monkeypatch.delenv("META_MODEL_API_KEY")
    r = c.post("/transcribe", content=wav(1), headers={"Authorization": "Bearer u"})
    assert r.status_code == 503 and r.json() == {"error": "voice_not_configured"}
