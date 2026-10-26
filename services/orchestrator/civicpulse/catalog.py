"""Category catalog: loading, keyword fallback and merging the LLM spec with the catalog row."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from civicpulse.models import SpecLLMOutput


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


@dataclass
class Catalog:
    version: str
    categories: dict[str, dict]

    @classmethod
    def load(cls, path: Path) -> Catalog:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(version=data["version"], categories={c["id"]: c for c in data["categories"]})

    def active_ids(self) -> list[str]:
        return [cid for cid, c in self.categories.items() if c.get("status", "active") == "active"]

    def keyword_match(self, text: str) -> str | None:
        """Last link of the router for text: whole-word keyword match, any language in the catalog.
        Ties go to the lowest keyword_priority, then the longest keyword."""
        folded = _fold(text)
        best: tuple[int, int, str] | None = None
        for cid, cat in self.categories.items():
            if cid == "other":
                continue
            for words in cat.get("keywords", {}).values():
                for kw in words:
                    if re.search(rf"\b{re.escape(_fold(kw))}\b", folded):
                        key = (cat.get("keyword_priority", 0), -len(kw), cid)
                        if best is None or key < best:
                            best = key
        return best[2] if best else None

    def merge_spec(self, llm: SpecLLMOutput, source: str) -> dict:
        """Report spec = catalog row + what the model added. Model prompts never replace the catalog's."""
        cat = self.categories.get(llm.category_id) or self.categories["other"]
        verifiability = cat["photo_verifiability"]
        if cat["id"] == "other" and not llm.photo_verifiable:
            verifiability = "none"
        targets = list(dict.fromkeys(cat.get("target_prompts", []) + llm.sam_prompts))
        return {
            "category_id": cat["id"],
            "catalog_version": self.version,
            "source": source,
            "summary_en": llm.summary_en,
            "severity": llm.severity,
            "entities": llm.entities.model_dump(exclude_none=True) if llm.entities else {},
            "photo_verifiability": verifiability,
            "context_prompts": cat.get("context_prompts", []),
            "target_prompts": targets,
            "extra_target_prompts": cat.get("extra_target_prompts", []),
            "resolved_prompts": cat.get("resolved_prompts", []),
            "confuser_prompts": cat.get("confuser_prompts", []),
            "issue_can_be_global": cat.get("issue_can_be_global", False),
            "issue_description": cat.get("issue_description"),
            "resolved_description": cat.get("resolved_description"),
            "vlm_hints": cat.get("vlm_hints"),
            "confidence": llm.confidence,
            "contains_personal_data": llm.contains_personal_data,
        }
