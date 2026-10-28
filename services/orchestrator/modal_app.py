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


def _fetch_anon_models() -> None:
    """Runs once at image build: the two detectors of civicpulse/anonymize.py, sha256-checked."""
    import hashlib
    import urllib.request

    zoo = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
    for name, url, digest in [
        ("yunet.onnx", f"{zoo}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
         "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
        ("nanodet.onnx", f"{zoo}/object_detection_nanodet/object_detection_nanodet_2022nov.onnx",
         "4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186"),
    ]:
        path = Path("/models") / name
        path.parent.mkdir(exist_ok=True)
        urllib.request.urlretrieve(url, path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"{name}: sha256 mismatch"


image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi>=0.115", "pydantic>=2.8", "httpx>=0.27", "pyyaml>=6", "meta-sam-parser>=0.1",
                 "numpy>=1.26", "opencv-python-headless>=4.10,<5", "pillow>=10")
    .run_function(_fetch_anon_models)
    .env({"CATALOG_PATH": "/app/civic_catalog.json", "ANON_MODELS_DIR": "/models"})
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
    from civicpulse.pipeline.vision import VisionStage

    s = get_settings()
    db, router = Db(s.supabase_url, s.supabase_service_key), LLMRouter(s.llm_providers)
    return IntakePipeline(db, Catalog.load(s.catalog_path), router, VisionStage(db, router, s.sam(), _anonymizer()))


_ANONYMIZER = None


def _anonymizer():
    """Loaded once per container: the detectors stay warm between reports."""
    global _ANONYMIZER
    if _ANONYMIZER is None:
        import os

        from civicpulse.anonymize import Anonymizer

        _ANONYMIZER = Anonymizer(Path(os.environ.get("ANON_MODELS_DIR", "/models")))
    return _ANONYMIZER


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
