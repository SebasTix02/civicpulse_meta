import json

import httpx
import jsonschema
import numpy as np
import pytest

from civicpulse.anonymize import Anonymized, to_jpeg
from civicpulse.catalog import Catalog
from civicpulse.config import REPO_ROOT, ProviderConfig
from civicpulse.llm.router import LLMRouter
from civicpulse.models import SpecLLMOutput
from civicpulse.pipeline.vision import VisionStage, decide_intake
from civicpulse.sam.service import Detection, SegmentResult

CATALOG = Catalog.load(REPO_ROOT / "packages/catalog/civic_catalog.json")
ANON = to_jpeg(np.full((300, 400, 3), 120, np.uint8))


def spec_for(category: str) -> dict:
    return CATALOG.merge_spec(SpecLLMOutput(
        category_id=category, summary_en="Deep pothole on the main street", severity=3, sam_prompts=[],
        photo_verifiable=True, confidence=0.9, contains_personal_data=False), "test")


class FakeDb:
    def __init__(self, spec: dict):
        self.rows = {
            "reports": {"id": "r1", "status": "spec_ready", "current_photo_version": 1, "spec": spec,
                        "domain_id": "roads", "is_sandbox": False, "model_versions": {"spec": "a:m"}},
            "report_photos": {"report_id": "r1", "version": 1, "photo_id": "p1"},
            "photos": {"id": "p1", "storage_path": "raw/dev/nonce.jpg"},
            "domains": {"id": "roads", "count_prompts": []},
        }
        self.inserted, self.uploads, self.updates, self.downloads = [], {}, [], []

    def select_one(self, table, **eq):
        return self.rows.get(table)

    def stage_done(self, stage, **kw):
        return any(r["stage"] == stage and r["status"] == "ok" for t, r in self.inserted if t == "stage_runs")

    def insert(self, table, row):
        self.inserted.append((table, row))

    def update(self, table, patch, **eq):
        self.updates.append((table, patch, eq))

    def download(self, path):
        self.downloads.append(path)
        return b"RAW PHOTO"

    def upload(self, path, data, content_type="image/jpeg"):
        self.uploads[path] = data

    def compare_and_set(self, table, id_, expected, patch):
        if self.rows[table]["status"] != expected:
            return False
        self.rows[table] |= patch
        return True


class FakeSAM:
    def __init__(self, hits: dict[str, list[tuple[float, list[int]]]]):
        self.hits, self.calls = hits, []

    def segment(self, image_jpeg, prompts, threshold=0.4):
        self.calls.append((image_jpeg, list(prompts)))
        out = SegmentResult(backend="fake")
        for p in prompts:
            out.detections[p] = [Detection(p, s, [b[0] / 400, b[1] / 300, (b[2] - b[0]) / 400, (b[3] - b[1]) / 300],
                                           box_px=b, mask_local=(bytes([1] * 4), 2, 2))
                                 for s, b in self.hits.get(p, []) if s >= threshold]
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


def anonymizer(photo: bytes) -> Anonymized:
    assert photo == b"RAW PHOTO"
    return Anonymized(ANON, 400, 300, faces=1, people=0, vehicles=0, blurred_share=0.02)


def verdict(label, conf):
    return 200, json.dumps({"label": label, "confidence": conf, "reason": "x"})


