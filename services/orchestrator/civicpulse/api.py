"""FastAPI app: Supabase Database Webhooks in, background jobs out (orchestrator.openapi.yaml)."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from civicpulse.config import Settings, get_settings

HANDLED_TABLES = {"reports", "report_photos", "resolutions", "dispatches"}


class DatabaseWebhookPayload(BaseModel):
    type: str
    table: str
    schema_: str | None = Field(default=None, alias="schema")
    record: dict[str, Any] | None = None
    old_record: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


def _check_bearer(header: str | None, secret: str) -> None:
    if not secret or not header or not header.startswith("Bearer "):
        raise HTTPException(401, "unauthorized")
    if not hmac.compare_digest(header.removeprefix("Bearer "), secret):
        raise HTTPException(401, "unauthorized")


def route(payload: DatabaseWebhookPayload) -> tuple[str, str] | None:
    """Which job a webhook starts, or None when it is ignored on purpose."""
    rec = payload.record or {}
    if payload.type != "INSERT":
        return None
    if payload.table == "reports":
        return "intake", rec["id"]
    if payload.table == "report_photos":
        return ("photo_replaced", rec["report_id"]) if rec.get("version", 1) > 1 else None
    if payload.table == "resolutions":
        return "closure", rec["id"]
    if payload.table == "dispatches":
        return ("brief", f"{rec['cluster_id']}:{rec['stakeholder_id']}") if rec.get("brief") is None else None
    return None


def create_app(spawn: Callable[[str, str], None], settings: Settings | None = None) -> FastAPI:
    """`spawn(job, key)` starts the job in the background (Modal Function.spawn in production)."""
    settings = settings or get_settings()
    app = FastAPI(title="CivicPulse orchestrator", version="0.1.0")

    @app.post("/webhooks/supabase", status_code=202)
    def webhook(payload: DatabaseWebhookPayload, authorization: str | None = Header(default=None)):
        _check_bearer(authorization, settings.webhook_secret)
        if payload.table not in HANDLED_TABLES:
            raise HTTPException(422, "unknown_table")
        job = route(payload)
        if job is None:
            return {"accepted": False, "reason": "ignored"}
        spawn(*job)
        return {"accepted": True, "job": job[0], "key": job[1]}

    @app.post("/ops/reprocess", status_code=202)
    def reprocess(body: dict, authorization: str | None = Header(default=None)):
        _check_bearer(authorization, settings.ops_secret)
        kind = "closure" if body.get("resolution_id") else "intake"
        key = body.get("resolution_id") or body.get("report_id")
        if not key:
            raise HTTPException(400, "report_id or resolution_id required")
        spawn(kind, key)
        return {"accepted": True, "job": kind, "key": key}

    @app.get("/healthz")
    def healthz():
        return {
            "ok": True,
            "llm_providers": [p.name for p in settings.llm_providers if p.api_key],
            "sam_backend": settings.sam_backend,
        }

    return app
