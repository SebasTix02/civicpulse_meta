"""Pydantic models mirroring packages/contracts/schemas/*.schema.json.

The JSON Schemas are the contract; these models are how the orchestrator enforces it.
tests/test_contracts.py checks that both agree on required keys and enums.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SAM_PROMPT = re.compile(r"^[a-z][a-z -]{0,39}$")

IntakeStatus = Literal[
    "REPORT_CONFIRMED_BY_PHOTO",
    "ISSUE_VISIBLE_NOT_LOCALIZED_REQUIRES_REVIEW",
    "CANDIDATE_REGIONS_FOUND_REQUIRES_REVIEW",
    "NOT_VERIFIABLE_ISSUE_NOT_FOUND_IN_PHOTO",
    "NOT_VERIFIABLE_NO_VISUAL_SPEC",
    "NOT_PHOTO_VERIFIABLE_CATEGORY",
]

RegionStatus = Literal[
    "PERSISTENT_ISSUE",
    "NOT_VERIFIABLE_REGION_NOT_VISIBLE",
    "NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION",
    "NOT_VERIFIABLE_REPAIR_VS_CONFUSER_CONFLICT",
    "NOT_VERIFIABLE_CLASSIFIER_UNAVAILABLE",
    "NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED",
    "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE",
    "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW",
]

ClassifierLabel = Literal["issue_present", "no_issue", "shadow", "water_or_wet_surface", "occlusion", "not_visible", "uncertain"]


class Entities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line: str | None = Field(default=None, max_length=60)
    landmark: str | None = Field(default=None, max_length=120)
    street: str | None = Field(default=None, max_length=120)
    time_bucket: Literal["early_morning", "peak_morning", "midday", "peak_evening", "night", "unknown"] | None = None
    people_affected_hint: int | None = Field(default=None, ge=0, le=100000)


class SpecLLMOutput(BaseModel):
    """Raw answer of the text model for stage `spec` (spec_llm_output.schema.json)."""

    model_config = ConfigDict(extra="forbid")
    category_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,47}$")
    domain_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,31}$")
    summary_en: str = Field(min_length=3, max_length=300)
    severity: int = Field(ge=1, le=5)
    entities: Entities | None = None
    sam_prompts: list[str] = Field(max_length=4)
    photo_verifiable: bool
    confidence: float = Field(ge=0, le=1)
    contains_personal_data: bool

    @field_validator("sam_prompts", mode="before")
    @classmethod
    def drop_bad_prompts(cls, value: list) -> list[str]:
        # Phrases that fail the pattern are dropped, not the whole answer (contract).
        if not isinstance(value, list):
            return value
        return [p.strip().lower() for p in value if isinstance(p, str) and SAM_PROMPT.match(p.strip().lower())][:4]


class ClassifierVerdict(BaseModel):
    label: ClassifierLabel
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class PairVerdict(BaseModel):
    """Classifier answer on a before/after pair of aligned crops."""

    before: ClassifierVerdict
    after: ClassifierVerdict
    valid: bool = True
    error: str | None = None


class RegionMetrics(BaseModel):
    """Geometry of one before-region after the after-photo is warped onto it (all 0..1)."""

    region_visible_after: float
    remaining_issue_ratio: float
    resolved_evidence_coverage: float
    confuser_region_coverage: float
    confuser_matches_resolved_iou: float
