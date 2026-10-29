import json

import cv2
import httpx
import jsonschema
import numpy as np
import pytest

from civicpulse.anonymize import Anonymized, to_jpeg
from civicpulse.catalog import Catalog
from civicpulse.config import REPO_ROOT, ProviderConfig
from civicpulse.llm.router import LLMRouter
from civicpulse.models import SpecLLMOutput
from civicpulse.pipeline.closure import ClosureStage, decide_closure, decide_region, location_check, verify_pair
from civicpulse.registration import register
from civicpulse.sam.service import Detection, SegmentResult

CATALOG = Catalog.load(REPO_ROOT / "packages/catalog/civic_catalog.json")
SPEC = CATALOG.merge_spec(SpecLLMOutput(
    category_id="pothole", summary_en="Pothole on the street", severity=3, sam_prompts=[], photo_verifiable=True,
    confidence=0.9, contains_personal_data=False), "test")
SCHEMA = json.loads((REPO_ROOT / "packages/contracts/schemas/closure_verification.schema.json").read_text())
H, W = 600, 800
HOLE = [300, 200, 400, 280]          # pothole box in BEFORE
SHIFT = (24, 12)                     # the AFTER shot is moved by this much


def scene(seed=1):
    rng = np.random.default_rng(seed)
    img = cv2.resize((rng.random((60, 80, 3)) * 255).astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST)
    img = cv2.GaussianBlur(img, (0, 0), 1.2)
    for _ in range(60):  # corners for SIFT
        x, y = int(rng.integers(0, W - 40)), int(rng.integers(0, H - 40))
        cv2.rectangle(img, (x, y), (x + int(rng.integers(8, 40)), y + int(rng.integers(8, 40))),
                      tuple(int(c) for c in rng.integers(0, 255, 3)), -1)
    return img


def shifted(img):
    m = np.float32([[1, 0, SHIFT[0]], [0, 1, SHIFT[1]]])
    return cv2.warpAffine(img, m, (W, H), borderMode=cv2.BORDER_REFLECT)


def anon(rgb) -> Anonymized:
    return Anonymized(to_jpeg(rgb, 92), rgb.shape[1], rgb.shape[0], faces=0, people=0, vehicles=0, blurred_share=0.0)


def moved(box):
    return [box[0] + SHIFT[0], box[1] + SHIFT[1], box[2] + SHIFT[0], box[3] + SHIFT[1]]


class FakeSAM:
    """Hits per photo: {jpeg: {prompt: [(score, box_px)]}}."""

    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def segment(self, image_jpeg, prompts, threshold=0.4):
        self.calls.append(list(prompts))
        out = SegmentResult(backend="fake")
        for p in prompts:
            out.detections[p] = [Detection(p, s, [0, 0, 0, 0], box_px=b, mask_local=(bytes([1] * 4), 2, 2))
                                 for s, b in self.hits.get(image_jpeg, {}).get(p, []) if s >= threshold]
        return out


def router(answers, monkeypatch):
    monkeypatch.setenv("K1", "x")
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        status, content = answers.pop(0) if answers else (500, "")
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    return LLMRouter([ProviderConfig("a", "https://a.test/v1", "m", "K1")],
                     client=httpx.Client(transport=httpx.MockTransport(handler))), seen


def pair(before_label, before_conf, after_label, after_conf):
    return 200, json.dumps({"before": {"label": before_label, "confidence": before_conf},
                            "after": {"label": after_label, "confidence": after_conf}, "reason": "x"})


def test_registration_recovers_the_shift_and_refuses_another_place():
    img = scene()
    reg = register(img, shifted(img))
    assert reg.ok and reg.inliers >= 25
    assert np.allclose(reg.H[:2, 2], [-SHIFT[0], -SHIFT[1]], atol=1.5)  # AFTER -> BEFORE undoes the shift
    other = register(img, scene(seed=7))
    assert not other.ok and other.reason


def test_fixed_pothole_is_only_a_candidate_for_human_review(monkeypatch):
    before, after = anon(scene()), anon(shifted(scene()))
    sam = FakeSAM({before.jpeg: {"pothole": [(0.8, HOLE)]},
                   after.jpeg: {"asphalt patch": [(0.7, moved(HOLE))]}})
    r, seen = router([pair("issue_present", 0.9, "no_issue", 0.9)], monkeypatch)
    res = verify_pair(before, after, SPEC, sam, r)
    v = res.verification
    check = v["region_checks"][0]
    assert v["ai_status"] == "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW"
    assert check["region_visible_after"] == 1.0 and check["remaining_issue_ratio"] == 0.0
    assert check["resolved_evidence_coverage"] > 0.9
    assert len(seen) == 1 and len(seen[0]["messages"][1]["content"]) == 4  # both crops in one call
    assert sam.calls[1] == ["pothole", "road crack", "asphalt patch", "repaired pothole", "shadow", "wet asphalt"]
    assert {"r1_before", "r1_after", "aligned"} <= set(res.figures)
    jsonschema.validate(v | {"before_report_id": "00000000-0000-4000-8000-000000000001",
                             "before_photo_id": "00000000-0000-4000-8000-000000000002",
                             "after_photo_id": "00000000-0000-4000-8000-000000000003"}, SCHEMA)


