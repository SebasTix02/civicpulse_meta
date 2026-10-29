"""Simulator v1: consensus scenarios with simulated devices, in a fresh sandbox session.

    pip install -e services/orchestrator
    python scripts/simulate.py                       # the four scenarios below, near Ambato's centre
    python scripts/simulate.py --lat -1.2417 --lon -78.6197

Uses SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from .env (never printed) and the database
function simulate_reports(), which only works inside a sandbox session: nothing here can touch
the real map. No photos, no SAM, no LLM: the reports arrive already photo-checked, so this only
exercises clustering and consensus, and costs nothing. Sandbox data is wiped with its session.

Scenarios (plan §5.5, demo minute 0:55):
  spammer    1 device sends 5 reports           -> must stay candidate
  wifi       4 phones on the same network       -> must stay candidate
  unclear    3 devices, photos not verifiable   -> must stay candidate
  neighbours 3 devices on their own networks    -> must be validated

Then it waits for the brief that Modal writes for the sandbox stakeholder (--no-brief skips it).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
sys.path.insert(0, str(ROOT / "scripts"))

from civicpulse.db import Db
from smoke_models import load_env

# name, category, offset north in metres (each scenario in its own spot), arguments, expected
SCENARIOS = [
    ("spammer", "pothole", 0, {"p_devices": 1, "p_reports_each": 5}, "candidate"),
    ("wifi", "garbage", 200, {"p_devices": 4, "p_network": "10.0.0.0/24"}, "candidate"),
    ("unclear", "broken_sidewalk", 400, {"p_devices": 3, "p_consistency": 0.5}, "candidate"),
    ("neighbours", "informal_stop", 600, {"p_devices": 3}, "validated"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=-1.2417)
    ap.add_argument("--lon", type=float, default=-78.6197)
    ap.add_argument("--city", default="ambato")
    ap.add_argument("--no-brief", action="store_true", help="do not wait for the brief of the validated cluster")
    args = ap.parse_args()
    load_env()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing in .env", file=sys.stderr)
        return 2
    db = Db(url, key)
    resp = db.client.post(f"{db.base}/sandbox_sessions", json={"city_id": args.city},
                          headers=db.headers | {"Prefer": "return=representation"})
    resp.raise_for_status()
    session = resp.json()[0]["id"]
    print(f"sandbox session {session}\n")
    print(f"{'scenario':<11} {'reports':>7} {'devices':>7} {'support':>7}  {'status':<10} expected")
    spam_reports = spam_filtered = failures = 0
    validated = None
    for name, category, north_m, kwargs, expected in SCENARIOS:
        out = db.rpc("simulate_reports", {"p_session": session, "p_category": category,
                                          "p_lat": args.lat + north_m / 111320, "p_lon": args.lon, **kwargs})
        last = out["last"]
        ok = last["status"] == expected
        failures += not ok
        if expected == "validated" and ok:
            validated = last["cluster_id"]
        if expected == "candidate":
            spam_reports += last["report_count"]
            spam_filtered += last["report_count"] if ok else 0
        print(f"{name:<11} {last['report_count']:>7} {last['distinct_devices']:>7} {last['weighted_support']:>7}  "
              f"{last['status']:<10} {expected} {'OK' if ok else 'FAIL'}")
    print(f"\nspam filtered: {spam_filtered}/{spam_reports} reports "
          f"({100 * spam_filtered / max(1, spam_reports):.0f}%), {failures} scenario(s) off")
    if validated and not args.no_brief:
        failures += not wait_for_brief(db, validated)
    return 1 if failures else 0


def wait_for_brief(db: Db, cluster_id: str, timeout_s: int = 120) -> bool:
    """The validated cluster is routed to the sandbox stakeholder (seed) and Modal writes its brief."""
    print(f"\nwaiting up to {timeout_s} s for the brief of cluster {cluster_id[:8]}...")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        rows = db.select("dispatches", {"select": "brief", "cluster_id": f"eq.{cluster_id}"})
        if not rows:
            print("no dispatch: is the sandbox stakeholder seeded (supabase/seed/ambato.sql)?")
            return False
        brief = rows[0]["brief"]
        if brief:
            model = (brief.get("model") or {}).get("model") or "template, no model"
            print(f"\n# {brief['title']}\n\n{brief['body_markdown']}\n")
            for a in brief["actions"]:
                print(f"- {a['action_id']}: {a['rationale']}")
            print(f"\nmodel: {model} · validator: {brief['validator']}")
            return True
        time.sleep(5)
    print("no brief yet: check the Modal logs of run_job (kind brief)")
    return False


if __name__ == "__main__":
    sys.exit(main())
