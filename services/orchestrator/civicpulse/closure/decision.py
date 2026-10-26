"""Asymmetric closure decision (plan §5.4 B).

A false "resolved" is much worse than a false "persistent", so every rule that can say
"not resolved" runs before the favorable one, and doubt never ends in a resolution.
Thresholds come from the research notebook runs (Oct 21-22) and are tuned on the real
before/after pairs during the hackathon (eval/closure).
"""

from __future__ import annotations

from dataclasses import dataclass

from civicpulse.models import PairVerdict, RegionMetrics, RegionStatus

CONFUSER_LABELS = {"shadow", "water_or_wet_surface", "occlusion", "not_visible"}


@dataclass(frozen=True)
class Thresholds:
    min_region_visible_after: float = 0.60
    min_remaining_issue_for_persistent: float = 0.20
    max_remaining_issue_for_candidate: float = 0.10
    max_confuser_roi_coverage: float = 0.20
    min_iou_confuser_same_as_repair: float = 0.60
    min_resolved_coverage_without_classifier: float = 0.30
    clf_min_initial_issue: float = 0.60  # the problem must be confirmed BEFORE
    clf_min_persistent: float = 0.50  # medium confidence keeps the ticket open
    clf_min_resolved: float = 0.80  # high bar to accept a fix


DEFAULT = Thresholds()


def decide_region(
    m: RegionMetrics, verdict: PairVerdict | None, classifier_enabled: bool, t: Thresholds = DEFAULT
) -> RegionStatus:
    if m.region_visible_after < t.min_region_visible_after:
        return "NOT_VERIFIABLE_REGION_NOT_VISIBLE"
    if m.remaining_issue_ratio >= t.min_remaining_issue_for_persistent:
        return "PERSISTENT_ISSUE"

    repair_conflict = m.confuser_matches_resolved_iou >= t.min_iou_confuser_same_as_repair
    if m.confuser_region_coverage >= t.max_confuser_roi_coverage:
        if not repair_conflict:
            return "NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION"
        if not classifier_enabled:
            # A fresh dark patch is also read as "wet asphalt": only the classifier can tell.
            return "NOT_VERIFIABLE_REPAIR_VS_CONFUSER_CONFLICT"

    sam_favorable = m.remaining_issue_ratio <= t.max_remaining_issue_for_candidate

    if classifier_enabled:
        if verdict is None or not verdict.valid or verdict.error:
            return "NOT_VERIFIABLE_CLASSIFIER_UNAVAILABLE"
        before, after = verdict.before, verdict.after
        if before.label != "issue_present" or before.confidence < t.clf_min_initial_issue:
            return "NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED"
        if after.label == "issue_present" and after.confidence >= t.clf_min_persistent:
            return "PERSISTENT_ISSUE"
        if after.label in CONFUSER_LABELS:
            return "NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION"
        if after.label == "no_issue" and after.confidence >= t.clf_min_resolved and sam_favorable:
            return "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW"
        return "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"

    if sam_favorable and m.resolved_evidence_coverage >= t.min_resolved_coverage_without_classifier:
        return "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW"
    return "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"


# When regions disagree, the overall status is the most conservative one.
_SEVERITY: list[RegionStatus] = [
    "PERSISTENT_ISSUE",
    "NOT_VERIFIABLE_REGION_NOT_VISIBLE",
    "NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION",
    "NOT_VERIFIABLE_REPAIR_VS_CONFUSER_CONFLICT",
    "NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED",
    "NOT_VERIFIABLE_CLASSIFIER_UNAVAILABLE",
    "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE",
    "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW",
]


def aggregate(region_statuses: list[RegionStatus], registration_ok: bool) -> RegionStatus:
    """Overall ai_status. Favorable only if registration worked and EVERY region is a candidate."""
    if not registration_ok:
        return "NOT_VERIFIABLE_REGION_NOT_VISIBLE"
    if not region_statuses:
        return "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"
    return min(region_statuses, key=_SEVERITY.index)
