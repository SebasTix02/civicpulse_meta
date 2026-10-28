"""Sweeper (plan §3.3): every minute, re-spawn reports stuck before a final status.

A webhook can be lost, a container can die mid-stage, or SAM / the LLM chain can be down for a
while. Stages are idempotent (stage_runs + compare-and-set), so re-running is always safe; the
sweeper only decides when. A report is stuck when it sits in `received` or `spec_ready` with no
update for STALE_S and no stage started in that window (a slow job still running is left
alone). After `max_attempts` recorded errors for the current photo it is marked `failed`, so a
broken photo does not bill SAM every minute; /ops/reprocess can still run it again by hand.

`vision_done` joins the list once clustering exists (day 5).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from civicpulse.db import Db

RETRY_STATUSES = ("received", "spec_ready")
STALE_S = 120
BATCH = 20


def sweep(db: Db, spawn: Callable[[str, str], None], now: datetime, max_attempts: int = 5) -> dict[str, list[str]]:
    since = now - timedelta(seconds=STALE_S)
    cutoff = since.isoformat()
    stuck = db.select("reports", {
        "select": "id,status,current_photo_version",
        "status": f"in.({','.join(RETRY_STATUSES)})",
        "updated_at": f"lt.{cutoff}",
        "order": "updated_at.asc",
        "limit": str(BATCH),
    })
    out: dict[str, list[str]] = {"spawned": [], "running": [], "failed": []}
    for r in stuck:
        runs = db.select("stage_runs", {
            "select": "status,started_at",
            "report_id": f"eq.{r['id']}",
            "photo_version": f"eq.{r['current_photo_version']}" if r["current_photo_version"] else "is.null",
        })
        if any(datetime.fromisoformat(run["started_at"]) >= since for run in runs):
            out["running"].append(r["id"])
            continue
        if sum(run["status"] == "error" for run in runs) >= max_attempts:
            if db.compare_and_set("reports", r["id"], r["status"], {"status": "failed"}):
                out["failed"].append(r["id"])
            continue
        spawn("intake", r["id"])
        out["spawned"].append(r["id"])
    return out