def test_confirmed_by_crop_and_only_the_anonymized_copy_leaves(monkeypatch):
    db, sam = FakeDb(spec_for("pothole")), FakeSAM({"pothole": [(0.8, [100, 100, 180, 160])]})
    r, seen = router([verdict("issue_present", 0.9)], monkeypatch)
    VisionStage(db, r, sam, anonymizer).run(db.rows["reports"])

    rep = db.rows["reports"]
    assert rep["status"] == "vision_done" and rep["intake_status"] == "REPORT_CONFIRMED_BY_PHOTO"
    assert rep["consistency_score"] == 1.0 and rep["model_versions"]["spec"] == "a:m"
    assert all(img == ANON for img, _ in sam.calls)  # SAM never sees the raw photo
    assert sam.calls[0][1] == ["pothole", "road crack", "shadow", "wet asphalt"]
    assert set(db.uploads) == {"public/reports/r1/v1.jpg", "public/reports/r1/v1_c1.jpg"}
    assert db.updates[0][1]["public_path"] == "public/reports/r1/v1.jpg"
    cand = rep["intake"]["candidates"][0]
    assert cand["box_xyxy"] == [100, 100, 180, 160] and cand["classifier"]["label"] == "issue_present"
    assert rep["intake"]["whole_image_check"] is None and len(seen) == 1  # confirmed: no extra calls
    assert seen[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert [row["stage"] for t, row in db.inserted] == ["sam_ground", "classify"]
    schema = json.loads((REPO_ROOT / "packages/contracts/schemas/intake_result.schema.json").read_text())
    jsonschema.validate(rep["intake"], schema)  # the stored intake keeps the contract


def test_classifier_down_leaves_candidates_for_review(monkeypatch):
    db, sam = FakeDb(spec_for("pothole")), FakeSAM({"pothole": [(0.5, [10, 10, 60, 60])]})
    r, _ = router([], monkeypatch)
    VisionStage(db, r, sam, anonymizer).run(db.rows["reports"])
    rep = db.rows["reports"]
    assert rep["intake_status"] == "CANDIDATE_REGIONS_FOUND_REQUIRES_REVIEW" and rep["consistency_score"] == 0.7
    assert rep["intake"]["models"]["classifier"] is None
    assert rep["intake"]["candidates"][0]["classifier"]["error"]


def test_nothing_found_widens_then_checks_the_whole_photo(monkeypatch):
    db, sam = FakeDb(spec_for("pothole")), FakeSAM({})
    r, seen = router([verdict("issue_present", 0.8)], monkeypatch)
    VisionStage(db, r, sam, anonymizer).run(db.rows["reports"])
    rep = db.rows["reports"]
    assert rep["intake"]["budget"] == "widened" and sam.calls[1][1] == ["worn asphalt", "pavement distress"]
    assert rep["intake_status"] == "ISSUE_VISIBLE_NOT_LOCALIZED_REQUIRES_REVIEW" and len(seen) == 1


def test_a_shadow_over_most_of_the_photo_is_global_not_a_region(monkeypatch):
    db = FakeDb(spec_for("pothole"))
    sam = FakeSAM({"pothole": [(0.9, [0, 0, 400, 300])], "shadow": [(0.9, [0, 0, 400, 300])]})
    r, _ = router([verdict("no_issue", 0.9)], monkeypatch)
    VisionStage(db, r, sam, anonymizer).run(db.rows["reports"])
    intake = db.rows["reports"]["intake"]
    assert intake["candidates"] == [] and intake["global_detections"][0]["prompt"] == "pothole"
    assert db.rows["reports"]["intake_status"] == "NOT_VERIFIABLE_ISSUE_NOT_FOUND_IN_PHOTO"


def test_category_without_photo_skips_sam(monkeypatch):
    spec = spec_for("pothole") | {"photo_verifiability": "none"}
    db, sam = FakeDb(spec), FakeSAM({})
    r, seen = router([], monkeypatch)
    VisionStage(db, r, sam, anonymizer).run(db.rows["reports"])
    assert db.rows["reports"]["intake_status"] == "NOT_PHOTO_VERIFIABLE_CATEGORY"
    assert sam.calls == [] and seen == [] and "public/reports/r1/v1.jpg" in db.uploads


def test_sam_error_is_recorded_and_raised(monkeypatch):
    class Broken(FakeSAM):
        def segment(self, *a, **k):
            raise httpx.ConnectError("down")

    db = FakeDb(spec_for("pothole"))
    r, _ = router([], monkeypatch)
    with pytest.raises(httpx.ConnectError):
        VisionStage(db, r, Broken({}), anonymizer).run(db.rows["reports"])
    assert db.rows["reports"]["status"] == "spec_ready"
    assert db.inserted[-1][1]["status"] == "error"


@pytest.mark.parametrize("cands,whole,on,expected", [
    ([{"classifier": {"label": "issue_present", "confidence": 0.69}}], None, True, "CANDIDATE_REGIONS_FOUND_REQUIRES_REVIEW"),
    ([{"classifier": {"label": "no_issue", "confidence": 0.9}}], None, True, "NOT_VERIFIABLE_ISSUE_NOT_FOUND_IN_PHOTO"),
    ([{"classifier": {"label": "shadow", "confidence": 0.9}}], {"label": "issue_present", "confidence": 0.9}, True,
     "ISSUE_VISIBLE_NOT_LOCALIZED_REQUIRES_REVIEW"),
    ([], None, False, "NOT_VERIFIABLE_ISSUE_NOT_FOUND_IN_PHOTO"),
])
def test_decide_intake_never_confirms_on_doubt(cands, whole, on, expected):
    assert decide_intake({"target_prompts": ["pothole"]}, cands, whole, on) == expected
