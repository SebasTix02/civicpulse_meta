"""Stage `closure` (plan §5.4 B): is the reported problem fixed in the AFTER photo?

1. The same spec as the intake (from the BEFORE report). SAM 3.1 on the BEFORE photo finds the
   problem regions (extra deterioration prompts only there: on a fresh patch they fire too); on
   the AFTER photo it looks for the problem, the repair (resolved_prompts) and the confusers.
2. Homography registration (civicpulse.registration) projects the AFTER masks onto the BEFORE
   frame, so the same region is compared. A good registration only enables the comparison.
3. The Muse classifier sees the same rectangle in BEFORE and in the aligned AFTER, in one call.
4. Asymmetric decision per region, in this order (a false "resolved" is much worse than a false
   "persists"): not visible -> SAM still sees it -> confuser -> classifier -> without classifier.
   The best outcome is RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW: a person always confirms.

"I no longer detect it" is never enough: no registration, no visible region or no classifier
confirmation means NOT_VERIFIABLE_*.

Location path (sebas, 2026-10-29): real AFTER photos are often taken from another angle and do not
register. When the AFTER photo's GPS is close to the BEFORE report's (location_check), the case is
compared without alignment: SAM must not see the problem anywhere in the AFTER photo, must see a
repair, and the classifier must confirm it on the two outlined crops with a stricter threshold. The
result says evidence = "location" so the reviewer knows the photos were not aligned.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

import cv2
import numpy as np

from civicpulse.anonymize import Anonymized, load, to_jpeg
from civicpulse.llm.prompts import pair_check_prompt
from civicpulse.llm.router import AllProvidersFailed, LLMRouter, image_part
from civicpulse.models import PairVerdict
from civicpulse.registration import Registration, register, warp_to_before
from civicpulse.sam.service import Detection, SAMService

REGION_MIN_SCORE = 0.35
MAX_REGIONS = 3
GLOBAL_SHARE = 0.5
MIN_VISIBLE = 0.6          # share of the region inside the warped AFTER photo
PERSISTENT_RATIO = 0.25    # share of the region where SAM still sees the problem
CONFUSER_RATIO = 0.30
REPAIR_CONFUSER_IOU = 0.6  # new asphalt read as "wet": plan §5.4 B 4.3
BEFORE_MIN = 0.60          # the classifier must see the problem in BEFORE
AFTER_FIXED_MIN = 0.80     # ... and no_issue in AFTER
AFTER_PERSISTS_MIN = 0.50  # issue_present in AFTER this sure -> PERSISTENT_ISSUE
NO_CLASSIFIER_REMAINING = 0.10
NO_CLASSIFIER_REPAIR = 0.30
LOCATION_AFTER_FIXED_MIN = 0.85  # stricter: the two crops are not the same pixels
LOCATION_PERSISTS_SCORE = 0.5    # a target this sure anywhere in the AFTER photo -> persists
MAX_AFTER_ACCURACY_M = 25.0
MAX_LOCATION_RADIUS_M = 40.0
CONFUSER_LABELS = {"shadow", "water_or_wet_surface", "occlusion"}
MAGENTA = (255, 0, 255)

# ai_status when no region is PERSISTENT and not all are candidates: first in this order.
NOT_VERIFIABLE_ORDER = [
    "NOT_VERIFIABLE_REGION_NOT_VISIBLE",
    "NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION",
    "NOT_VERIFIABLE_REPAIR_VS_CONFUSER_CONFLICT",
    "NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED",
    "NOT_VERIFIABLE_CLASSIFIER_UNAVAILABLE",
    "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE",
]
RESOLVED = "RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW"
NOTE = "Visual support for inspection, not a guarantee of repair. When in doubt the system does not declare resolved."


def full_mask(d: Detection, w: int, h: int) -> np.ndarray:
    """The detection as a 0/1 mask over the whole photo (box when there is no raster)."""
    out = np.zeros((h, w), np.uint8)
    x1, y1, x2, y2 = (max(0, min(v, lim)) for v, lim in zip(d.box_px, (w, h, w, h), strict=True))
    if x2 <= x1 or y2 <= y1:
        return out
    if d.mask_local:
        raster, mw, mh = d.mask_local
        local = np.frombuffer(raster, np.uint8).reshape(mh, mw)
        out[y1:y2, x1:x2] = cv2.resize(local, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST) > 0
    else:
        out[y1:y2, x1:x2] = 1
    return out


def _segment(sam: SAMService, jpeg: bytes, w: int, h: int, prompts: list[str]) -> dict[str, list[Detection]]:
    if not prompts:
        return {}
    found = sam.segment(jpeg, prompts, threshold=REGION_MIN_SCORE).detections
    for d in (d for ds in found.values() for d in ds):
        if d.box_px is None:  # fixtures only have the normalized box
            x, y, bw, bh = d.box_xywh
            d.box_px = [round(x * w), round(y * h), round((x + bw) * w), round((y + bh) * h)]
    return found


def _union(found: dict[str, list[Detection]], prompts: list[str], w: int, h: int) -> np.ndarray:
    out = np.zeros((h, w), np.uint8)
    for p in prompts:
        for d in found.get(p, []):
            out |= full_mask(d, w, h)
    return out


def _share(region: np.ndarray, other: np.ndarray) -> float:
    area = int(region.sum())
    return float((region & other).sum() / area) if area else 0.0


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = int((a | b).sum())
    return float((a & b).sum() / union) if union else 0.0


def _crop_box(mask: np.ndarray, pad: float = 0.35, min_side: int = 320) -> list[int]:
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), ys.max()
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    hw = max((x2 - x1) * (1 + 2 * pad), min_side) / 2
    hh = max((y2 - y1) * (1 + 2 * pad), min_side) / 2
    return [max(0, int(cx - hw)), max(0, int(cy - hh)), min(w, int(cx + hw)), min(h, int(cy + hh))]


def _outlined(rgb: np.ndarray, mask: np.ndarray, box: list[int], max_side: int = 640) -> bytes:
    x1, y1, x2, y2 = box
    crop = rgb[y1:y2, x1:x2].copy()
    contours, _ = cv2.findContours(np.ascontiguousarray(mask[y1:y2, x1:x2]), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(crop, contours, -1, MAGENTA, max(2, round(max(crop.shape[:2]) / 200)))
    scale = max_side / max(crop.shape[:2])
    if scale < 1:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return to_jpeg(crop, 88)


def decide_region(m: dict, verdict: dict | None) -> str:
    """Plan §5.4 B step 4, in order. `verdict` is None when the classifier is off or down."""
    if m["region_visible_after"] < MIN_VISIBLE:
        return "NOT_VERIFIABLE_REGION_NOT_VISIBLE"
    if m["remaining_issue_ratio"] >= PERSISTENT_RATIO:
        return "PERSISTENT_ISSUE"
    conflict = m["confuser_matches_resolved_iou"] >= REPAIR_CONFUSER_IOU
    if m["confuser_region_coverage"] >= CONFUSER_RATIO and not conflict:
        return "NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION"
    if verdict is None:
        if conflict:
            return "NOT_VERIFIABLE_REPAIR_VS_CONFUSER_CONFLICT"
        if m["remaining_issue_ratio"] < NO_CLASSIFIER_REMAINING and m["resolved_evidence_coverage"] >= NO_CLASSIFIER_REPAIR:
            return RESOLVED
        return "NOT_VERIFIABLE_CLASSIFIER_UNAVAILABLE"
    before, after = verdict["before"], verdict["after"]
    if after["label"] == "issue_present" and after["confidence"] >= AFTER_PERSISTS_MIN:
        return "PERSISTENT_ISSUE"
    if not (before["label"] == "issue_present" and before["confidence"] >= BEFORE_MIN):
        return "NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED"
    if after["label"] in CONFUSER_LABELS:
        return "NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION"
    if after["label"] == "no_issue" and after["confidence"] >= AFTER_FIXED_MIN:
        return RESOLVED
    return "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"


def location_check(distance_m: float | None, after_accuracy_m: float | None,
                   before_accuracy_m: float | None) -> dict:
    """Is the AFTER photo at the reported place? Both GPS errors widen the radius, up to a cap;
    the score falls from 1 at the exact spot to 0 at the radius."""
    out = {"ok": False, "distance_m": None if distance_m is None else round(distance_m, 1),
           "after_accuracy_m": after_accuracy_m, "before_accuracy_m": before_accuracy_m,
           "radius_m": None, "score": 0.0, "reason": None}
    if distance_m is None or after_accuracy_m is None:
        return out | {"reason": "no_after_gps"}
    if after_accuracy_m > MAX_AFTER_ACCURACY_M:
        return out | {"reason": "after_gps_too_coarse"}
    radius = min(MAX_LOCATION_RADIUS_M, 10.0 + after_accuracy_m + (before_accuracy_m or 15.0))
    score = max(0.0, 1.0 - distance_m / radius)
    return out | {"ok": distance_m <= radius, "radius_m": round(radius, 1), "score": round(score, 3),
                  "reason": None if distance_m <= radius else "too_far"}


def decide_location(verdict: dict | None, persists_anywhere: bool, has_repair: bool, conflict: bool) -> str:
    """The location path, in the same spirit as decide_region but stricter."""
    if persists_anywhere:
        return "PERSISTENT_ISSUE"
    if not has_repair:
        return "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"
    if conflict:
        return "NOT_VERIFIABLE_REPAIR_VS_CONFUSER_CONFLICT"
    if verdict is None:
        return "NOT_VERIFIABLE_CLASSIFIER_UNAVAILABLE"
    before, after = verdict["before"], verdict["after"]
    if after["label"] == "issue_present" and after["confidence"] >= AFTER_PERSISTS_MIN:
        return "PERSISTENT_ISSUE"
    if not (before["label"] == "issue_present" and before["confidence"] >= BEFORE_MIN):
        return "NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED"
    if after["label"] in CONFUSER_LABELS:
        return "NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION"
    if after["label"] == "no_issue" and after["confidence"] >= LOCATION_AFTER_FIXED_MIN:
        return RESOLVED
    return "NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE"


def decide_closure(statuses: list[str], registration_ok: bool) -> str:
    if not registration_ok:
        return "NOT_VERIFIABLE_REGION_NOT_VISIBLE"
    if not statuses:
        return "NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED"
    if "PERSISTENT_ISSUE" in statuses:
        return "PERSISTENT_ISSUE"
    if all(s == RESOLVED for s in statuses):
        return RESOLVED
    return next(s for s in NOT_VERIFIABLE_ORDER if s in statuses)


@dataclass
class PairResult:
    verification: dict              # contract fields except the ids, which the stage adds
    figures: dict[str, bytes]       # name -> anonymized jpeg for the panel
    classifier_model: str | None


def verify_pair(before: Anonymized, after: Anonymized, spec: dict, sam: SAMService, router: LLMRouter | None,
                sam_model: str = "sam-3.1 (Meta Model API)", location: dict | None = None,
                force_location: bool = False) -> PairResult:
    """The whole check on two already anonymized photos. No database: the eval uses it as is.
    `location` is location_check()'s result; `force_location` (eval only) skips the registration."""
    brgb, argb = load(before.jpeg), load(after.jpeg)
    bh, bw = brgb.shape[:2]
    ah, aw = argb.shape[:2]
    targets = list(dict.fromkeys(spec.get("target_prompts") or []))[:3]
    extra = [p for p in spec.get("extra_target_prompts") or [] if p not in targets][:2]
    resolved = [p for p in spec.get("resolved_prompts") or [] if p not in targets][:2]
    confusers = [p for p in spec.get("confuser_prompts") or [] if p not in targets + resolved][:2]

    found_b = _segment(sam, before.jpeg, bw, bh, targets)
    regions = _regions(found_b, targets, bw, bh)
    if not regions and extra:
        found_b |= _segment(sam, before.jpeg, bw, bh, extra)
        regions = _regions(found_b, targets + extra, bw, bh)

    reg: Registration = Registration(False, "skipped") if force_location else register(brgb, argb)
    out = {"schema_version": "1.0", "catalog_version": 1,  # the stage sets the BEFORE report's
           "registration": reg.as_json(), "region_checks": [], "figures": {}, "note": NOTE,
           "evidence": "aligned" if reg.ok else "none", "location": location,
           "models": {"segmentation": sam_model, "classifier": None},
           "created_at": datetime.now(UTC).isoformat()}
    figures: dict[str, bytes] = {}
    classifier_model = None
    if regions and not reg.ok and location and location.get("ok"):
        return _verify_by_location(out, brgb, argb, before, after, regions, spec, sam, router,
                                   targets, resolved, confusers)
    if not regions or not reg.ok:
        out["ai_status"] = decide_closure([], reg.ok)
        return PairResult(out, figures, None)

    found_a = _segment(sam, after.jpeg, aw, ah, list(dict.fromkeys(targets + resolved + confusers)))

    def warp(m: np.ndarray) -> np.ndarray:
        return warp_to_before(m, reg.H, (bh, bw), nearest=True)

    visible = warp(np.ones((ah, aw), np.uint8))
    issue_after = warp(_union(found_a, targets, aw, ah))
    repair_after = warp(_union(found_a, resolved, aw, ah))
    confuser_after = warp(_union(found_a, confusers, aw, ah))
    aligned = warp_to_before(argb, reg.H, (bh, bw))

    statuses = []
    for i, (d, mask) in enumerate(regions, 1):
        conf_in, rep_in = confuser_after & mask, repair_after & mask
        m = {"region_visible_after": round(_share(mask, visible), 3),
             "remaining_issue_ratio": round(_share(mask, issue_after), 3),
             "resolved_evidence_coverage": round(_share(mask, repair_after), 3),
             "confuser_region_coverage": round(_share(mask, confuser_after), 3),
             "confuser_matches_resolved_iou": round(_iou(conf_in, rep_in), 3) if conf_in.any() else 0.0}
        box = _crop_box(mask)
        check = {"region_id": i, "prompt": d.prompt, "initial_confidence": round(d.score, 3),
                 "initial_area_px": int(mask.sum()), "crop_box_xyxy": box, **m, "classifier": None,
                 "figure_paths": {"before": None, "after_aligned": None}}
        verdict = None
        geometry_decides = decide_region(m, None) in ("NOT_VERIFIABLE_REGION_NOT_VISIBLE", "PERSISTENT_ISSUE")
        if not geometry_decides and router is not None:  # no classifier call when SAM already decides
            before_jpeg, after_jpeg = _outlined(brgb, mask, box), _outlined(aligned, mask, box)
            figures[f"r{i}_before"], figures[f"r{i}_after"] = before_jpeg, after_jpeg
            check["classifier"], verdict, model = _ask_pair(router, spec, before_jpeg, after_jpeg)
            classifier_model = model or classifier_model
        check["status"] = decide_region(m, verdict)
        statuses.append(check["status"])
        out["region_checks"].append(check)
        if check["status"] == "PERSISTENT_ISSUE":
            break  # one region that persists already decides the closure
    out["ai_status"] = decide_closure(statuses, True)
    out["models"]["classifier"] = classifier_model
    figures["aligned"] = to_jpeg(np.where(visible[..., None] > 0, aligned, (brgb * 0.35).astype(np.uint8)), 82)
    return PairResult(out, figures, classifier_model)


