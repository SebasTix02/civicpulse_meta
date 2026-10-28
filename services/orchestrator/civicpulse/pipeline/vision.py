"""Stage `sam_ground` + `classify` (spec_ready -> vision_done, plan §3.3 step 4 and §5.4 A).

1. The raw photo is anonymized on our CPU (civicpulse.anonymize). Only that copy goes to the
   Meta Model API and to the public bucket; the raw one never leaves Supabase and Modal.
2. SAM 3.1 grounds the report with a small prompt budget (billed per request): target prompts,
   the main confusers and the domain's count prompts. The extra target prompts run only when
   the main ones found nothing (budget "widened").
3. The Muse classifier checks up to 3 candidate crops, each with the mask outlined in magenta,
   and stops at the first confirmation. If no crop was confirmed it looks at the whole photo once.
4. decide_intake() turns that into intake_status. A photo never rejects a report: "not found"
   means "not verifiable with this photo", and a classifier that is down never confirms anything.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import cv2
import numpy as np

from civicpulse.anonymize import Anonymized, load, to_jpeg
from civicpulse.db import Db
from civicpulse.llm.prompts import crop_check_prompt, whole_image_check_prompt
from civicpulse.llm.router import AllProvidersFailed, LLMRouter, image_part
from civicpulse.models import ClassifierVerdict, IntakeStatus
from civicpulse.sam.service import Detection, SAMService

CANDIDATE_MIN_SCORE = 0.25  # low on purpose (plan §5.3): more proposals never risk the asymmetric rule
CONFUSER_MIN_SCORE = 0.40
COUNT_MIN_SCORE = 0.40
GLOBAL_SHARE = 0.5  # a mask over half the photo is a condition (shadow, water), not a region
GLOBAL_MIN_SCORE = 0.75
MAIN_CONFUSER_OVERLAP = 0.30
SAME_REGION_IOU = 0.6
MAX_CANDIDATES = 5
CONFIRM_MIN = 0.70  # plan §5.4 A: a crop shows the problem with confidence >= 0.70
CONSISTENCY = {"REPORT_CONFIRMED_BY_PHOTO": 1.0, "ISSUE_VISIBLE_NOT_LOCALIZED_REQUIRES_REVIEW": 0.7,
               "CANDIDATE_REGIONS_FOUND_REQUIRES_REVIEW": 0.7}  # everything else 0.5 (plan §5.5, to calibrate)
MAGENTA = (255, 0, 255)


@dataclass(frozen=True)
class Budget:
    """SAM requests per photo, from env so it can be tuned without a deploy of new code."""

    targets: int = int(os.environ.get("SAM_TARGET_PROMPTS", "3"))
    confusers: int = int(os.environ.get("SAM_CONFUSER_PROMPTS", "2"))
    extra: int = int(os.environ.get("SAM_EXTRA_PROMPTS", "2"))
    classify: int = int(os.environ.get("CLASSIFY_MAX_CANDIDATES", "3"))


def _iou(a: list[int], b: list[int]) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def _covered(a: list[int], by: list[int]) -> float:
    """Share of box a inside box `by`."""
    ix = max(0, min(a[2], by[2]) - max(a[0], by[0]))
    iy = max(0, min(a[3], by[3]) - max(a[1], by[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return ix * iy / area if area else 0.0


def _area_px(d: Detection) -> int:
    x1, y1, x2, y2 = d.box_px
    if not d.mask_local:
        return (x2 - x1) * (y2 - y1)
    raster, mw, mh = d.mask_local
    return int(sum(raster) * (x2 - x1) * (y2 - y1) / max(1, mw * mh))


def outlined_crop(rgb: np.ndarray, d: Detection, pad: float = 0.25, min_side: int = 320, max_side: int = 768) -> tuple[bytes, list[int]]:
    """The candidate with some context around it and its mask outlined in magenta."""
    h, w = rgb.shape[:2]
    x1, y1, x2, y2 = d.box_px
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half_w = max((x2 - x1) * (1 + 2 * pad), min_side) / 2
    half_h = max((y2 - y1) * (1 + 2 * pad), min_side) / 2
    cx1, cy1 = max(0, int(cx - half_w)), max(0, int(cy - half_h))
    cx2, cy2 = min(w, int(cx + half_w)), min(h, int(cy + half_h))
    crop = np.ascontiguousarray(rgb[cy1:cy2, cx1:cx2])
    mask = np.zeros(crop.shape[:2], np.uint8)
    if d.mask_local and x2 > x1 and y2 > y1:
        raster, mw, mh = d.mask_local
        local = np.frombuffer(raster, np.uint8).reshape(mh, mw)
        mask[y1 - cy1:y2 - cy1, x1 - cx1:x2 - cx1] = cv2.resize(local, (x2 - x1, y2 - y1),
                                                                 interpolation=cv2.INTER_NEAREST)
    else:
        mask[y1 - cy1:y2 - cy1, x1 - cx1:x2 - cx1] = 1
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(crop, contours, -1, MAGENTA, max(2, round(max(crop.shape[:2]) / 200)))
    scale = max_side / max(crop.shape[:2])
    if scale < 1:
        crop = cv2.resize(crop, (round(crop.shape[1] * scale), round(crop.shape[0] * scale)),
                          interpolation=cv2.INTER_AREA)
    return to_jpeg(crop, 88), [cx1, cy1, cx2, cy2]


def decide_intake(spec: dict, candidates: list[dict], whole_image: dict | None, classifier_on: bool) -> IntakeStatus:
    """Asymmetric like the closure rule: only a confident classifier answer confirms."""
    if spec.get("photo_verifiability") == "none":
        return "NOT_PHOTO_VERIFIABLE_CATEGORY"
    if not spec.get("target_prompts"):
        return "NOT_VERIFIABLE_NO_VISUAL_SPEC"
    answers = [c["classifier"] for c in candidates if c.get("classifier")]
    if any(a["label"] == "issue_present" and a["confidence"] >= CONFIRM_MIN for a in answers):
        return "REPORT_CONFIRMED_BY_PHOTO"
    if whole_image and whole_image["label"] == "issue_present" and whole_image["confidence"] >= CONFIRM_MIN:
        return "ISSUE_VISIBLE_NOT_LOCALIZED_REQUIRES_REVIEW"
    if candidates and (not classifier_on or any(a["label"] in ("issue_present", "uncertain") for a in answers)):
        return "CANDIDATE_REGIONS_FOUND_REQUIRES_REVIEW"  # a person decides
    return "NOT_VERIFIABLE_ISSUE_NOT_FOUND_IN_PHOTO"


class VisionStage:
    def __init__(self, db: Db, router: LLMRouter, sam: SAMService, anonymizer: Callable[[bytes], Anonymized],
                 sam_model: str = "sam-3.1 (Meta Model API)", budget: Budget | None = None):
        self.db, self.router, self.sam, self.anonymize = db, router, sam, anonymizer
        self.sam_model, self.budget = sam_model, budget or Budget()

    def run(self, report: dict) -> None:
        version = report["current_photo_version"]
        if self.db.stage_done("sam_ground", report_id=report["id"], photo_version=version):
            return
        started = time.monotonic()
        try:
            intake, classifier_model = self._intake(report)
        except Exception as exc:  # recorded, then re-raised so the sweeper retries
            self.db.insert("stage_runs", {"report_id": report["id"], "photo_version": version, "stage": "sam_ground",
                                          "status": "error", "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                                          "latency_ms": int((time.monotonic() - started) * 1000)})
            raise
        status = intake["intake_status"] if intake else "NOT_VERIFIABLE_ISSUE_NOT_FOUND_IN_PHOTO"
        moved = self.db.compare_and_set("reports", report["id"], "spec_ready", {
            "status": "vision_done",
            "intake": intake,
            "intake_status": status,
            "consistency_score": CONSISTENCY.get(status, 0.5),
            "model_versions": (report.get("model_versions") or {}) | {
                "sam_ground": self.sam_model, "classify": classifier_model},
        })
        ms = int((time.monotonic() - started) * 1000)
        for stage, provider in (("sam_ground", "meta_api"), ("classify", classifier_model)):
            self.db.insert("stage_runs", {"report_id": report["id"], "photo_version": version, "stage": stage,
                                          "status": "ok" if moved else "skipped", "provider": provider,
                                          "latency_ms": ms})

    def _intake(self, report: dict) -> tuple[dict | None, str | None]:
        spec, version = report.get("spec") or {}, report["current_photo_version"]
        if version is None:
            return None, None
        link = self.db.select_one("report_photos", report_id=report["id"], version=version)
        photo = self.db.select_one("photos", id=link["photo_id"])
        anon = self.anonymize(self.db.download(photo["storage_path"]))
        public_dir = (f"public/sandbox/{report.get('sandbox_session_id')}/{report['id']}" if report.get("is_sandbox")
                      else f"public/reports/{report['id']}")
        public_path = f"{public_dir}/v{version}.jpg"
        self.db.upload(public_path, anon.jpeg)
        self.db.update("photos", {"public_path": public_path, "width": anon.width, "height": anon.height}, id=photo["id"])

        intake = {
            "schema_version": "1.0", "photo_version": version, "photo_id": photo["id"],
            "image_size": {"width": anon.width, "height": anon.height},
            "anonymization": {"faces": anon.faces, "people": anon.people, "vehicles": anon.vehicles,
                              "blurred_share": anon.blurred_share, "public_path": public_path},
            "candidates": [], "global_detections": [], "whole_image_check": None, "people_count": None,
            "budget": "intake", "models": {"segmentation": self.sam_model, "classifier": None},
            "created_at": datetime.now(UTC).isoformat(),
        }
        if spec.get("photo_verifiability") == "none" or not spec.get("target_prompts"):
            intake["intake_status"] = decide_intake(spec, [], None, False)
            intake["consistency_score"] = CONSISTENCY.get(intake["intake_status"], 0.5)
            return intake, None

        domain = self.db.select_one("domains", id=report["domain_id"]) if report.get("domain_id") else None
        count_prompts = list((domain or {}).get("count_prompts") or [])[:1]
        targets = spec["target_prompts"][: self.budget.targets]
        confusers = [p for p in spec.get("confuser_prompts", []) if p not in targets][: self.budget.confusers]
        prompts = list(dict.fromkeys(targets + confusers + count_prompts))
        found = self._segment(anon, prompts)
        regions, globals_ = self._regions(found, targets, spec, anon)
        if not regions and spec.get("extra_target_prompts"):
            extra = [p for p in spec["extra_target_prompts"] if p not in prompts][: self.budget.extra]
            found |= self._segment(anon, extra)
            regions, globals_ = self._regions(found, targets + extra, spec, anon)
            intake["budget"] = "widened"
        if count_prompts:
            intake["people_count"] = sum(1 for d in found.get(count_prompts[0], []) if d.score >= COUNT_MIN_SCORE)
        intake["global_detections"] = globals_

        rgb = load(anon.jpeg)
        confusers_found = [d for p in confusers for d in found.get(p, []) if d.score >= CONFUSER_MIN_SCORE]
        classifier_model, classifier_on = None, False
        for i, d in enumerate(regions, 1):
            crop_jpeg, crop_box = outlined_crop(rgb, d)
            overlap, confuser = max(((_covered(d.box_px, c.box_px), c.prompt) for c in confusers_found),
                                    default=(0.0, None))
            cand = {"candidate_id": i, "prompt": d.prompt, "sam_score": d.score, "area_px": _area_px(d),
                    "box_xyxy": d.box_px, "crop_box_xyxy": crop_box,
                    "main_confuser": confuser if overlap >= MAIN_CONFUSER_OVERLAP else None,
                    "confuser_overlap": round(overlap, 3), "classifier": None, "crop_public_path": None}
            confirmed = any((c["classifier"] or {}).get("label") == "issue_present"
                            and c["classifier"]["confidence"] >= CONFIRM_MIN for c in intake["candidates"])
            if i <= self.budget.classify and not confirmed:
                cand["crop_public_path"] = f"{public_dir}/v{version}_c{i}.jpg"
                self.db.upload(cand["crop_public_path"], crop_jpeg)
                answer, model = self._ask(crop_check_prompt(spec), crop_jpeg)
                cand["classifier"] = answer
                if model:
                    classifier_model, classifier_on = model, True
            intake["candidates"].append(cand)

        if decide_intake(spec, intake["candidates"], None, classifier_on) != "REPORT_CONFIRMED_BY_PHOTO":
            answer, model = self._ask(whole_image_check_prompt(spec), anon.jpeg)
            intake["whole_image_check"] = answer
            if model:
                classifier_model, classifier_on = model, True
        intake["models"]["classifier"] = classifier_model
        intake["intake_status"] = decide_intake(spec, intake["candidates"], intake["whole_image_check"], classifier_on)
        intake["consistency_score"] = CONSISTENCY.get(intake["intake_status"], 0.5)
        return intake, classifier_model

    def _segment(self, anon: Anonymized, prompts: list[str]) -> dict[str, list[Detection]]:
        found = self.sam.segment(anon.jpeg, prompts, threshold=CANDIDATE_MIN_SCORE).detections
        for d in (d for ds in found.values() for d in ds):
            if d.box_px is None:  # fixtures only have the normalized box
                x, y, w, h = d.box_xywh
                d.box_px = [round(x * anon.width), round(y * anon.height),
                            round((x + w) * anon.width), round((y + h) * anon.height)]
        return found

    def _regions(self, found: dict[str, list[Detection]], targets: list[str], spec: dict,
                 anon: Anonymized) -> tuple[list[Detection], list[dict]]:
        """Target detections as candidate regions (best first, same region once), and the masks
        that cover most of the photo as global conditions instead."""
        image_area = anon.width * anon.height
        regions, globals_ = [], []
        for d in sorted((d for p in targets for d in found.get(p, [])), key=lambda d: -d.score):
            share = _area_px(d) / image_area
            if share > GLOBAL_SHARE and not spec.get("issue_can_be_global"):
                if d.score >= GLOBAL_MIN_SCORE:
                    globals_.append({"prompt": d.prompt, "score": d.score, "zone_coverage": round(share, 3)})
                continue
            if all(_iou(d.box_px, r.box_px) < SAME_REGION_IOU for r in regions):
                regions.append(d)
        return regions[:MAX_CANDIDATES], globals_

    def _ask(self, system: str, jpeg: bytes) -> tuple[dict, str | None]:
        """One classifier call. A failure is recorded in the answer and confirms nothing."""
        user = [{"type": "text", "text": "Photo from the citizen report:"}, image_part(jpeg)]
        try:
            res = self.router.structured(system, user, ClassifierVerdict, vision=True, max_tokens=200)
        except AllProvidersFailed as exc:
            return {"model": None, "label": "uncertain", "confidence": 0.0, "reason": None,
                    "error": str(exc)[:300]}, None
        v: ClassifierVerdict = res.data  # type: ignore[assignment]
        return {"model": f"{res.provider}:{res.model}", "label": v.label, "confidence": v.confidence,
                "reason": v.reason[:300] or None}, f"{res.provider}:{res.model}"
