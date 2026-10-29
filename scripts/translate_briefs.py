"""Add the es <-> en translation to briefs written before translations existed.

    pip install -e services/orchestrator
    python scripts/translate_briefs.py            # every dispatch whose brief has no translation

Uses SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and the Muse keys from .env (never printed). One
Muse call per brief, as the brief stage now does for new ones (pipeline/brief.py). A brief whose
translation would change its citations is left as it is.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
sys.path.insert(0, str(ROOT / "scripts"))

from civicpulse.config import Settings
from civicpulse.db import Db
from civicpulse.llm.router import LLMRouter
from civicpulse.pipeline.brief import BriefStage
from smoke_models import load_env


def main() -> int:
    load_env()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing in .env", file=sys.stderr)
        return 2
    db = Db(url, key)
    stage = BriefStage(db, LLMRouter(Settings().llm_providers))
    rows = db.select("dispatches", {"select": "cluster_id,stakeholder_id,brief", "brief": "not.is.null"})
    todo = [r for r in rows if not (r["brief"] or {}).get("translations")]
    print(f"{len(todo)} of {len(rows)} briefs have no translation")
    for r in todo:
        result = stage.add_translation(f"{r['cluster_id']}:{r['stakeholder_id']}")
        print(f"  cluster {r['cluster_id'][:8]}: {result} ({r['brief'].get('title', '')[:60]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
