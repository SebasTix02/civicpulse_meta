"""Photos excluded by hand (banned.txt): never downloaded, packed, anonymized or evaluated."""

from __future__ import annotations

from pathlib import Path

BANNED_FILE = Path(__file__).with_name("banned.txt")


def banned_stems() -> set[str]:
    """{"bach_0027_after", ...}: file names without extension."""
    lines = BANNED_FILE.read_text(encoding="utf-8").splitlines() if BANNED_FILE.exists() else []
    return {s for line in lines if (s := line.split("#", 1)[0].strip())}
