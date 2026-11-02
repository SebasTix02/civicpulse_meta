"""Deterministic grader for stage `spec` answers (text -> spec JSON). No LLM judge.

Used by the Muse Glimmer fine-tune (services/glimmer): the same checks score the keyword
fallback, the base model and the tuned model on held-out reports.

A row has the gold category (the one the synthetic report was written for) and the teacher's
spec (Muse Spark Open, kept only when it agreed with the gold category).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from pydantic import ValidationError

from civicpulse.llm.router import extract_json
from civicpulse.models import SpecLLMOutput

CHECKS = ("json_valid", "category", "severity_within_1", "personal_data", "prompts_when_visual")


@dataclass
class Grade:
    json_valid: bool
    category: bool
    severity_within_1: bool
    personal_data: bool
    prompts_when_visual: bool
    predicted_category: str | None
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def parse(text: str) -> SpecLLMOutput:
    return SpecLLMOutput.model_validate(extract_json(text))


def grade(answer: str | SpecLLMOutput, gold_category: str, teacher: dict, known: set[str] | None = None) -> Grade:
    """`answer` is the model's raw text (or an already parsed spec, e.g. the keyword fallback).
    An unknown category counts as "other", as in build_spec."""
    try:
        spec = answer if isinstance(answer, SpecLLMOutput) else parse(answer)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        return Grade(False, False, False, False, False, None, str(exc)[:200])
    cat = spec.category_id if known is None or spec.category_id in known else "other"
    return Grade(
        json_valid=True,
        category=cat == gold_category,
        severity_within_1=abs(spec.severity - int(teacher["severity"])) <= 1,
        personal_data=spec.contains_personal_data == bool(teacher["contains_personal_data"]),
        prompts_when_visual=(not spec.photo_verifiable) or len(spec.sam_prompts) > 0,
        predicted_category=cat,
    )


def summarize(grades: list[Grade]) -> dict[str, float]:
    """Share of rows passing each check (0..1), plus n."""
    n = len(grades)
    out: dict[str, float] = {"n": n}
    for check in CHECKS:
        out[check] = round(sum(getattr(g, check) for g in grades) / n, 3) if n else 0.0
    return out


def compact(spec: dict) -> str:
    """The assistant turn the model is trained to write: one line of JSON, fixed key order."""
    keys = ("category_id", "domain_id", "summary_en", "severity", "entities", "sam_prompts",
            "photo_verifiable", "confidence", "contains_personal_data")
    return json.dumps({k: spec.get(k) for k in keys if k in spec}, ensure_ascii=False, separators=(",", ":"))
