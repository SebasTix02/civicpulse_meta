"""Prompts. Everything a model reads is English (plan §3.8); citizens write in any language."""

from __future__ import annotations

import json

SPEC_SYSTEM = """You turn a citizen report about a problem in a city into a JSON spec.
Rules:
- category_id must be one id from the catalog list below, or "other".
- summary_en: one short English sentence with the problem and the place. Never copy personal data.
- severity: 1 (minor) to 5 (danger to life).
- sam_prompts: up to 4 short English noun phrases (1 to 5 lowercase words) describing what the
  problem looks like in a photo, e.g. "pothole", "people waiting at curb".
- photo_verifiable: whether a photo could show this problem.
- confidence: 0 to 1, how sure you are of the category.
- contains_personal_data: true if the text has names, phone numbers, plates or ids.
Answer with JSON only, no prose. Keys: category_id, domain_id, summary_en, severity, entities,
sam_prompts, photo_verifiable, confidence, contains_personal_data.

Catalog:
{catalog}"""


def spec_system(categories: dict[str, dict]) -> str:
    lines = [
        json.dumps({"id": cid, "name": c["display_name"]["en"], "means": c.get("issue_description") or ""})
        for cid, c in categories.items()
        if c.get("status", "active") == "active"
    ]
    return SPEC_SYSTEM.format(catalog="\n".join(lines))


LABEL_GLOSSARY = (
    "issue_present = the reported problem is visible; "
    "no_issue = the reported problem is not visible (absent, or fixed as described); "
    "shadow = a cast shadow makes it impossible to judge; "
    "water_or_wet_surface = water or a wet surface hides it; "
    "occlusion = a vehicle, person or object covers it; "
    "not_visible = out of frame, too small or blurry; "
    "uncertain = you cannot decide."
)


def problem_text(spec: dict) -> str:
    return (f"Report summary: {spec.get('summary_en', '')}. Reported problem: {spec['category_id']}, meaning "
            f"{spec.get('issue_description') or 'the problem described in the report'}. {spec.get('vlm_hints') or ''}")


def crop_check_prompt(spec: dict) -> str:
    return ("You are a municipal field inspector checking a citizen report against the photo they sent. "
            + problem_text(spec)
            + " A segmentation model outlined a candidate region in magenta. Judge ONLY what is inside the outline. "
            + "Labels: " + LABEL_GLOSSARY + " Use issue_present only if you clearly see the reported problem. "
            + 'Answer ONLY with JSON: {"label": "...", "confidence": 0.0, "reason": "short phrase"}')


def pair_check_prompt(spec: dict) -> str:
    return ("You are a municipal field inspector checking whether a reported problem was fixed. "
            + problem_text(spec)
            + f" Fixed means: {spec.get('resolved_description') or 'the problem is gone'}. "
            + "Image 1 is BEFORE, image 2 is AFTER, aligned to the same place; judge only the magenta outline. "
            + "Labels: " + LABEL_GLOSSARY + " "
            + 'Answer ONLY with JSON: {"before": {"label": "...", "confidence": 0.0}, '
            + '"after": {"label": "...", "confidence": 0.0}, "reason": "short phrase"}')
