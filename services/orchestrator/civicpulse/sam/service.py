"""SAMService: one interface, two backends (plan §3.2, §5.3).

- MetaApiSAM: SAM 3.1 through the Meta Model API. Primary if the day-1 test reproduces the
  notebook (pothole ~0.93, binary masks, scores) at a reasonable price per request.
- ModalGpuSAM: official facebookresearch/sam3 code on a Modal L4 (services/sam_gpu). Fallback.
- FixtureSAM: recorded results keyed by sha256(image) + prompt, for tests, sandbox samples
  and replay mode.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx


@dataclass
class Detection:
    prompt: str
    score: float
    box_xywh: list[float]  # normalized 0..1
    mask_rle: dict | None = None  # COCO RLE (MetaApiSAM: box-local, with box_px); decoded on the CPU side when needed
    box_px: list[int] | None = None  # [x1, y1, x2, y2] in image pixels, x2/y2 exclusive
    mask_local: tuple[bytes, int, int] | None = field(default=None, repr=False)  # raster 0/1 bytes, width, height of the box


@dataclass
class SegmentResult:
    detections: dict[str, list[Detection]] = field(default_factory=dict)
    backend: str = ""
    latency_ms: int = 0


class SAMService(Protocol):
    def segment(self, image_jpeg: bytes, prompts: list[str], threshold: float = 0.4) -> SegmentResult: ...


class MetaApiSAM:
    """SAM 3.1 on the Meta Model API (dev.meta.ai/docs/sam/segmenting): POST /v1/responses with
    model sam-3.1, one concept per request (lists in one prompt are unreliable), the image as a
    base64 data URL. The answer is a text stream of records `<0f>N<|box;x1..;w;h;c|><|mask;..|>`
    parsed with Meta's meta-sam-parser. Masks are box-local rasters; they are kept as COCO RLE
    plus the box in pixels, and decoded on the CPU side only when a stage needs pixels."""

    def __init__(self, api_key: str, base_url: str = "https://api.meta.ai/v1", model: str = "sam-3.1",
                 client: httpx.Client | None = None):
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model
        self.client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=5.0))

    def _ask(self, image_url: str, prompt: str) -> dict:
        resp = self.client.post(
            f"{self.base_url}/responses",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "stream": False,
                  "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
                  "input": [{"type": "message", "role": "user", "content": [
                      {"type": "input_text", "text": prompt},
                      {"type": "input_image", "image_url": image_url}]}]},
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def output_text(answer: dict) -> str:
        if isinstance(answer.get("output_text"), str):
            return answer["output_text"]
        return "".join(part.get("text", "") for item in answer.get("output", [])
                       for part in item.get("content", []) or [] if part.get("type") == "output_text")

    @staticmethod
    def parse(prompt: str, text: str, threshold: float) -> list[Detection]:
        from meta_sam_parser import (
            CompletedOutcome,
            decode_mask_to_raster,
            decode_mask_to_rle,
            image_segmentation_format,
        )

        parser = image_segmentation_format().create_parser()
        parser.push(text if text.endswith("\n") else text + "\n", emit=False)
        records = parser.finish(CompletedOutcome()).result.records
        size = re.search(r";w=(\d+);h=(\d+)", text)
        if not size:
            return []
        width, height = int(size.group(1)), int(size.group(2))
        out = []
        for r in records:
            if r.kind != "mask":
                continue
            score = r.confidence if r.confidence is not None else 1.0  # API omitted c: keep, flagged in rle
            if score < threshold:
                continue
            b = r.bounds
            rle = decode_mask_to_rle(r.mask)
            out.append(Detection(
                prompt=prompt, score=round(score, 4),
                box_xywh=[b.left / width, b.top / height, (b.right - b.left) / width, (b.bottom - b.top) / height],
                mask_rle={"size": list(rle.size), "counts": rle.counts, "box_px": [b.left, b.top, b.right, b.bottom],
                          "image_px": [width, height], "box_local": True, "score_reported": r.confidence is not None},
                box_px=[int(b.left), int(b.top), int(b.right), int(b.bottom)],
                mask_local=(bytes(decode_mask_to_raster(r.mask)), int(r.mask.width), int(r.mask.height)),
            ))
        return out

    def segment(self, image_jpeg: bytes, prompts: list[str], threshold: float = 0.4) -> SegmentResult:
        started = time.monotonic()
        image_url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()
        out = SegmentResult(backend="meta_api")
        for prompt in prompts:  # billed per request: callers keep the prompt budget small
            out.detections[prompt] = self.parse(prompt, self.output_text(self._ask(image_url, prompt)), threshold)
        out.latency_ms = int((time.monotonic() - started) * 1000)
        return out


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
