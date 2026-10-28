"""Runtime settings, all from environment variables (Modal Secrets in production)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve()
# In the repo this file is services/orchestrator/civicpulse/config.py; in the Modal image it is
# /root/civicpulse/config.py, with no repo around it (CATALOG_PATH is set there instead).
REPO_ROOT = _HERE.parents[3] if len(_HERE.parents) > 3 else _HERE.parent


@dataclass(frozen=True)
class ProviderConfig:
    """One OpenAI-compatible endpoint in the LLMRouter chain."""

    name: str
    base_url: str
    model: str
    api_key_env: str
    vision: bool = True
    # Always-reasoning models (Muse Spark Open): reasoning tokens count against max_tokens, so
    # every call gets at least this budget, and reasoning_effort is sent to keep it short.
    reasoning_min_tokens: int = 0
    reasoning_effort: str | None = None

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


def _provider_chain() -> list[ProviderConfig]:
    """Muse-only chain. 2026-10-27: Muse Spark Open on Baseten first (hackathon credits, 2M
    tokens/min; the key only works with muse-spark-open-1026), then Muse Spark 1.3 on the Meta
    Model API (its credits are kept for SAM 3.1), then other Muse Spark Open hosts and Muse
    Glimmer. Keywords are the last link and are not a provider. LLM_PROVIDER_ORDER overrides."""
    env = os.environ.get
    open_reasoning = {"reasoning_min_tokens": int(env("REASONING_MIN_TOKENS", "4096")),
                      "reasoning_effort": env("REASONING_EFFORT", "low")}
    chain = [
        ProviderConfig("baseten_muse_spark_open", env("BASETEN_BASE", "https://inference.baseten.co/v1"),
                       env("BASETEN_MODEL", "meta-models/muse-spark-open-1026"), "BASETEN_API_KEY", **open_reasoning),
        # Muse Spark 1.3 also spends hidden reasoning tokens (smoke test 2026-10-27: 64 of 64, empty answer)
        ProviderConfig("meta_muse_spark", env("META_API_BASE", "https://api.meta.ai/v1"),
                       env("META_MODEL", "muse-spark-1.3"), "META_MODEL_API_KEY",
                       reasoning_min_tokens=int(env("META_REASONING_MIN_TOKENS", "4096"))),
        ProviderConfig("fireworks_muse_spark_open", env("FIREWORKS_BASE", "https://api.fireworks.ai/inference/v1"),
                       env("FIREWORKS_MODEL", "accounts/fireworks/models/muse-spark-open"), "FIREWORKS_API_KEY",
                       **open_reasoning),
        ProviderConfig("modal_muse_spark_open", env("MODAL_LLM_BASE", ""),
                       env("MODAL_LLM_MODEL", "muse-spark-open"), "MODAL_LLM_API_KEY", **open_reasoning),
        ProviderConfig("muse_glimmer", env("GLIMMER_BASE", ""), env("GLIMMER_MODEL", "muse-glimmer"), "GLIMMER_API_KEY"),
    ]
    order = env("LLM_PROVIDER_ORDER")
    if order:
        by_name = {p.name: p for p in chain}
        chain = [by_name[n.strip()] for n in order.split(",") if n.strip() in by_name]
    return [p for p in chain if p.base_url]


@dataclass(frozen=True)
class Settings:
    supabase_url: str = field(default_factory=lambda: os.environ.get("SUPABASE_URL", ""))
    supabase_service_key: str = field(default_factory=lambda: os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""))
    webhook_secret: str = field(default_factory=lambda: os.environ.get("ORCHESTRATOR_WEBHOOK_SECRET", ""))
    ops_secret: str = field(default_factory=lambda: os.environ.get("ORCHESTRATOR_OPS_SECRET", ""))
    sam_backend: str = field(default_factory=lambda: os.environ.get("SAM_BACKEND", "meta_api"))  # meta_api | modal_gpu | fixtures
    catalog_path: Path = field(
        default_factory=lambda: Path(os.environ.get("CATALOG_PATH", REPO_ROOT / "packages/catalog/civic_catalog.json")))
    llm_providers: list[ProviderConfig] = field(default_factory=_provider_chain)
    max_stage_attempts: int = 5

    def sam(self):
        """The SAMService for `sam_backend`. Only meta_api is wired; modal_gpu stays the plan B."""
        from civicpulse.sam.service import FixtureSAM, MetaApiSAM

        if self.sam_backend == "fixtures":
            return FixtureSAM(Path(os.environ.get("SAM_FIXTURES", REPO_ROOT / "fixtures/sam")))
        if self.sam_backend != "meta_api":
            raise RuntimeError(f"SAM_BACKEND={self.sam_backend} is not available yet")
        key = os.environ.get("META_MODEL_API_KEY")
        if not key:
            raise RuntimeError("META_MODEL_API_KEY missing: SAM 3.1 runs on the Meta Model API")
        return MetaApiSAM(key, os.environ.get("META_API_BASE", "https://api.meta.ai/v1"))


def get_settings() -> Settings:
    return Settings()