def test_same_photo_as_after_persists_without_asking_the_classifier(monkeypatch):
    before = anon(scene())
    sam = FakeSAM({before.jpeg: {"pothole": [(0.8, HOLE)]}})  # the very same photo: SAM sees it again
    r, seen = router([], monkeypatch)
    v = verify_pair(before, before, SPEC, sam, r).verification
    assert v["ai_status"] == "PERSISTENT_ISSUE" and not seen


def test_another_place_is_not_verifiable(monkeypatch):
    before, after = anon(scene()), anon(scene(seed=7))
    sam = FakeSAM({before.jpeg: {"pothole": [(0.8, HOLE)]}})
    r, seen = router([], monkeypatch)
    v = verify_pair(before, after, SPEC, sam, r).verification
    assert v["ai_status"] == "NOT_VERIFIABLE_REGION_NOT_VISIBLE" and not v["registration"]["ok"] and not seen


def test_no_pothole_found_before_cannot_close(monkeypatch):
    before, after = anon(scene()), anon(shifted(scene()))
    r, _ = router([], monkeypatch)
    v = verify_pair(before, after, SPEC, FakeSAM({}), r).verification
    assert v["ai_status"] == "NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED"


@pytest.mark.parametrize(("verdict", "expected"), [
    (pair("issue_present", 0.9, "issue_present", 0.6), "PERSISTENT_ISSUE"),
    (pair("issue_present", 0.9, "uncertain", 0.5), "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"),
    (pair("issue_present", 0.9, "no_issue", 0.7), "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"),  # not sure enough
    (pair("no_issue", 0.9, "no_issue", 0.9), "NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED"),
    (pair("issue_present", 0.9, "shadow", 0.9), "NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION"),
    ((500, ""), "NOT_VERIFIABLE_CLASSIFIER_UNAVAILABLE"),  # down, and SAM saw no repair either
])
def test_the_classifier_never_closes_on_doubt(monkeypatch, verdict, expected):
    before, after = anon(scene()), anon(shifted(scene()))
    sam = FakeSAM({before.jpeg: {"pothole": [(0.8, HOLE)]}})
    r, _ = router([verdict], monkeypatch)
    assert verify_pair(before, after, SPEC, sam, r).verification["ai_status"] == expected


def test_decision_order():
    base = {"region_visible_after": 1.0, "remaining_issue_ratio": 0.0, "resolved_evidence_coverage": 0.0,
            "confuser_region_coverage": 0.0, "confuser_matches_resolved_iou": 0.0}
    ok = {"before": {"label": "issue_present", "confidence": 0.9}, "after": {"label": "no_issue", "confidence": 0.95}}
    assert decide_region(base | {"region_visible_after": 0.5}, ok) == "NOT_VERIFIABLE_REGION_NOT_VISIBLE"
    assert decide_region(base | {"remaining_issue_ratio": 0.4}, ok) == "PERSISTENT_ISSUE"
    assert decide_region(base | {"confuser_region_coverage": 0.5}, ok) == "NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION"
    conflict = base | {"confuser_region_coverage": 0.5, "confuser_matches_resolved_iou": 0.8}
    assert decide_region(conflict, None) == "NOT_VERIFIABLE_REPAIR_VS_CONFUSER_CONFLICT"
    assert decide_region(conflict, ok) == "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW"  # the classifier decides
    assert decide_region(base | {"resolved_evidence_coverage": 0.5}, None) == "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW"
    assert decide_closure(["RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW", "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"], True) \
        == "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"
    assert decide_closure(["NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE", "PERSISTENT_ISSUE"], True) == "PERSISTENT_ISSUE"


class FakeDb:
    def __init__(self, before_jpeg, after_jpeg):
        self.files = {"public/reports/rep1/v1.jpg": before_jpeg, "raw/dev/after.jpg": after_jpeg}
        self.rows = {
            "resolutions": {"id": "res1", "cluster_id": "c1", "photo_id": "p2", "ai_status": "pending",
                            "is_sandbox": False, "before_report_id": None},
            "photos": {"p1": {"id": "p1", "public_path": "public/reports/rep1/v1.jpg"},
                       "p2": {"id": "p2", "storage_path": "raw/dev/after.jpg"}},
            "report_photos": {"report_id": "rep1", "version": 1, "photo_id": "p1"},
        }
        self.uploads, self.inserted, self.patch = {}, [], None

    def select_one(self, table, **eq):
        if table == "photos":
            return self.rows["photos"][eq["id"]]
        return self.rows[table]

    def select(self, table, params):
        assert table == "reports" and params["cluster_id"] == "eq.c1"
        return [{"id": "rep1", "spec": SPEC, "current_photo_version": 1, "intake_status": "REPORT_CONFIRMED_BY_PHOTO"}]

    def rpc(self, fn, args):
        assert fn == "closure_location" and args == {"p_resolution_id": "res1", "p_before_report_id": "rep1"}
        return {"distance_m": None, "after_accuracy_m": None, "before_accuracy_m": None}

    def download(self, path):
        return self.files[path]

    def upload(self, path, data, content_type="image/jpeg"):
        self.uploads[path] = data

    def update(self, *a, **k):
        pass

    def update_where(self, table, patch, params):
        assert params == {"id": "eq.res1", "ai_status": "eq.pending"}
        self.patch = patch
        return True

    def insert(self, table, row):
        self.inserted.append((table, row))


