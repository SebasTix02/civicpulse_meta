import json

from civicpulse.models import SpecLLMOutput
from civicpulse.spec_grade import compact, grade, summarize

TEACHER = {"category_id": "pothole", "domain_id": "roads", "summary_en": "Pothole on Main St", "severity": 3,
           "entities": None, "sam_prompts": ["pothole"], "photo_verifiable": True, "confidence": 0.9,
           "contains_personal_data": False}


def test_teacher_answer_passes_every_check():
    g = grade(compact(TEACHER), "pothole", TEACHER)
    assert g.json_valid and g.category and g.severity_within_1 and g.personal_data and g.prompts_when_visual


def test_compact_is_one_line_in_fixed_order():
    line = compact({**TEACHER, "extra": 1})
    assert "\n" not in line and "extra" not in line
    assert list(json.loads(line)) == ["category_id", "domain_id", "summary_en", "severity", "entities",
                                      "sam_prompts", "photo_verifiable", "confidence", "contains_personal_data"]


def test_reasoning_and_prose_around_json_are_ignored():
    text = "<think>it is a hole</think>Sure: " + compact(TEACHER) + " done"
    assert grade(text, "pothole", TEACHER).json_valid


def test_wrong_category_severity_and_missing_prompts():
    bad = {**TEACHER, "category_id": "garbage", "severity": 5, "sam_prompts": [], "contains_personal_data": True}
    g = grade(compact(bad), "pothole", TEACHER)
    assert g.json_valid and not g.category and not g.severity_within_1 and not g.personal_data
    assert not g.prompts_when_visual and g.predicted_category == "garbage"


def test_unknown_category_counts_as_other():
    g = grade(compact({**TEACHER, "category_id": "alien_landing"}), "other", TEACHER, known={"pothole", "other"})
    assert g.category and g.predicted_category == "other"


def test_invalid_json_fails_everything():
    g = grade("I think it is a pothole", "pothole", TEACHER)
    assert not g.json_valid and not g.category and g.error


def test_parsed_spec_and_summary():
    kw = SpecLLMOutput(category_id="pothole", summary_en="keyword fallback", severity=3, sam_prompts=[],
                       photo_verifiable=True, confidence=0.5, contains_personal_data=True)
    grades = [grade(kw, "pothole", TEACHER), grade("nope", "pothole", TEACHER)]
    s = summarize(grades)
    assert s["n"] == 2 and s["json_valid"] == 0.5 and s["category"] == 0.5 and s["personal_data"] == 0.0
