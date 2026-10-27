import json

import httpx
from meta_sam_parser._mask_codec import _encode_raster

from civicpulse.sam.service import MetaApiSAM


def record(obj: int, box: tuple[int, int, int, int], c: float, size=(100, 80)) -> str:
    x1, y1, x2, y2 = box
    mw, mh = x2 - x1 + 1, y2 - y1 + 1
    payload = _encode_raster([1] * (mw * mh), mw, mh)
    return (f"{obj}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={size[0]};h={size[1]};c={c}|>"
            f"<|mask;x=0;y=0;data={mh},{mw},{payload};c={c}|>")


def test_parse_filters_by_confidence_and_normalizes_box():
    text = "<0f>" + record(0, (10, 20, 29, 39), 0.93) + "," + record(1, (50, 10, 59, 19), 0.2)
    dets = MetaApiSAM.parse("pothole", text, threshold=0.4)
    assert len(dets) == 1
    d = dets[0]
    assert d.score == 0.93
    assert d.box_xywh == [0.1, 0.25, 0.2, 0.25]
    assert d.mask_rle["box_px"] == [10, 20, 30, 40] and d.mask_rle["size"] == [20, 20]


def test_no_match_is_empty():
    assert MetaApiSAM.parse("person", "", threshold=0.1) == []


def test_segment_sends_one_request_per_prompt():
    bodies = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        bodies.append(body)
        prompt = body["input"][0]["content"][0]["text"]
        text = "<0f>" + record(0, (10, 20, 29, 39), 0.9) if prompt == "pothole" else ""
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": text}]}]})

    sam = MetaApiSAM("k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = sam.segment(b"\xff\xd8fake", ["pothole", "person"])
    assert [b["model"] for b in bodies] == ["sam-3.1", "sam-3.1"]
    assert bodies[0]["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,")
    assert len(res.detections["pothole"]) == 1 and res.detections["person"] == []
    assert res.backend == "meta_api"
