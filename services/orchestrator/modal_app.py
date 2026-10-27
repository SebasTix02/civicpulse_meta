"""Modal deployment: web endpoint for webhooks, background jobs and the 1-minute sweeper.

    modal deploy services/orchestrator/modal_app.py

Secrets (Modal dashboard): `civicpulse-supabase` (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
ORCHESTRATOR_WEBHOOK_SECRET, ORCHESTRATOR_OPS_SECRET) and `civicpulse-llm`
(BASETEN_API_KEY, META_MODEL_API_KEY; optional partner keys). Cost rules from plan §7: CPU only here,
min_containers=0, the GPU fallback for SAM lives in its own app.
"""

from __future__ import annotations

from pathlib import Path

import modal

_HERE = Path(__file__).resolve()
# Only meaningful on the deploying machine; inside the container this file is /root/modal_app.py
# and the local files below are already in the image.
ROOT = _HERE.parents[2] if len(_HERE.parents) > 2 else _HERE.parent

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi>=0.115", "pydantic>=2.8", "httpx>=0.27", "pyyaml>=6", "meta-sam-parser>=0.1")
    .env({"CATALOG_PATH": "/app/civic_catalog.json"})
    .add_local_file(ROOT / "packages/catalog/civic_catalog.json", "/app/civic_catalog.json")
    .add_local_python_source("civicpulse")
)

app = modal.App("civicpulse-orchestrator", image=image)
secrets = [modal.Secret.from_name("civicpulse-supabase"), modal.Secret.from_name("civicpulse-llm")]


def _pipeline():
    from civicpulse.catalog import Catalog
    from civicpulse.config import get_settings
    from civicpulse.db import Db
    from civicpulse.llm.router import LLMRouter
    from civicpulse.pipeline.intake import IntakePipeline

    s = get_settings()
    return IntakePipeline(Db(s.supabase_url, s.supabase_service_key), Catalog.load(s.catalog_path), LLMRouter(s.llm_providers))


@app.function(secrets=secrets, timeout=600, max_containers=4)
def run_job(kind: str, key: str) -> str:
    if kind in ("intake", "photo_replaced"):
        return _pipeline().run(key)
    raise NotImplementedError(f"job {kind}: closure day 8, brief day 7")


@app.function(secrets=secrets, min_containers=0, scaledown_window=60)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def api():
    from civicpulse.api import create_app

    return create_app(lambda kind, key: run_job.spawn(kind, key))


@app.function(secrets=secrets, schedule=modal.Cron("* * * * *"), timeout=55)
def sweeper() -> None:
    """Re-spawns reports stuck in a non-final status for more than 2 minutes (day 4)."""
