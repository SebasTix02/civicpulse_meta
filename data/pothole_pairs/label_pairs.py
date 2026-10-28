"""Build a local page to hand-label pothole before/after pairs for the closure evaluation.

    python data/pothole_pairs/label_pairs.py            # up to 60 new pairs, fixed seed
    python data/pothole_pairs/label_pairs.py --n 200    # every real pair not labeled yet

Opens nothing on the network: it writes data/private/label_pairs.html, which shows the
anonymized photos from data/private/anon/ side by side. Open it in your browser (double click),
label with the keyboard, then press "Download CSV" and save it as
data/pothole_pairs/closure_labels.csv (pair ids and labels only, safe to commit).

Labels:
  1 repaired        the pothole in "before" is clearly fixed in "after", same place
  2 not_repaired    same place, the pothole (or part of it) is still there
  3 not_comparable  different place or angle, too dark, too blurred, or you cannot tell
Progress is kept in the browser, so you can close the page and continue later. Pairs already in
closure_labels.csv come back with their label, so the new download keeps them: save it over the
old file. Only real pairs are offered (usable_as = pair): qc.py marks as same_photo the cases
whose before and after are one identical file.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "data" / "private"
ANON = PRIVATE / "anon"
LABELS = ROOT / "data" / "pothole_pairs" / "closure_labels.csv"
MAX_BLURRED = 0.4  # above this the blur may hide the pothole (prefilter.csv)


def candidates() -> list[str]:
    blurred = {}
    report = PRIVATE / "prefilter.csv"
    if report.exists():
        blurred = {Path(r["photo"]).stem: float(r["blurred_share"] or 0)
                   for r in csv.DictReader(report.open(encoding="utf-8"))}
    out = []
    for row in csv.DictReader((ROOT / "data" / "pothole_pairs" / "pairs.csv").open(encoding="utf-8")):
        if row.get("usable_as") != "pair":
            continue
        sides = [f"{row['pair_id']}_before", f"{row['pair_id']}_after"]
        if all((ANON / f"{s}.jpg").exists() and blurred.get(s, 0) <= MAX_BLURRED for s in sides):
            out.append(row["pair_id"])
    return out


PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Etiquetar pares de baches</title>
<style>
 :root { color-scheme: light dark; --bg:#f6f6f4; --fg:#1d1d1b; --muted:#6b6b66; --card:#fff; --line:#ddd;
   --ok:#2e7d32; --no:#c62828; --nc:#8d6e00; }
 @media (prefers-color-scheme: dark) { :root { --bg:#161615; --fg:#ecece8; --muted:#a3a39c; --card:#222220; --line:#3a3a37; } }
 body { margin:0; font:15px/1.4 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
 header { display:flex; gap:12px; align-items:center; flex-wrap:wrap; padding:10px 16px; border-bottom:1px solid var(--line); }
 header b { font-size:17px; } .muted { color:var(--muted); }
 .pair { display:grid; grid-template-columns:1fr 1fr; gap:8px; padding:8px 16px; }
 .pair figure { margin:0; background:var(--card); border:1px solid var(--line); border-radius:6px; overflow:hidden; }
 .pair img { width:100%; height:calc(100vh - 210px); object-fit:contain; background:#000; display:block; }
 figcaption { padding:4px 8px; font-weight:600; }
 .actions { display:flex; gap:8px; flex-wrap:wrap; padding:8px 16px; align-items:center; }
 button { font:inherit; padding:8px 14px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer; }
 button.ok { border-color:var(--ok); } button.no { border-color:var(--no); } button.nc { border-color:var(--nc); }
 button.sel { outline:3px solid currentColor; font-weight:700; }
 input[type=text] { font:inherit; padding:7px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--fg); flex:1; min-width:180px; }
 @media (max-width: 700px) { .pair { grid-template-columns:1fr; } .pair img { height:40vh; } }
</style></head><body>
<header><b id="title"></b><span class="muted" id="progress"></span><span style="flex:1"></span>
 <button id="prev">← Anterior</button><button id="next">Siguiente →</button><button id="dl">Descargar CSV</button></header>
<div class="pair">
 <figure><img id="before" alt="Antes"><figcaption>ANTES</figcaption></figure>
 <figure><img id="after" alt="Después"><figcaption>DESPUÉS</figcaption></figure>
</div>
<div class="actions">
 <button class="ok" data-l="repaired">1 · Reparado</button>
 <button class="no" data-l="not_repaired">2 · No reparado</button>
 <button class="nc" data-l="not_comparable">3 · No comparable</button>
 <input type="text" id="note" placeholder="Nota opcional (p. ej. otro ángulo, parche parcial)">
</div>
<script>
const PAIRS = __PAIRS__;
const KEY = "civicpulse-closure-labels-v1";
let labels = __LABELS__;
try { Object.assign(labels, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) {}
let i = PAIRS.findIndex(p => !labels[p]); if (i < 0) i = 0;
const $ = id => document.getElementById(id);
function save() { try { localStorage.setItem(KEY, JSON.stringify(labels)); } catch (e) {} }
function show() {
  const p = PAIRS[i];
  $("title").textContent = p;
  $("before").src = "anon/" + p + "_before.jpg";
  $("after").src = "anon/" + p + "_after.jpg";
  const done = PAIRS.filter(x => labels[x]).length;
  $("progress").textContent = `${i + 1} / ${PAIRS.length} · etiquetados ${done}`;
  $("note").value = (labels[p] && labels[p].note) || "";
  document.querySelectorAll("button[data-l]").forEach(b =>
    b.classList.toggle("sel", !!labels[p] && labels[p].label === b.dataset.l));
}
function setLabel(l) {
  const p = PAIRS[i];
  labels[p] = { label: l, note: $("note").value.trim() };
  save();
  if (i < PAIRS.length - 1) i++;
  show();
}
document.querySelectorAll("button[data-l]").forEach(b => b.onclick = () => setLabel(b.dataset.l));
$("note").oninput = () => { const p = PAIRS[i]; if (labels[p]) { labels[p].note = $("note").value.trim(); save(); } };
$("prev").onclick = () => { if (i > 0) i--; show(); };
$("next").onclick = () => { if (i < PAIRS.length - 1) i++; show(); };
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === "1") setLabel("repaired");
  else if (e.key === "2") setLabel("not_repaired");
  else if (e.key === "3") setLabel("not_comparable");
  else if (e.key === "ArrowLeft") $("prev").click();
  else if (e.key === "ArrowRight") $("next").click();
});
$("dl").onclick = () => {
  const esc = s => '"' + String(s).replace(/"/g, '""') + '"';
  const rows = ["pair_id,label,note"].concat(
    PAIRS.filter(p => labels[p]).map(p => [p, labels[p].label, esc(labels[p].note || "")].join(",")));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([rows.join("\\n") + "\\n"], { type: "text/csv" }));
  a.download = "closure_labels.csv";
  a.click();
};
show();
</script></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    pool = candidates()
    if not pool:
        print("no usable pairs: run qc.py and prefilter.py first", file=sys.stderr)
        return 2
    done = {}
    if LABELS.exists():
        done = {r["pair_id"]: {"label": r["label"], "note": r.get("note") or ""}
                for r in csv.DictReader(LABELS.open(encoding="utf-8")) if r["pair_id"] in pool}
    fresh = [p for p in pool if p not in done]
    chosen = sorted(done) + sorted(random.Random(args.seed).sample(fresh, min(args.n, len(fresh))))
    out = PRIVATE / "label_pairs.html"
    out.write_text(PAGE.replace("__PAIRS__", json.dumps(chosen)).replace("__LABELS__", json.dumps(done)),
                   encoding="utf-8")
    print(f"{len(chosen) - len(done)} new + {len(done)} already labeled, of {len(pool)} usable pairs -> "
          f"{out.relative_to(ROOT)} (open it in your browser)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
