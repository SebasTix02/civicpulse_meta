from datetime import UTC, datetime

import httpx

from civicpulse.config import ProviderConfig, Settings
from civicpulse.health import check

NOW = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)


class FakeDb:
    def __init__(self, runs):
        self.runs, self.written = runs, None

    def select(self, table, params):
        assert table == "stage_runs" and params["started_at"].startswith("gte.2026-10-30T20:30")
        return self.runs

    def update(self, table, patch, **eq):
        assert table == "app_config" and eq == {"key": "provider_health"}
        self.written = patch["value"]


def settings(monkeypatch, sam_key=True):
    monkeypatch.setenv("K1", "x")
    if sam_key:
        monkeypatch.setenv("META_MODEL_API_KEY", "y")
    else:
        monkeypatch.delenv("META_MODEL_API_KEY", raising=False)
    return Settings(sam_backend="meta_api", llm_providers=[ProviderConfig("a", "https://a.test/v1", "m", "K1")])


def client(status):
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, json={"data": []})))


def test_all_ok_with_a_provider_up_and_no_recent_runs(monkeypatch):
    db = FakeDb([])
    h = check(db, settings(monkeypatch), NOW, client(200))
    assert h["sam"] == "ok" and h["vision"] == "ok" and db.written["checked_at"] == NOW.isoformat()
    assert db.written["providers"] == {"a": True}


def test_provider_down_or_rejected_key_means_vision_down(monkeypatch):
    for status in (503, 401):
        assert check(FakeDb([]), settings(monkeypatch), NOW, client(status))["vision"] == "down"


def test_recent_runs_that_all_failed_mark_the_stage_down(monkeypatch):
    runs = [{"stage": "sam_ground", "status": "error"}] * 3 + [{"stage": "classify", "status": "ok"}]
    h = check(FakeDb(runs), settings(monkeypatch), NOW, client(200))
    assert h["sam"] == "down" and h["vision"] == "ok"
    two = [{"stage": "classify", "status": "error"}] * 2  # too few runs to say anything
    assert check(FakeDb(two), settings(monkeypatch), NOW, client(200))["vision"] == "ok"


def test_no_sam_key_means_sam_down(monkeypatch):
    assert check(FakeDb([]), settings(monkeypatch, sam_key=False), NOW, client(200))["sam"] == "down"
