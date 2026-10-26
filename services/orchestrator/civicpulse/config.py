"""Runtime settings, all from environment variables (Modal Secrets in production)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ProviderConfig:
    """One OpenAI-compatible endpoint in the LLMRouter chain."""

    name: str
    base_url: str
    model: str
    api_key_env: str
    vision: bool = True

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


def _provider_chain() -> list[ProviderConfig]:
    """Muse-only chain, decided 2026-10-26: Muse Spark 1.3 on the Meta Model API first, then
    Muse Spark Open on partner clouds, then Muse Glimmer. Keywords are the last link and are
    not a provider. Model ids for partner clouds are confirmed on day 1 and set by env."""
    env = os.environ.get
    chain = [
        ProviderConfig("meta_muse_spark", env("META_API_BASE", "https://api.meta.ai/v1"),
                       env("META_MODEL", "muse-spark-1.3"), "META_MODEL_API_KEY"),
        ProviderConfig("baseten_muse_spark_open", env("BASETEN_BASE", "https://inference.baseten.co/v1"),
                       env("BASETEN_MODEL", "muse-spark-open"), "BASETEN_API_KEY"),
        ProviderConfig("fireworks_muse_spark_open", env("FIREWORKS_BASE", "https://api.fireworks.ai/inference/v1"),
                       env("FIREWORKS_MODEL", "accounts/fireworks/models/muse-spark-open"), "FIREWORKS_API_KEY"),
        ProviderConfig("modal_muse_spark_open", env("MODAL_LLM_BASE", ""),
                       env("MODAL_LLM_MODEL", "muse-spark-open"), "MODAL_LLM_API_KEY"),
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


def get_settings() -> Settings:
    return Settings()
