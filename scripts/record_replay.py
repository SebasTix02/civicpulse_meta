"""Record a real run as a replay scenario (riesgos §4.3): the demo page plays it back step by step.

    python scripts/record_replay.py <cluster_id> --name neighbours --title-es "Tres vecinos" --title-en "Three neighbours"
    python scripts/record_replay.py <cluster_id> --name closure --dry-run     # print what it would write

Reads with SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from .env (never printed); no model is called,
so it costs nothing. Uses the database function replay_export() (migration 17), which never gives
device ids, networks, raw text or raw photos. Downloads only the anonymized public copies (report
photos, the crops the classifier saw, closure figures) and writes, under apps/web/public/replay/:

    <name>/scenario.json, <name>/*.jpg, index.json (the list the demo page shows)

Commit them to ship the replay with the site: it then works even with Supabase down (level 4).
Rules (riesgos §4.4): only real runs; nothing is edited by hand; simulated citizens stay marked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
sys.path.insert(0, str(ROOT / "scripts"))

from civicpulse.db import Db
from smoke_models import load_env

OUT = ROOT / "apps" / "web" / "public" / "replay"
SCHEMA_VERSION = 1
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def strip_ids(node):
    """Drop every field holding a database id; the scenario refers to reports by short refs only."""
    if isinstance(node, dict):
        return {k: strip_ids(v) for k, v in node.items() if not (isinstance(v, str) and UUID.fullmatch(v))}
    if isinstance(node, list):
        return [strip_ids(v) for v in node]
    return node


class Photos:
    """Downloads public copies once and gives each a local file name."""

    def __init__(self, db: Db, folder: Path, dry_run: bool):
        self.db, self.folder, self.dry_run, self.files = db, folder, dry_run, {}

    def get(self, public_path: str | None, name: str) -> str | None:
        if not public_path:
            return None
        if public_path not in self.files:
            if not public_path.startswith("public/"):  # never a raw or sandbox (private) bucket
                raise ValueError(f"not a public copy: {public_path.split('/')[0]}/...")
            if not self.dry_run:
                (self.folder / f"{name}.jpg").write_bytes(self.db.download(public_path))
            self.files[public_path] = f"{name}.jpg"
        return self.files[public_path]


def build(export: dict, photos: Photos) -> dict:
    reports = []
    for r in export["reports"]:
        ref = r["ref"]
        r["photo"] = photos.get(r.pop("photo_public_path"), ref)
        intake = r.get("intake") or {}
        (intake.get("anonymization") or {}).pop("public_path", None)
        for c in intake.get("candidates") or []:
            c["crop"] = photos.get(c.pop("crop_public_path", None), f"{ref}_c{c['candidate_id']}")
        reports.append(r)
    resolutions = []
    for n, res in enumerate(export["resolutions"], 1):
        res["after_photo"] = photos.get(res.pop("after_public_path"), f"res{n}_after")
        v = res.get("verification") or {}
        for c in v.get("region_checks") or []:
            fp = c.get("figure_paths") or {}
            c["figure_paths"] = {"before": photos.get(fp.get("before"), f"res{n}_r{c['region_id']}_before"),
                                 "after_aligned": photos.get(fp.get("after_aligned"), f"res{n}_r{c['region_id']}_after")}
        figs = v.get("figures") or {}
        if figs:
            v["figures"] = {k: photos.get(p, f"res{n}_{k}") for k, p in figs.items()}
        resolutions.append(res)
    for d in export["dispatches"]:
        for c in (d.get("brief") or {}).get("citations") or []:
            if c.get("kind") in ("report", "cluster"):
                c["ref"] = c["id"]  # the short ref is enough; the database id stays home
    cluster = export["cluster"]
    return strip_ids({"schema_version": SCHEMA_VERSION, "cluster": cluster, "events": export["events"],
                      "reports": reports, "dispatches": export["dispatches"], "resolutions": resolutions})


def first_time(s: dict) -> str:
    times = [r["created_at"] for r in s["reports"]] + [e["at"] for e in s["events"]]
    return min(times) if times else s["cluster"]["opened_at"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cluster_id")
    ap.add_argument("--name", required=True, help="folder name: lowercase letters, digits and dashes")
    ap.add_argument("--title-es", default="")
    ap.add_argument("--title-en", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not NAME.match(args.name):
        print("--name: lowercase letters, digits and dashes", file=sys.stderr)
        return 2
    load_env()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing in .env", file=sys.stderr)
        return 2
    db = Db(url, key)
    export = db.rpc("replay_export", {"p_cluster_id": args.cluster_id})
    if not export:
        print("no such cluster", file=sys.stderr)
        return 1
    folder = OUT / args.name
    if not args.dry_run:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.jpg"):
            old.unlink()
    photos = Photos(db, folder, args.dry_run)
    scenario = build(export, photos)
    scenario |= {"name": args.name,
                 "title": {"es": args.title_es or args.name, "en": args.title_en or args.title_es or args.name},
                 "run_started_at": first_time(scenario), "recorded_at": datetime.now(UTC).isoformat()}
    text = json.dumps(scenario, ensure_ascii=False, indent=1)
    if UUID.search(text):  # belt and braces: no database id may leave
        print("refusing: a database id is still in the scenario", file=sys.stderr)
        return 1
    c = scenario["cluster"]
    print(f"{args.name}: {c['category_id']} ({'sandbox' if c['is_sandbox'] else 'real map'}), "
          f"{len(scenario['reports'])} reports, {len(scenario['dispatches'])} briefs, "
          f"{len(scenario['resolutions'])} closures, {len(photos.files)} photos")
    if args.dry_run:
        return 0
    (folder / "scenario.json").write_text(text, encoding="utf-8")
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"scenarios": []}
    entry = {"name": args.name, "title": scenario["title"], "run_started_at": scenario["run_started_at"],
             "is_sandbox": c["is_sandbox"]}
    index["scenarios"] = [s for s in index["scenarios"] if s["name"] != args.name] + [entry]
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {folder.relative_to(ROOT)}/ and {index_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
