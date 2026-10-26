from civicpulse.closure.decision import aggregate, decide_region
from civicpulse.models import ClassifierVerdict, PairVerdict, RegionMetrics


def metrics(**kw):
    base = {"region_visible_after": 0.9, "remaining_issue_ratio": 0.0, "resolved_evidence_coverage": 0.6,
                "confuser_region_coverage": 0.0, "confuser_matches_resolved_iou": 0.0}
    return RegionMetrics(**(base | kw))


def verdict(before=("issue_present", 0.9), after=("no_issue", 0.9)):
    return PairVerdict(before=ClassifierVerdict(label=before[0], confidence=before[1]),
                       after=ClassifierVerdict(label=after[0], confidence=after[1]))


def test_region_out_of_view_is_never_resolved():
    assert decide_region(metrics(region_visible_after=0.3), verdict(), True) == "NOT_VERIFIABLE_REGION_NOT_VISIBLE"


def test_sam_still_sees_the_issue():
    assert decide_region(metrics(remaining_issue_ratio=0.5), verdict(), True) == "PERSISTENT_ISSUE"


def test_shadow_over_region():
    m = metrics(confuser_region_coverage=0.5)
    assert decide_region(m, verdict(), True) == "NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION"


def test_fresh_patch_read_as_wet_without_classifier():
    m = metrics(confuser_region_coverage=0.5, confuser_matches_resolved_iou=0.99)
    assert decide_region(m, None, False) == "NOT_VERIFIABLE_REPAIR_VS_CONFUSER_CONFLICT"


def test_fresh_patch_read_as_wet_classifier_decides():
    m = metrics(confuser_region_coverage=0.5, confuser_matches_resolved_iou=0.99)
    assert decide_region(m, verdict(), True) == "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW"


def test_classifier_medium_confidence_keeps_ticket_open():
    assert decide_region(metrics(), verdict(after=("issue_present", 0.55)), True) == "PERSISTENT_ISSUE"


def test_classifier_needs_high_confidence_to_accept_fix():
    assert decide_region(metrics(), verdict(after=("no_issue", 0.79)), True) == "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"


def test_issue_must_be_confirmed_before():
    assert decide_region(metrics(), verdict(before=("uncertain", 0.9)), True) == "NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED"


def test_classifier_down_never_resolves():
    assert decide_region(metrics(), None, True) == "NOT_VERIFIABLE_CLASSIFIER_UNAVAILABLE"


def test_aggregate_is_conservative():
    ok = "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW"
    assert aggregate([ok, ok], True) == ok
    assert aggregate([ok, "PERSISTENT_ISSUE"], True) == "PERSISTENT_ISSUE"
    assert aggregate([ok, "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"], True) == "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"
    assert aggregate([ok], False) == "NOT_VERIFIABLE_REGION_NOT_VISIBLE"
    assert aggregate([], True) == "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"