def _verify_by_location(out: dict, brgb: np.ndarray, argb: np.ndarray, before: Anonymized, after: Anonymized,
                        regions: list, spec: dict, sam: SAMService, router: LLMRouter | None,
                        targets: list[str], resolved: list[str], confusers: list[str]) -> PairResult:
    """No alignment: one comparison of the best BEFORE region with the repair SAM finds in AFTER."""
    ah, aw = argb.shape[:2]
    out["evidence"] = "location"
    found_a = _segment(sam, after.jpeg, aw, ah, list(dict.fromkeys(targets + resolved + confusers)))

    def local(prompts: list[str], min_score: float = 0.0) -> list[tuple[Detection, np.ndarray]]:
        ds = sorted((d for p in prompts for d in found_a.get(p, []) if d.score >= min_score), key=lambda d: -d.score)
        return [(d, m) for d in ds if (m := full_mask(d, aw, ah)).any() and m.mean() <= GLOBAL_SHARE]

    persists = local(targets, LOCATION_PERSISTS_SCORE)
    repairs = local(resolved)
    confuser_mask = _union(found_a, confusers, aw, ah)
    d, mask = regions[0]
    box = _crop_box(mask)
    check = {"region_id": 1, "prompt": d.prompt, "initial_confidence": round(d.score, 3),
             "initial_area_px": int(mask.sum()), "crop_box_xyxy": box, "classifier": None,
             "remaining_issue_ratio": 1.0 if persists else 0.0,
             "figure_paths": {"before": None, "after_aligned": None}}
    figures: dict[str, bytes] = {}
    verdict, model, conflict = None, None, False
    if repairs:
        rmask = repairs[0][1]
        conflict = _iou(rmask, rmask & confuser_mask) >= REPAIR_CONFUSER_IOU
        check["resolved_evidence_coverage"] = round(float(rmask.mean()), 3)
        check["after_box_xyxy"] = _crop_box(rmask)
    if not persists and repairs and not conflict and router is not None:
        before_jpeg = _outlined(brgb, mask, box)
        after_jpeg = _outlined(argb, repairs[0][1], check["after_box_xyxy"])
        figures["r1_before"], figures["r1_after"] = before_jpeg, after_jpeg
        check["classifier"], verdict, model = _ask_pair(router, spec, before_jpeg, after_jpeg, aligned=False)
    check["status"] = decide_location(verdict, bool(persists), bool(repairs), conflict)
    out["region_checks"].append(check)
    out["ai_status"] = check["status"]
    out["models"]["classifier"] = model
    return PairResult(out, figures, model)


