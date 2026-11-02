"""Risk signals in the report text, by keyword (Spanish and English).

They complement the model's risk_signals: the model may miss one, and when every provider is down
the keyword path still sees them. Each signal raises the case priority (migration 27,
app_config priority_rules); they never decide the category. Whole words, accents ignored.
"""

from __future__ import annotations

import re
import unicodedata

from civicpulse.models import RISK_SIGNALS

# Regular expressions over the lower-cased, accent-free text, each wrapped in word boundaries.
RULES: dict[str, tuple[str, ...]] = {
    "accident": (r"accidente?s?", r"choc(o|aron|ar|ado|ada)", r"choques?", r"se ca(yo|yeron|en)", r"volc(o|aron)",
                 r"atropell\w*", r"crash(ed)?", r"fell"),
    "injury": (r"herid[oa]s?", r"lesionad[oa]s?", r"lastimad[oa]s?", r"fractur\w*", r"hospitalizad[oa]s?",
               r"injur(ed|y|ies)", r"hurt"),
    "vehicle_damage": (r"llantas?", r"neumaticos?", r"suspension", r"(se|me|le) (dano|danio|reviento)",
                       r"(llanta|neumatico) reventad[oa]", r"tires?", r"rims?", r"damaged (my|the) car", r"flat tire"),
    "blocks_traffic": (r"trafico", r"congestion", r"atascos?", r"trancon(es)?", r"no se puede pasar", r"bloquea\w*",
                       r"esquivan", r"se desvian", r"traffic", r"blocks? the (road|street|lane)", r"swerv\w*"),
    "near_school": (r"escuelas?", r"colegios?", r"unidad educativa", r"jardin de infantes", r"guarderias?",
                    r"schools?", r"kindergarten"),
    "near_hospital": (r"hospital(es)?", r"clinicas?", r"centro de salud", r"subcentro", r"clinics?"),
    "night_hazard": (r"de noche", r"en la noche", r"por la noche", r"no se ve", r"oscur[oa]s?", r"oscuridad",
                     r"sin luz", r"at night", r"in the dark"),
    "flooding": (r"inund\w*", r"se llena de agua", r"charcos?", r"anegad[oa]s?", r"flood\w*", r"puddles?"),
    "recurring": (r"otra vez", r"de nuevo", r"volvio", r"ya (lo )?report\w*", r"hace (meses|anos)", r"again",
                  r"keeps coming back", r"reported (it )?before"),
}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


_PATTERNS = {sig: re.compile(r"\b(?:" + "|".join(words) + r")\b") for sig, words in RULES.items()}
assert set(_PATTERNS) == set(RISK_SIGNALS)


def keyword_signals(text: str) -> list[str]:
    folded = _fold(text or "")
    return [sig for sig in RISK_SIGNALS if _PATTERNS[sig].search(folded)]


def merge_signals(*lists: list[str]) -> list[str]:
    """Union in the canonical order."""
    found = {s for lst in lists for s in lst}
    return [s for s in RISK_SIGNALS if s in found]
