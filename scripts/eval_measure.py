"""Size and depth eval: does civicpulse/measure.py agree with a person looking at the same photo?

    python data/pothole_pairs/label_size.py           # 1. label size, depth and urgency by hand
    python scripts/eval_measure.py --dry-run          # 2. what it would cost, nothing is sent
    python scripts/eval_measure.py --report docs/eval/measure_eval_$(date +%F).md
    python scripts/eval_measure.py --stability        # also a zoomed copy of each photo

For every labeled "before" photo (anonymized, data/private/anon/) it runs the production code:
SAM 3.1 "pothole" (the best detection plays the confirmed candidate), then VisionStage._measure,
the same function the pipeline calls (SAM "road" + relative_size, one Muse call for the depth
cues). The classifier confirmation is skipped: this eval measures the measurement, given a pothole.
The text is empty, so depth comes from the photo alone.

Numbers:
  size       exact and within-one agreement with the label (photos where both say a class), split by
             whether the street fits in the photo; rank correlation of width_share with the label
  depth      exact and within-one; how often a "high" by hand came out "low"
  priority   the photo bonus (size + depth, production weights from migration 28) against the
             urgency label: rank correlation, mean bonus per urgency, and how many urgent photos land
             in the top third. Before this change every single photo had the same bonus (0).
  stability  --stability: the same photo cropped to its central 85% and scaled back (as if taken a
             step closer): change of width_share and of size class. Distance should not matter.

Results go to data/private/measure_eval.csv; --resume continues a stopped run.
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "orchestrator"))
sys.path.insert(0, str(ROOT / "scripts"))

import cv2
from civicpulse.anonymize import Anonymized, load, to_jpeg
from civicpulse.catalog import Catalog
from civicpulse.config import REPO_ROOT, Settings
from civicpulse.llm.router import LLMRouter
from civicpulse.measure import relative_size
from civicpulse.pipeline.vision import VisionStage
from eval_robustness import PRICE_IN, PRICE_OUT, PRICE_SAM, CountingSAM
from smoke_models import load_env

ANON = ROOT / "data" / "private" / "anon"
LABELS = ROOT / "data" / "pothole_pairs" / "size_depth_labels.csv"
OUT = ROOT / "data" / "private" / "measure_eval.csv"
SIZES, DEPTHS = ("small", "medium", "large"), ("low", "medium", "high")
WEIGHTS = {"size": {"small": 0, "medium": 0.5, "large": 1.0}, "depth": {"low": 0, "medium": 0.5, "high": 1.0}}
EST_TOKENS_IN, EST_TOKENS_OUT = 1500, 120
FIELDS = ["photo", "label_size", "label_depth", "urgency", "road_fits", "pothole_score", "size_status",
          "width_share", "area_share", "clipped", "size_class", "depth_level", "depth_cues", "depth_confidence",
          "zoom_width_share", "zoom_size_class", "ms", "error"]


def anon(jpeg: bytes) -> Anonymized:
    rgb = load(jpeg)
    return Anonymized(jpeg, rgb.shape[1], rgb.shape[0], faces=0, people=0, vehicles=0, blurred_share=0.0)


def zoomed(rgb) -> bytes:
    """Central 85% scaled back to full size: as if the photo were taken a step closer."""
    h, w = rgb.shape[:2]
    dy, dx = int(h * 0.075), int(w * 0.075)
    return to_jpeg(cv2.resize(rgb[dy:h - dy, dx:w - dx], (w, h), interpolation=cv2.INTER_LINEAR), 90)


def best_pothole(stage: VisionStage, a: Anonymized, prompt: str):
    found = stage._segment(a, [prompt]).get(prompt, [])
    return max(found, key=lambda d: d.score) if found else None


def run_one(label: dict, stage: VisionStage, cfg: dict, prompt: str, stability: bool) -> dict:
    row = {"photo": label["photo"], "label_size": label["size"], "label_depth": label["depth"],
           "urgency": label["urgency"], "road_fits": label["road_fits"], "error": ""}
    started = time.monotonic()
    try:
        jpeg = (ANON / f"{label['photo']}.jpg").read_bytes()
        a, rgb = anon(jpeg), load(jpeg)
        d = best_pothole(stage, a, prompt)
        if d is None:
            row |= {"size_status": "no_pothole"}
        else:
            m = stage._measure(cfg, d, {}, a, rgb, "")
            size, depth = m.get("size", {}), m.get("depth", {})
            row |= {"pothole_score": round(d.score, 3), "size_status": size.get("status"),
                    "width_share": size.get("width_share"), "area_share": size.get("area_share"),
                    "clipped": size.get("clipped"), "size_class": size.get("size_class"),
                    "depth_level": depth.get("level"), "depth_cues": " ".join(depth.get("cues") or []),
                    "depth_confidence": depth.get("confidence")}
            if stability:
                z = zoomed(rgb)
                za = anon(z)
                zd = best_pothole(stage, za, prompt)
                if zd is not None:
                    zs = relative_size(zd, stage._segment(za, [cfg["surface_prompt"]]).get(cfg["surface_prompt"], []),
                                       za.width, za.height)
                    row |= {"zoom_width_share": zs.get("width_share"), "zoom_size_class": zs.get("size_class")}
    except Exception as exc:  # noqa: BLE001 - recorded, the rest keeps going
        row["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    row["ms"] = int((time.monotonic() - started) * 1000)
    return row


def ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda k: xs[k])
    out = [0.0] * len(xs)
    k = 0
    while k < len(order):
        j = k
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[k]]:
            j += 1
        for t in range(k, j + 1):
            out[order[t]] = (k + j) / 2 + 1
        k = j + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def agreement(pairs: list[tuple[str, str]], order: tuple[str, ...]) -> tuple[int, int, int]:
    exact = sum(a == b for a, b in pairs)
    near = sum(abs(order.index(a) - order.index(b)) <= 1 for a, b in pairs)
    return exact, near, len(pairs)


def confusion(pairs: list[tuple[str, str]], order: tuple[str, ...], title: str) -> list[str]:
    c = Counter(pairs)
    lines = [f"{title} (rows: by hand, columns: measured)", f"{'':>10}" + "".join(f"{o:>9}" for o in order)]
    for a in order:
        lines.append(f"{a:>10}" + "".join(f"{c[(a, b)]:>9}" for b in order))
    return lines


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "0/0"


def bonus(r: dict) -> float:
    size = 0.0 if r.get("clipped") in ("True", True) else WEIGHTS["size"].get(r.get("size_class") or "", 0.0)
    return size + WEIGHTS["depth"].get(r.get("depth_level") or "", 0.0)


def summary(rows: list[dict]) -> list[str]:
    lines: list[str] = []
    say = lines.append
    ok = [r for r in rows if not r["error"]]
    found = [r for r in ok if r.get("size_status") not in ("no_pothole", None, "")]
    measured = [r for r in found if r["size_status"] == "ok"]
    say(f"photos: {len(rows)} ({len(rows) - len(ok)} failed) · pothole found by SAM: {pct(len(found), len(ok))} · "
        f"road found: {pct(len(measured), len(found))} · street runs off the photo (clipped): "
        f"{pct(sum(r['clipped'] in ('True', True) for r in measured), len(measured))}")

    say("\nSIZE")
    for name, subset in (("all measured", measured),
                         ("street fits in the photo (by hand)", [r for r in measured if r["road_fits"] == "yes"]),
                         ("not clipped (what adds priority)", [r for r in measured if r["clipped"] not in ("True", True)])):
        pairs = [(r["label_size"], r["size_class"]) for r in subset if r["label_size"] in SIZES]
        e, n, t = agreement(pairs, SIZES)
        say(f"  {name:<36} exact {pct(e, t)} · within one class {pct(n, t)}")
    both = [r for r in measured if r["label_size"] in SIZES]
    rho = spearman([float(r["width_share"]) for r in both], [SIZES.index(r["label_size"]) for r in both])
    say(f"  rank correlation width_share vs label: {rho:.2f}" if rho is not None else "  rank correlation: n/a")
    lines += ["  " + x for x in confusion([(r["label_size"], r["size_class"]) for r in both], SIZES, "size")]

    say("\nDEPTH (photo only, no text)")
    dpairs = [(r["label_depth"], r["depth_level"]) for r in found if r["label_depth"] in DEPTHS and r["depth_level"] in DEPTHS]
    e, n, t = agreement(dpairs, DEPTHS)
    unknown = sum(r["label_depth"] in DEPTHS and r["depth_level"] not in DEPTHS for r in found)
    say(f"  exact {pct(e, t)} · within one level {pct(n, t)} · no answer (model unsure or down) {unknown}")
    say(f"  high by hand measured low: {sum(p == ('high', 'low') for p in dpairs)}")
    lines += ["  " + x for x in confusion(dpairs, DEPTHS, "depth")]

    say("\nPRIORITY (photo bonus = size + depth, production weights)")
    scored = [r for r in ok if r["urgency"] in ("1", "2", "3")]
    b = [bonus(r) for r in scored]
    u = [int(r["urgency"]) for r in scored]
    rho = spearman(b, u)
    say("  rank correlation with urgency by hand: before none (every photo had the same bonus), now "
        + (f"{rho:.2f}" if rho is not None else "n/a"))
    for level in (1, 2, 3):
        vals = [x for x, y in zip(b, u, strict=True) if y == level]
        if vals:
            say(f"  urgency {level}: {len(vals):>3} photos, mean bonus {statistics.fmean(vals):.2f}")
    if scored:
        cut = sorted(b, reverse=True)[max(0, len(b) // 3 - 1)]
        urgent = [x for x, y in zip(b, u, strict=True) if y == 3]
        top = sum(x >= cut and x > 0 for x in urgent)
        say(f"  urgent photos in the top third by bonus: {pct(top, len(urgent))} (by chance about 33%)")

    zoom = [r for r in measured if r.get("zoom_width_share") not in (None, "")]
    if zoom:
        say("\nSTABILITY (same photo, central 85% scaled back)")
        diffs = [abs(float(r["zoom_width_share"]) - float(r["width_share"])) for r in zoom]
        same = sum(r["zoom_size_class"] == r["size_class"] for r in zoom)
        say(f"  median change of width_share {statistics.median(diffs):.3f} · same size class {pct(same, len(zoom))}")

    ms = [int(r["ms"]) for r in ok if r.get("ms")]
    if ms:
        say(f"\nlatency per photo: median {statistics.median(ms) / 1000:.1f} s")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the first N labeled photos")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--stability", action="store_true", help="also measure a zoomed copy (2 more SAM requests per photo)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", type=Path, help="also write the summary as Markdown here (numbers only, no photos)")
    args = ap.parse_args()
    if not LABELS.exists():
        print(f"{LABELS.relative_to(ROOT)} not found: run data/pothole_pairs/label_size.py and label first", file=sys.stderr)
        return 2
    labels = [r for r in csv.DictReader(LABELS.open(encoding="utf-8")) if (ANON / f"{r['photo']}.jpg").exists()]
    if args.limit:
        labels = labels[: args.limit]
    done = []
    if args.resume and OUT.exists():
        done = [r for r in csv.DictReader(OUT.open(encoding="utf-8")) if not r["error"]]
    seen = {r["photo"] for r in done}
    jobs = [r for r in labels if r["photo"] not in seen]
    sam_per = 4 if args.stability else 2
    usd = len(jobs) * (sam_per * PRICE_SAM + EST_TOKENS_IN * PRICE_IN + EST_TOKENS_OUT * PRICE_OUT)
    print(f"{len(jobs)} photos to run ({len(done)} done); estimate USD {len(jobs) * sam_per * PRICE_SAM:.2f} of Meta "
          f"credits (SAM, {sam_per} requests each) + USD {usd - len(jobs) * sam_per * PRICE_SAM:.2f} of Baseten (Muse)")
    if args.dry_run or not jobs:
        if done:
            print("\n".join(summary(done)))
        return 0

    load_env()
    if not os.environ.get("META_MODEL_API_KEY"):
        print("META_MODEL_API_KEY missing in .env", file=sys.stderr)
        return 2
    os.environ.setdefault("SAM_BACKEND", "meta_api")
    settings = Settings()
    cat = Catalog.load(REPO_ROOT / "packages/catalog/civic_catalog.json").categories["pothole"]
    cfg, prompt = cat["measure"], cat["target_prompts"][0]
    router, sam = LLMRouter(settings.llm_providers), CountingSAM(settings.sam())
    stage = VisionStage(None, router, sam, None)  # type: ignore[arg-type]

    rows = list(done)
    new_file = not (args.resume and OUT.exists())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w" if new_file else "a", encoding="utf-8", newline="") as f:
        out = csv.DictWriter(f, FIELDS, extrasaction="ignore")
        if new_file:
            out.writeheader()
        with ThreadPoolExecutor(args.workers) as pool:
            futures = [pool.submit(run_one, lab, stage, cfg, prompt, args.stability) for lab in jobs]
            for k, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                rows.append(row)
                out.writerow(row)
                f.flush()
                if k % 10 == 0 or k == len(jobs):
                    print(f"  {k}/{len(jobs)} photos, {sam.requests} SAM requests")
    usage = list(router.usage.values())
    spent = sam.requests * PRICE_SAM + sum(u["prompt_tokens"] * PRICE_IN + u["completion_tokens"] * PRICE_OUT for u in usage)
    table = summary([{k: ("" if v is None else v) for k, v in r.items()} for r in rows])
    table.append(f"cost of this run: {sam.requests} SAM requests + {sum(u['calls'] for u in usage)} Muse calls, "
                 f"about USD {spent:.2f}")
    print("\n".join(table))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            f"# Pothole size and depth eval, {time.strftime('%Y-%m-%d')}\n\n"
            f"`python scripts/eval_measure.py {' '.join(sys.argv[1:])}` on anonymized \"before\" photos of real "
            "pothole repairs from a public entity in Ambato, labeled by hand "
            "(data/pothole_pairs/size_depth_labels.csv). SAM 3.1 through the Meta Model API, depth cues from "
            "Muse Spark; the code is the production one (civicpulse/measure.py, VisionStage._measure).\n\n"
            "```\n" + "\n".join(table).strip() + "\n```\n", encoding="utf-8")
        print(f"summary written to {args.report}")
    print(f"\nper-photo results in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