def _regions(found: dict[str, list[Detection]], prompts: list[str], w: int, h: int) -> list[tuple[Detection, np.ndarray]]:
    """Problem regions in BEFORE, best first, the same place once, not the whole photo."""
    out: list[tuple[Detection, np.ndarray]] = []
    for d in sorted((d for p in prompts for d in found.get(p, [])), key=lambda d: -d.score):
        mask = full_mask(d, w, h)
        if not mask.any() or mask.mean() > GLOBAL_SHARE:
            continue
        if all(_iou(mask, m) < 0.5 for _, m in out):
            out.append((d, mask))
    return out[:MAX_REGIONS]


def _ask_pair(router: LLMRouter, spec: dict, before_jpeg: bytes, after_jpeg: bytes,
              aligned: bool = True) -> tuple[dict, dict | None, str | None]:
    second = "Image 2, AFTER (aligned to the same place):" if aligned else "Image 2, AFTER (same place by GPS, another angle):"
    user = [{"type": "text", "text": "Image 1, BEFORE:"}, image_part(before_jpeg),
            {"type": "text", "text": second}, image_part(after_jpeg)]
    try:
        res = router.structured(pair_check_prompt(spec, aligned), user, PairVerdict, vision=True, max_tokens=250)
    except AllProvidersFailed as exc:
        return {"model": None, "before": {"label": "uncertain", "confidence": 0.0},
                "after": {"label": "uncertain", "confidence": 0.0}, "reason": None, "error": str(exc)[:300]}, None, None
    v: PairVerdict = res.data  # type: ignore[assignment]
    model = f"{res.provider}:{res.model}"
    answer = {"model": model, "before": v.before.model_dump(include={"label", "confidence"}),
              "after": v.after.model_dump(include={"label", "confidence"}),
              "reason": (v.reason or v.before.reason or v.after.reason)[:300] or None, "error": None}
    return answer, {"before": answer["before"], "after": answer["after"]}, model