def test_stage_stores_the_verification_and_the_figures(monkeypatch):
    before_rgb = scene()
    before_jpeg, after_jpeg = to_jpeg(before_rgb, 92), to_jpeg(shifted(before_rgb), 92)
    db = FakeDb(before_jpeg, after_jpeg)
    sam = FakeSAM({before_jpeg: {"pothole": [(0.8, HOLE)]}, after_jpeg: {"asphalt patch": [(0.7, moved(HOLE))]}})
    r, _ = router([pair("issue_present", 0.9, "no_issue", 0.9)], monkeypatch)
    assert ClosureStage(db, r, sam, anon_passthrough).run("res1") == "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW"
    v = db.patch["verification"]
    jsonschema.validate(v, SCHEMA)
    assert v["before_report_id"] == "rep1" and v["after_photo_id"] == "p2"
    assert v["region_checks"][0]["figure_paths"]["before"] == "public/resolutions/res1/r1_before.jpg"
    assert "public/resolutions/res1/after.jpg" in db.uploads
    assert db.inserted[-1][1]["stage"] == "closure" and db.inserted[-1][1]["status"] == "ok"


def anon_passthrough(photo: bytes) -> Anonymized:
    rgb = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
    return Anonymized(photo, rgb.shape[1], rgb.shape[0], faces=0, people=0, vehicles=0, blurred_share=0.0)


NEAR = location_check(6.0, 8.0, 10.0)
PATCH_ELSEWHERE = [420, 260, 540, 350]  # where the repair shows up in a photo from another angle


def test_location_check():
    assert NEAR["ok"] and NEAR["radius_m"] == 28.0 and 0.7 < NEAR["score"] < 0.8
    assert location_check(30.0, 8.0, 10.0)["reason"] == "too_far"
    assert location_check(5.0, 60.0, 10.0)["reason"] == "after_gps_too_coarse"
    assert location_check(None, None, None)["reason"] == "no_after_gps"
    assert location_check(39.0, 25.0, 30.0)["radius_m"] == 40.0  # capped


def test_unaligned_but_near_by_gps_can_be_a_candidate(monkeypatch):
    before, after = anon(scene()), anon(scene(seed=7))  # does not register
    sam = FakeSAM({before.jpeg: {"pothole": [(0.8, HOLE)]},
                   after.jpeg: {"asphalt patch": [(0.7, PATCH_ELSEWHERE)]}})
    r, seen = router([pair("issue_present", 0.9, "no_issue", 0.9)], monkeypatch)
    res = verify_pair(before, after, SPEC, sam, r, location=NEAR)
    v = res.verification
    assert v["evidence"] == "location" and not v["registration"]["ok"]
    assert v["ai_status"] == "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW"
    assert "another angle" in seen[0]["messages"][0]["content"]
    assert {"r1_before", "r1_after"} <= set(res.figures)
    jsonschema.validate(v | {"before_report_id": "00000000-0000-4000-8000-000000000001",
                             "before_photo_id": "00000000-0000-4000-8000-000000000002",
                             "after_photo_id": "00000000-0000-4000-8000-000000000003"}, SCHEMA)


@pytest.mark.parametrize(("after_hits", "verdict", "location", "expected"), [
    ({"asphalt patch": [(0.7, PATCH_ELSEWHERE)]}, pair("issue_present", 0.9, "no_issue", 0.9),
     location_check(35.0, 8.0, 10.0), "NOT_VERIFIABLE_REGION_NOT_VISIBLE"),           # too far: no location path
    ({"pothole": [(0.6, PATCH_ELSEWHERE)], "asphalt patch": [(0.7, HOLE)]}, None, NEAR,
     "PERSISTENT_ISSUE"),                                                              # a pothole anywhere persists
    ({}, None, NEAR, "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"),                          # no repair seen
    ({"asphalt patch": [(0.7, PATCH_ELSEWHERE)]}, pair("issue_present", 0.9, "no_issue", 0.82), NEAR,
     "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"),                                          # stricter than aligned
    ({"asphalt patch": [(0.7, PATCH_ELSEWHERE)]}, pair("issue_present", 0.9, "uncertain", 0.9), NEAR,
     "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"),                                          # "not the same spot"
])
def test_location_path_never_closes_on_doubt(monkeypatch, after_hits, verdict, location, expected):
    before, after = anon(scene()), anon(scene(seed=7))
    sam = FakeSAM({before.jpeg: {"pothole": [(0.8, HOLE)]}, after.jpeg: after_hits})
    r, seen = router([verdict] if verdict else [], monkeypatch)
    v = verify_pair(before, after, SPEC, sam, r, location=location).verification
    assert v["ai_status"] == expected
    if verdict is None:
        assert not seen
