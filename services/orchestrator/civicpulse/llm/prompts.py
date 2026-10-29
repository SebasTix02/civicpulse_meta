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


def whole_image_check_prompt(spec: dict) -> str:
    """Asked once when no outlined region was confirmed: is the problem anywhere in the photo?"""
    return ("You are a municipal field inspector checking a citizen report against the photo they sent. "
            + problem_text(spec)
            + " Look at the whole photo. Faces, people and vehicles may be blurred for privacy; ignore the blur. "
            + "Labels: " + LABEL_GLOSSARY + " Use issue_present only if you clearly see the reported problem. "
            + 'Answer ONLY with JSON: {"label": "...", "confidence": 0.0, "reason": "short phrase, say where"}')


def pair_check_prompt(spec: dict) -> str:
    return ("You are a municipal field inspector checking whether a reported problem was fixed. "
            + problem_text(spec)
            + f" Fixed means: {spec.get('resolved_description') or 'the problem is gone'}. "
            + "Image 1 is BEFORE, image 2 is AFTER, aligned to the same place; judge only the magenta outline. "
            + "Labels: " + LABEL_GLOSSARY + " "
            + 'Answer ONLY with JSON: {"before": {"label": "...", "confidence": 0.0}, '
            + '"after": {"label": "...", "confidence": 0.0}, "reason": "short phrase"}')


BRIEF_PROFILES = {
    "operational": "a field operations team: what, where, how much and what to do first. Short and concrete.",
    "technical": "engineers: include the measured numbers and how sure the evidence is.",
    "citizen": "residents: plain words, no jargon, no blame.",
    "press": "journalists: neutral and factual, no blame, no speculation.",
}
BRIEF_LANGS = {"es": "Spanish (Ecuador)", "en": "English"}


def brief_system(profile: str, lang: str, actions: list[str]) -> str:
    """The context is facts with citation ids; every claim must cite them (validator in brief.py)."""
    return (
        "You write a short brief for a city stakeholder about a problem that several citizens reported "
        "and that CivicPulse validated by consensus. Readers: " + BRIEF_PROFILES.get(profile, BRIEF_PROFILES["operational"])
        + f" Write title and body_markdown in {BRIEF_LANGS.get(lang, 'Spanish (Ecuador)')}; JSON keys stay English.\n"
        "Rules:\n"
        "- Use ONLY the facts in the context JSON. Do not invent places, causes, dates, people or numbers.\n"
        "- End every factual sentence with its citation ids in square brackets, e.g. [m_report_count] or "
        "[r_1a2b3c4d] [c_9f8e7d6c]. Only ids that appear in the context exist.\n"
        "- body_markdown: 3 to 8 sentences, at most 2 short paragraphs or a short list. No headings.\n"
        "- Say how strong the evidence is (devices, photo checks) and what is not known.\n"
        "- actions: 1 to 3 items; action_id must be one of: " + ", ".join(actions) + ". Each with a one-sentence "
        "rationale in the same language and the citation ids that support it.\n"
        'Answer ONLY with JSON: {"title": "...", "body_markdown": "...", '
        '"actions": [{"action_id": "...", "rationale": "...", "citations": ["m_report_count"]}]}'
    )


def brief_translate_system(src: str, dst: str) -> str:
    return (
        f"Translate a stakeholder brief from {BRIEF_LANGS.get(src, src)} to {BRIEF_LANGS.get(dst, dst)}. "
        "Translate faithfully: add nothing, drop nothing, keep numbers and dates as they are. "
        "Keep every citation in square brackets exactly as written and in the same place, e.g. [m_report_count]. "
        "rationales: the action rationales in the same order, translated the same way.\n"
        'Answer ONLY with JSON: {"title": "...", "body_markdown": "...", "rationales": ["..."]}'
    )
