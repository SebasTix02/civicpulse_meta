"""Build a local page to hand-label pothole size, depth and urgency on the "before" photos.

    python data/pothole_pairs/label_size.py              # before photos of the repaired / not repaired pairs
    python data/pothole_pairs/label_size.py --all        # every anonymized before photo with a pothole label

Opens nothing on the network: it writes data/private/label_size.html with the anonymized photos
from data/private/anon/. These labels are the reference for scripts/eval_measure.py, which checks
civicpulse/measure.py (relative size from SAM 3.1, depth cues from Muse). Per photo:

  size      relative to the roadway width at the pothole: 1 small (< 15%), 2 medium (15 to 35%),
            3 large (> 35%), 4 cannot tell
  depth     1 low (surface wear, shallow), 2 medium (a clear hole), 3 high (base or stones exposed,
            vertical walls, could damage a tyre), 4 cannot tell
  urgency   as an inspector would rank it from the photo: 1 can wait, 2 normal, 3 urgent
  street    "c" toggles: both edges of the roadway are visible at the pothole (default yes)

Keys go in order (size, depth, urgency) and the page moves on. Labels stay in the browser; "Guardar
CSV" saves data/pothole_pairs/size_depth_labels.csv (photo ids and labels only, safe to commit).
Running the script again preloads that file.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "data" / "private"
ANON = PRIVATE / "anon"
HERE = ROOT / "data" / "pothole_pairs"
CLOSURE = HERE / "closure_labels_v2.csv"
LABELS = HERE / "size_depth_labels.csv"
FIELDS = ["photo", "size", "depth", "urgency", "road_fits"]
MAX_BLURRED = 0.4


def photos(every: bool) -> list[str]:
    blurred = {}
    report = PRIVATE / "prefilter.csv"
    if report.exists():
        blurred = {Path(r["photo"]).stem: float(r["blurred_share"] or 0)
                   for r in csv.DictReader(report.open(encoding="utf-8"))}
    keep = {"repaired", "not_repaired"} | ({"not_comparable"} if every else set())
    out = []
    for r in csv.DictReader(CLOSURE.open(encoding="utf-8")):
        stem = f"{r['pair_id']}_before"
        if r["label"] in keep and (ANON / f"{stem}.jpg").exists() and blurred.get(stem, 0) <= MAX_BLURRED:
            out.append(stem)
    return out


def preload(ids: set[str]) -> dict:
    if not LABELS.exists():
        return {}
    return {r["photo"]: {k: r[k] for k in FIELDS[1:]} for r in csv.DictReader(LABELS.open(encoding="utf-8"))
            if r["photo"] in ids}


PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tamaño y profundidad</title>
<style>
 :root { color-scheme: light dark; --bg:#f6f6f4; --fg:#1d1d1b; --muted:#6b6b66; --card:#fff; --line:#ddd; --acc:#1565c0; }
 @media (prefers-color-scheme: dark) { :root { --bg:#161615; --fg:#ecece8; --muted:#a3a39c; --card:#222220; --line:#3a3a37; --acc:#64b5f6; } }
 body { margin:0; font:15px/1.4 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
 header { display:flex; gap:10px; align-items:center; flex-wrap:wrap; padding:8px 16px; border-bottom:1px solid var(--line); }
 header b { font-size:17px; } .muted { color:var(--muted); }
 img { display:block; width:100%; height:calc(100vh - 290px); object-fit:contain; background:#000; }
 .rows { padding:6px 16px; display:grid; gap:6px; }
 .row { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
 .row > span { width:90px; font-weight:600; }
 .row.active > span { color:var(--acc); }
 button { font:inherit; padding:6px 11px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer; }
 button.sel { outline:3px solid var(--acc); font-weight:700; }
 .strip { display:flex; flex-wrap:wrap; gap:3px; padding:4px 16px 12px; }
 .strip span { width:14px; height:14px; border-radius:3px; background:var(--line); cursor:pointer; }
 .strip span.done { background:var(--acc); } .strip span.cur { outline:2px solid var(--fg); }
</style></head><body>
<header><b id="title"></b><span class="muted" id="progress"></span><span style="flex:1"></span>
 <button id="prev">← Anterior</button><button id="next">Siguiente →</button>
 <button id="dl">Guardar CSV</button></header>
<img id="photo" alt="foto antes">
<div class="rows">
 <div class="row" data-f="size"><span>Tamaño</span>
  <button data-v="small">1 · Pequeño (&lt;15 % del ancho)</button><button data-v="medium">2 · Mediano (15–35 %)</button>
  <button data-v="large">3 · Grande (&gt;35 %)</button><button data-v="unclear">4 · No se sabe</button></div>
 <div class="row" data-f="depth"><span>Profundidad</span>
  <button data-v="low">1 · Baja (desgaste)</button><button data-v="medium">2 · Media (hueco claro)</button>
  <button data-v="high">3 · Alta (base expuesta, paredes verticales)</button><button data-v="unclear">4 · No se sabe</button></div>
 <div class="row" data-f="urgency"><span>Urgencia</span>
  <button data-v="1">1 · Puede esperar</button><button data-v="2">2 · Normal</button><button data-v="3">3 · Urgente</button></div>
 <div class="row" data-f="road_fits"><span>Calle</span>
  <button data-v="yes">c · Se ven los dos bordes de la calzada</button><button data-v="no">c · La calle no entra completa</button></div>
</div>
<div class="strip" id="strip"></div>
<script>
const PHOTOS = __PHOTOS__, ORDER = ["size", "depth", "urgency"];
const KEY = "civicpulse-size-labels-v1";
let labels = __LABELS__;
try { Object.assign(labels, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) {}
const $ = id => document.getElementById(id);
const complete = p => labels[p] && ORDER.every(f => labels[p][f]);
let i = PHOTOS.findIndex(p => !complete(p)); if (i < 0) i = 0;
function save() { try { localStorage.setItem(KEY, JSON.stringify(labels)); } catch (e) {} }
function cur() { const p = PHOTOS[i]; labels[p] = labels[p] || { road_fits: "yes" }; return labels[p]; }
function field() { const l = labels[PHOTOS[i]] || {}; return ORDER.find(f => !l[f]) || null; }
function show() {
  const p = PHOTOS[i], l = labels[p] || { road_fits: "yes" };
  $("title").textContent = p; $("photo").src = "anon/" + p + ".jpg";
  $("progress").textContent = `${i + 1}/${PHOTOS.length} · listas ${PHOTOS.filter(complete).length}`;
  const f = field();
  document.querySelectorAll(".row").forEach(r => {
    r.classList.toggle("active", r.dataset.f === f);
    r.querySelectorAll("button").forEach(b => b.classList.toggle("sel", l[r.dataset.f] === b.dataset.v));
  });
  const strip = $("strip"); strip.innerHTML = "";
  PHOTOS.forEach((x, k) => { const s = document.createElement("span");
    s.className = (complete(x) ? "done" : "") + (k === i ? " cur" : ""); s.title = x;
    s.onclick = () => { i = k; show(); }; strip.appendChild(s); });
}
function set(f, v) {
  cur()[f] = v; save();
  if (ORDER.includes(f) && !field()) { const k = PHOTOS.findIndex((x, k) => k > i && !complete(x)); if (k >= 0) i = k; }
  show();
}
document.querySelectorAll(".row button").forEach(b => b.onclick = () => set(b.closest(".row").dataset.f, b.dataset.v));
$("prev").onclick = () => { if (i > 0) i--; show(); };
$("next").onclick = () => { if (i < PHOTOS.length - 1) i++; show(); };
$("dl").onclick = () => {
  const rows = ["photo,size,depth,urgency,road_fits"].concat(PHOTOS.filter(complete)
    .map(p => [p, labels[p].size, labels[p].depth, labels[p].urgency, labels[p].road_fits || "yes"].join(",")));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([rows.join("\\n") + "\\n"], { type: "text/csv" }));
  a.download = "size_depth_labels.csv"; a.click();
};
document.addEventListener("keydown", e => {
  if (e.key === "ArrowLeft") return $("prev").click();
  if (e.key === "ArrowRight") return $("next").click();
  if (e.key === "c") return set("road_fits", cur().road_fits === "no" ? "yes" : "no");
  const f = field() || "urgency", row = document.querySelector(`.row[data-f="${f}"]`);
  const b = row.querySelectorAll("button")[Number(e.key) - 1];
  if (b) set(f, b.dataset.v);
});
show();
</script></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="also the not_comparable cases (more photos, less clear)")
    args = ap.parse_args()
    found = photos(args.all)
    if not found:
        print("no labeled before photo in data/private/anon/: run qc.py, prefilter.py and label_all.py first",
              file=sys.stderr)
        return 2
    done = preload(set(found))
    out = PRIVATE / "label_size.html"
    out.write_text(PAGE.replace("__PHOTOS__", json.dumps(found)).replace("__LABELS__", json.dumps(done)),
                   encoding="utf-8")
    print(f"{len(found)} photos, {len(done)} preloaded -> {out} (open it in your browser)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
