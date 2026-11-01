"""Sandbox cleanup (plan §3.6): sandbox_cleanup() (migration 20) deletes the expired sessions'
rows and names them; this removes what Storage still holds for them. Called by the healthcheck
cron, so a session is gone at most ~10 minutes after it expires.

Files of a session: the camera uploads in the private `sandbox` bucket (<session>/<device>/...),
the anonymized copies and crops in `public` (sandbox/<session>/<report>/...) and the closure
figures (sandbox/resolutions/<resolution>/...). Sample photos (public/samples/...) are shared and
stay.
"""

from __future__ import annotations


def cleanup(db) -> dict:
    gone = db.rpc("sandbox_cleanup", {}) or {}
    sessions, resolutions = gone.get("sessions") or [], gone.get("resolutions") or []
    removed = 0
    for bucket, prefix in ([("sandbox", s) for s in sessions] + [("public", f"sandbox/{s}") for s in sessions]
                           + [("public", f"sandbox/resolutions/{r}") for r in resolutions]):
        names = db.list_files(bucket, prefix)
        if names:
            db.remove(bucket, names)
            removed += len(names)
    return {"sessions": len(sessions), "resolutions": len(resolutions), "files": removed}
