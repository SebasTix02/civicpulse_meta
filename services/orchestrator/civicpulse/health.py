"""Provider health for the backup demo levels (riesgos §4.2): a Modal cron writes
app_config.provider_health, which get_demo_status() reads (stale after 60 min -> level 3).

Free checks only, so it can run every 10 minutes without spending credits:
- vision: at least one Muse provider with a key answers GET /models, and the recent model
  stages (spec, classify, closure, brief) did not all fail;
- sam: the backend is configured, and the recent sam_ground / closure runs did not all fail.
"Recent" is the last WINDOW_MIN minutes; with fewer than MIN_RUNS runs the stages say nothing.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import httpx

from civicpulse.config import Settings
from civicpulse.db import Db

WINDOW_MIN = 30
MIN_RUNS = 3
VISION_STAGES = ("spec", "classify", "closure", "brief")
SAM_STAGES = ("sam_ground", "closure")


def _all_failed(runs: list[dict], stages: tuple[str, ...]) -> bool:
    rs = [r for r in runs if r["stage"] in stages and r["status"] in ("ok", "error")]
    return len(rs) >= MIN_RUNS and all(r["status"] == "error" for r in rs)


def _provider_up(client: httpx.Client, base_url: str, key: str) -> bool:
    try:
        resp = client.get(f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"}, timeout=8.0)
    except httpx.HTTPError:
        return False
    return resp.status_code < 500 and resp.status_code not in (401, 403)


def check(db: Db, settings: Settings, now: datetime, client: httpx.Client | None = None) -> dict:
    client = client or httpx.Client()
    since = (now - timedelta(minutes=WINDOW_MIN)).isoformat()
    runs = db.select("stage_runs", {"select": "stage,status", "started_at": f"gte.{since}",
                                    "order": "started_at.desc", "limit": "200"})
    providers = {p.name: _provider_up(client, p.base_url, p.api_key)
                 for p in settings.llm_providers if p.api_key and p.vision}
    vision_ok = any(providers.values()) and not _all_failed(runs, VISION_STAGES)
    sam_ok = (settings.sam_backend == "fixtures"
              or (settings.sam_backend == "meta_api" and bool(os.environ.get("META_MODEL_API_KEY"))))
    sam_ok = sam_ok and not _all_failed(runs, SAM_STAGES)
    health = {"checked_at": now.isoformat(), "sam": "ok" if sam_ok else "down",
              "vision": "ok" if vision_ok else "down", "providers": providers,
              "recent_runs": len(runs)}
    db.update("app_config", {"value": health}, key="provider_health")
    return health

