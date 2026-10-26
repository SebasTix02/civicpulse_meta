"""SAMService: one interface, two backends (plan §3.2, §5.3).

- MetaApiSAM: SAM 3.1 through the Meta Model API. Primary if the day-1 test reproduces the
  notebook (pothole ~0.93, binary masks, scores) at a reasonable price per prompt.
- ModalGpuSAM: official facebookresearch/sam3 code on a Modal L4 (services/sam_gpu). Fallback.
- FixtureSAM: recorded results keyed by sha256(image) + prompt, for tests, sandbox samples
  and replay mode.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Detection:
    prompt: str
    score: float
    box_xywh: list[float]  # normalized 0..1
    mask_rle: dict | None = None  # COCO RLE; decoded on the CPU side when needed


@dataclass
class SegmentResult:
    detections: dict[str, list[Detection]] = field(default_factory=dict)
    backend: str = ""
    latency_ms: int = 0


class SAMService(Protocol):
    def segment(self, image_jpeg: bytes, prompts: list[str], threshold: float = 0.4) -> SegmentResult: ...


class MetaApiSAM:
    """TODO day 1: confirm endpoint, request shape and billing per prompt against the docs
    at dev.meta.ai/models/sam-3-1, then implement. Kept unimplemented on purpose so nothing
    silently returns empty masks."""

    def __init__(self, api_key: str, base_url: str = "https://api.meta.ai/v1"):
        self.api_key, self.base_url = api_key, base_url

    def segment(self, image_jpeg: bytes, prompts: list[str], threshold: float = 0.4) -> SegmentResult:
        raise NotImplementedError("MetaApiSAM.segment: implement after the day-1 API test")


class FixtureSAM:
    """Reads fixtures/<sha256>.json written by scripts/record_fixtures.py from real runs."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def segment(self, image_jpeg: bytes, prompts: list[str], threshold: float = 0.4) -> SegmentResult:
        digest = hashlib.sha256(image_jpeg).hexdigest()
        path = self.root / f"{digest}.json"
        if not path.exists():
            raise KeyError(f"no SAM fixture for image {digest[:12]}")
        data = json.loads(path.read_text())
        out = SegmentResult(backend="fixtures")
        for p in prompts:
            out.detections[p] = [Detection(**d) for d in data.get(p, []) if d["score"] >= threshold]
        return out
