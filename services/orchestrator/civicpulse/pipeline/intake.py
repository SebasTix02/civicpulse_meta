"""Ingestion flow A (plan §3.3): received -> spec_ready -> vision_done -> clustered.

Every stage is idempotent: it is skipped when stage_runs already has an `ok` row for the
same (report_id, photo_version, stage), and status moves use compare-and-set. Duplicate
webhooks, sweeper retries and /ops/reprocess are all safe.

Built so far: the `spec` stage. Vision (day 2/4), cluster (day 5) and brief (day 7) are
stubs that raise, so the sweeper leaves the report where it is instead of faking a result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from civicpulse.catalog import Catalog
from civicpulse.db import Db
from civicpulse.llm.prompts import spec_system
from civicpulse.llm.router import AllProvidersFailed, LLMRouter
from civicpulse.models import SpecLLMOutput
from civicpulse.redact import redact

KEYWORD_PREFERRED_BELOW = 0.40


@dataclass
class SpecOutcome:
    spec: dict
    provider: str
    model: str
    latency_ms: int


def build_spec(text: str, catalog: Catalog, router: LLMRouter) -> SpecOutcome:
    """Text -> validated spec merged with the catalog. Falls back to keywords if every
    provider fails, or if the model is unsure and the keywords found a category."""
    started = time.monotonic()
    keyword_cat = catalog.keyword_match(text)
    safe_text, _ = redact(text)  # the model never sees phones, ids, plates or emails
    try:
        res = router.structured(spec_system(catalog.categories), safe_text, SpecLLMOutput)
        llm: SpecLLMOutput = res.data  # type: ignore[assignment]
        if llm.category_id not in catalog.categories:
            llm = llm.model_copy(update={"category_id": "other"})
        if llm.confidence < KEYWORD_PREFERRED_BELOW and keyword_cat:
            llm = llm.model_copy(update={"category_id": keyword_cat})
            return SpecOutcome(catalog.merge_spec(llm, "keywords_over_low_confidence"), res.provider, res.model, res.latency_ms)
        return SpecOutcome(catalog.merge_spec(llm, res.provider), res.provider, res.model, res.latency_ms)
    except AllProvidersFailed:
        cat_id = keyword_cat or "other"
        fallback = SpecLLMOutput(
            category_id=cat_id, summary_en=f"Citizen report classified by keywords as {cat_id}", severity=3,
            sam_prompts=[], photo_verifiable=cat_id != "other", confidence=0.5 if keyword_cat else 0.0,
            contains_personal_data=True,  # unknown: assume yes, so raw text is never shown outside the device
        )
        ms = int((time.monotonic() - started) * 1000)
        return SpecOutcome(catalog.merge_spec(fallback, "keywords"), "keywords", "catalog", ms)


class IntakePipeline:
    def __init__(self, db: Db, catalog: Catalog, router: LLMRouter):
        self.db, self.catalog, self.router = db, catalog, router

    def run(self, report_id: str) -> str:
        report = self.db.select_one("reports", id=report_id)
        if report is None:
            return "missing"
        if report["status"] == "received":
            self.stage_spec(report)
            report = self.db.select_one("reports", id=report_id)
        if report["status"] == "spec_ready":
            raise NotImplementedError("stage sam_ground/classify: day 2 and day 4")
        return report["status"]

    def stage_spec(self, report: dict) -> None:
        version = report["current_photo_version"]
        if self.db.stage_done("spec", report_id=report["id"], photo_version=version):
            return
        raw = report["raw_text"] or ""
        redacted, _ = redact(raw)
        out = build_spec(raw, self.catalog, self.router)
        domain = self._domain_for(out.spec["category_id"])
        moved = self.db.compare_and_set("reports", report["id"], "received", {
            "status": "spec_ready",
            "spec": out.spec,
            "category_id": out.spec["category_id"],
            "domain_id": domain,
            "catalog_version": 1,
            "severity": out.spec["severity"],
            "raw_text_redacted": redacted,
            "model_versions": {"spec": f"{out.provider}:{out.model}"},
        })
        self.db.insert("stage_runs", {
            "report_id": report["id"], "photo_version": version, "stage": "spec",
            "status": "ok" if moved else "skipped", "provider": out.provider, "model": out.model,
            "latency_ms": out.latency_ms,
        })

    def _domain_for(self, category_id: str) -> str | None:
        row = self.db.select_one("domain_categories", category_id=category_id)
        return row["domain_id"] if row else None
