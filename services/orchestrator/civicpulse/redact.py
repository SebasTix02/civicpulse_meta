"""Mask personal data in citizen text before any model sees it (free API credits may be used by
Meta for product improvement). Patterns target Ecuador first: phones, cédulas, plates, emails."""

from __future__ import annotations

import re

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("[email]", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("[phone]", re.compile(r"(?<!\d)(?:\+?593[\s-]?|0)9\d(?:[\s-]?\d){7}(?!\d)")),   # mobile
    ("[phone]", re.compile(r"(?<!\d)(?:\+?593[\s-]?|0)[2-7](?:[\s-]?\d){7}(?!\d)")),  # landline
    ("[id]", re.compile(r"(?<!\d)\d{9}-?\d(?!\d)")),                                   # cédula
    ("[plate]", re.compile(r"\b[A-Z]{3}[\s-]?\d{3,4}\b", re.IGNORECASE)),
]


def redact(text: str) -> tuple[str, bool]:
    """Returns (redacted text, whether anything was masked)."""
    out = text
    for token, pattern in PATTERNS:
        out = pattern.sub(token, out)
    return out, out != text