def _as_anonymized(jpeg: bytes) -> Anonymized:
    rgb = load(jpeg)
    return Anonymized(jpeg, rgb.shape[1], rgb.shape[0], faces=0, people=0, vehicles=0, blurred_share=0.0)


class ClosureStage:
    """resolutions row (ai_status pending) -> verification + ai_status. The outcome stays open:
    the stakeholder (or the community) confirms in the panel."""

    def __init__(self, db, router: LLMRouter, sam: SAMService, anonymizer, sam_model: str = "sam-3.1 (Meta Model API)"):
        self.db, self.router, self.sam, self.anonymize, self.sam_model = db, router, sam, anonymizer, sam_model

    def run(self, resolution_id: str) -> str:
        res = self.db.select_one("resolutions", id=resolution_id)
        if res is None:
            return "no_resolution"
        if res["ai_status"] != "pending":
            return "already_verified"
        started = time.monotonic()
        try:
            verification, status, model = self._verify(res)
        except Exception as exc:  # recorded, then re-raised so the sweeper or Modal retries
            self.db.insert("stage_runs", {"resolution_id": resolution_id, "stage": "closure", "status": "error",
                                          "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                                          "latency_ms": int((time.monotonic() - started) * 1000)})
            raise
        stored = self.db.update_where("resolutions", {"verification": verification, "ai_status": status},
                                      {"id": f"eq.{resolution_id}", "ai_status": "eq.pending"})
        self.db.insert("stage_runs", {"resolution_id": resolution_id, "stage": "closure",
                                      "status": "ok" if stored else "skipped", "provider": "meta_api",
                                      "model": model, "latency_ms": int((time.monotonic() - started) * 1000)})
        return status

    def _verify(self, res: dict) -> tuple[dict, str, str | None]:
        before_report = self._before_report(res)
        spec = before_report["spec"] or {}
        link = self.db.select_one("report_photos", report_id=before_report["id"],
                                  version=before_report["current_photo_version"])
        before_photo = self.db.select_one("photos", id=link["photo_id"])
        before = _as_anonymized(self.db.download(before_photo["public_path"]))  # the intake's public copy
        after_photo = self.db.select_one("photos", id=res["photo_id"])
        after = self.anonymize(self.db.download(after_photo["storage_path"]))
        base = (f"public/sandbox/resolutions/{res['id']}" if res.get("is_sandbox") else f"public/resolutions/{res['id']}")
        self.db.upload(f"{base}/after.jpg", after.jpeg)
        self.db.update("photos", {"public_path": f"{base}/after.jpg", "width": after.width, "height": after.height},
                       id=after_photo["id"])

        geo = self.db.rpc("closure_location", {"p_resolution_id": res["id"], "p_before_report_id": before_report["id"]}) or {}
        location = location_check(geo.get("distance_m"), geo.get("after_accuracy_m"), geo.get("before_accuracy_m"))
        result = verify_pair(before, after, spec, self.sam, self.router, self.sam_model, location=location)
        v = result.verification
        paths = {}
        for name, jpeg in result.figures.items():
            paths[name] = f"{base}/{name}.jpg"
            self.db.upload(paths[name], jpeg)
        for c in v["region_checks"]:
            i = c["region_id"]
            c["figure_paths"] = {"before": paths.get(f"r{i}_before"), "after_aligned": paths.get(f"r{i}_after")}
        v["figures"] = {"masks_before": None, "masks_after": None, "alignment": paths.get("aligned")}
        v |= {"catalog_version": before_report.get("catalog_version") or 1,
              "before_report_id": before_report["id"], "before_photo_id": before_photo["id"],
              "after_photo_id": after_photo["id"]}
        return v, v["ai_status"], result.classifier_model

    def _before_report(self, res: dict) -> dict:
        if res.get("before_report_id"):
            return self.db.select_one("reports", id=res["before_report_id"])
        rows = self.db.select("reports", {
            "select": "id,spec,catalog_version,current_photo_version,intake_status,created_at", "cluster_id": f"eq.{res['cluster_id']}",
            "moderation_status": "eq.visible", "current_photo_version": "not.is.null",
            "order": "created_at.desc", "limit": "20"})
        confirmed = [r for r in rows if r.get("intake_status") == "REPORT_CONFIRMED_BY_PHOTO"]
        if not (confirmed or rows):
            raise RuntimeError("cluster has no report with a photo")
        return (confirmed or rows)[0]
