from fastapi.testclient import TestClient

from civicpulse.api import create_app
from civicpulse.config import Settings


def client():
    spawned = []
    app = create_app(lambda kind, key: spawned.append((kind, key)), Settings(webhook_secret="s3", ops_secret="o"))
    return TestClient(app), spawned


def post(c, body, token="s3"):
    return c.post("/webhooks/supabase", json=body, headers={"Authorization": f"Bearer {token}"})


def test_rejects_wrong_secret():
    c, spawned = client()
    assert post(c, {"type": "INSERT", "table": "reports", "record": {"id": "r1"}}, token="nope").status_code == 401
    assert spawned == []


def test_report_insert_spawns_intake():
    c, spawned = client()
    r = post(c, {"type": "INSERT", "table": "reports", "schema": "public", "record": {"id": "r1"}})
    assert r.status_code == 202 and spawned == [("intake", "r1")]


def test_first_photo_version_is_ignored():
    c, spawned = client()
    post(c, {"type": "INSERT", "table": "report_photos", "record": {"report_id": "r1", "version": 1}})
    post(c, {"type": "INSERT", "table": "report_photos", "record": {"report_id": "r1", "version": 2}})
    assert spawned == [("photo_replaced", "r1")]


def test_unknown_table():
    c, _ = client()
    assert post(c, {"type": "INSERT", "table": "devices", "record": {}}).status_code == 422
