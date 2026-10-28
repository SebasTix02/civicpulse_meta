"""Build a local page to hand-label every pothole case, 100 at a time, with five labels.

    python data/pothole_pairs/label_all.py               # every case with at least one anonymized photo
    python data/pothole_pairs/label_all.py --batch 50    # smaller batches

Opens nothing on the network: it writes data/private/label_all.html, which shows the anonymized
photos from data/private/anon/ side by side (a missing side says why: no photo, logo placeholder).
Open it in your browser (double click) and label with the keyboard:

  1 repaired          the pothole in "before" is clearly fixed in "after", same place
  2 not_repaired      same place, the pothole (or part of it) is still there
  3 not_comparable    different place or angle, too dark, too blurred, or you cannot tell
  4 already_repaired  the street already looks repaired in "before" too, so the pair proves nothing
  5 same_photo        "after" is the same picture as "before", or there is no "after" at all

Every label is kept in the browser at once. At the end of each batch the page asks you to press
"Guardar CSV" and save the file as data/pothole_pairs/closure_labels_v2.csv (pair ids, labels and
notes only, safe to commit). Running the script again preloads that file, so you can continue in
another browser; "Cargar CSV" does the same from the page. The first time, the 93 labels of
closure_labels.csv come preloaded so you only revise them.
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
LABELS = HERE / "closure_labels_v2.csv"
OLD_LABELS = HERE / "closure_labels.csv"
LABEL_IDS = ("repaired", "not_repaired", "not_comparable", "already_repaired", "same_photo")
WHY_MISSING = {"placeholder": "logo del proveedor (sin foto)", "missing": "sin foto", "banned": "excluida",
               "reused": "sin copia anonimizada"}


def cases() -> list[dict]:
    blurred = {}
    report = PRIVATE / "prefilter.csv"
    if report.exists():
        blurred = {Path(r["photo"]).stem: float(r["blurred_share"] or 0)
                   for r in csv.DictReader(report.open(encoding="utf-8"))}
    out = []
    for row in csv.DictReader((HERE / "pairs.csv").open(encoding="utf-8")):
        case = {"id": row["pair_id"], "usable_as": row["usable_as"], "days": row["days_to_attend"]}
        for side in ("before", "after"):
            stem = f"{row['pair_id']}_{side}"
            has = (ANON / f"{stem}.jpg").exists()
            case[side] = has
            case[f"{side}_why"] = "" if has else WHY_MISSING.get(row[f"{side}_status"], "sin foto")
            case[f"{side}_blur"] = round(blurred.get(stem, 0), 2)
            case[f"{side}_reused"] = row[f"{side}_status"] == "reused"
        if case["before"] or case["after"]:
            out.append(case)
    return out


def preload(ids: set[str]) -> tuple[dict, str]:
    for path in (LABELS, OLD_LABELS):
        if path.exists():
            rows = {r["pair_id"]: {"label": r["label"], "note": r.get("note") or ""}
                    for r in csv.DictReader(path.open(encoding="utf-8"))
                    if r["pair_id"] in ids and r["label"] in LABEL_IDS}
            return rows, path.name
    return {}, ""


PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Etiquetar casos de baches</title>
<style>
 :root { color-scheme: light dark; --bg:#f6f6f4; --fg:#1d1d1b; --muted:#6b6b66; --card:#fff; --line:#ddd;
   --c1:#2e7d32; --c2:#c62828; --c3:#8d6e00; --c4:#1565c0; --c5:#6a1b9a; --warn:#b45309; }
 @media (prefers-color-scheme: dark) { :root { --bg:#161615; --fg:#ecece8; --muted:#a3a39c; --card:#222220;
   --line:#3a3a37; --c1:#66bb6a; --c2:#ef5350; --c3:#d4b106; --c4:#64b5f6; --c5:#ce93d8; --warn:#f59e0b; } }
 body { margin:0; font:15px/1.4 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
 header { display:flex; gap:10px; align-items:center; flex-wrap:wrap; padding:8px 16px; border-bottom:1px solid var(--line); }
 header b { font-size:17px; } .muted { color:var(--muted); } .warn { color:var(--warn); font-weight:600; }
 select, button, input { font:inherit; }
 .pair { display:grid; grid-template-columns:1fr 1fr; gap:8px; padding:8px 16px; }
 .pair figure { margin:0; background:var(--card); border:1px solid var(--line); border-radius:6px; overflow:hidden; }
 .pair img, .pair .none { width:100%; height:calc(100vh - 250px); object-fit:contain; background:#000; display:block; }
 .pair .none { display:flex; align-items:center; justify-content:center; color:#bbb; background:#111; }
 figcaption { padding:4px 8px; font-weight:600; }
 .actions { display:flex; gap:8px; flex-wrap:wrap; padding:6px 16px; align-items:center; }
 button { padding:7px 12px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer; }
 button[data-l] { border-width:2px; }
 button.sel { outline:3px solid currentColor; font-weight:700; }
 input[type=text] { padding:7px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--fg); flex:1; min-width:220px; }
 .strip { display:flex; flex-wrap:wrap; gap:3px; padding:4px 16px 12px; }
 .strip span { width:14px; height:14px; border-radius:3px; background:var(--line); cursor:pointer; }
 .strip span.cur { outline:2px solid var(--fg); }
 #banner { display:none; margin:8px 16px; padding:10px 14px; border-radius:6px; border:2px solid var(--c1); background:var(--card); }
 @media (max-width: 700px) { .pair { grid-template-columns:1fr; } .pair img, .pair .none { height:34vh; } }
</style></head><body>
<header><b id="title"></b><span class="muted" id="progress"></span><span id="flags"></span><span style="flex:1"></span>
 <label>Lote <select id="batch"></select></label>
 <button id="prev">← Anterior</button><button id="next">Siguiente →</button><button id="skip">Siguiente sin etiqueta</button>
 <button id="dl">Guardar CSV</button><button id="ul">Cargar CSV</button><input type="file" id="file" accept=".csv" hidden></header>
<div id="banner"></div>
<div class="pair">
 <figure><div id="before"></div><figcaption>ANTES</figcaption></figure>
 <figure><div id="after"></div><figcaption>DESPUÉS</figcaption></figure>
</div>
<div class="actions">
 <button data-l="repaired" style="border-color:var(--c1)">1 · Reparado</button>
 <button data-l="not_repaired" style="border-color:var(--c2)">2 · No reparado</button>
 <button data-l="not_comparable" style="border-color:var(--c3)">3 · No comparable</button>
 <button data-l="already_repaired" style="border-color:var(--c4)">4 · Ya reparado en el antes</button>
 <button data-l="same_photo" style="border-color:var(--c5)">5 · Misma foto / sin después</button>
 <input type="text" id="note" placeholder="Nota opcional (Enter para guardarla; p. ej. otro ángulo, parche parcial)">
</div>
<div class="strip" id="strip"></div>
<script>
const CASES = __CASES__, BATCH = __BATCH__, SOURCE = __SOURCE__;
const LABELS = ["repaired", "not_repaired", "not_comparable", "already_repaired", "same_photo"];
const COLORS = { repaired:"var(--c1)", not_repaired:"var(--c2)", not_comparable:"var(--c3)",
                 already_repaired:"var(--c4)", same_photo:"var(--c5)" };
const KEY = "civicpulse-pothole-labels-v2";
let labels = __LABELS__;
try { Object.assign(labels, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) {}
const $ = id => document.getElementById(id);
const nBatches = Math.ceil(CASES.length / BATCH);
let i = CASES.findIndex(c => !labels[c.id]); if (i < 0) i = 0;
let savedUpTo = -1;  // last batch already offered for download in this visit

for (let b = 0; b < nBatches; b++) {
  const o = document.createElement("option");
  o.value = b; o.textContent = `${b + 1} (casos ${b * BATCH + 1}–${Math.min(CASES.length, (b + 1) * BATCH)})`;
  $("batch").appendChild(o);
}
function save() { try { localStorage.setItem(KEY, JSON.stringify(labels)); } catch (e) {} }
function side(c, s) {
  if (!c[s]) return `<div class="none">${c[s + "_why"]}</div>`;
  return `<img src="anon/${c.id}_${s}.jpg" alt="${s}">`;
}
function batchOf(k) { return Math.floor(k / BATCH); }
function batchDone(b) {
  return CASES.slice(b * BATCH, (b + 1) * BATCH).every(c => labels[c.id]);
}
function show() {
  const c = CASES[i], b = batchOf(i);
  $("title").textContent = c.id;
  $("before").innerHTML = side(c, "before");
  $("after").innerHTML = side(c, "after");
  const done = CASES.filter(x => labels[x.id]).length;
  const inBatch = CASES.slice(b * BATCH, (b + 1) * BATCH);
  $("progress").textContent = `caso ${i - b * BATCH + 1}/${inBatch.length} del lote ${b + 1}/${nBatches} · ` +
    `lote ${inBatch.filter(x => labels[x.id]).length}/${inBatch.length} · total ${done}/${CASES.length}`;
  const flags = [];
  if (c.before_reused || c.after_reused) flags.push("foto repetida en otros casos");
  if (Math.max(c.before_blur, c.after_blur) > 0.4) flags.push("muy difuminada");
  if (c.usable_as === "same_photo") flags.push("mismo archivo antes y después");
  $("flags").innerHTML = flags.length ? `<span class="warn">${flags.join(" · ")}</span>` : "";
  $("batch").value = b;
  $("note").value = (labels[c.id] && labels[c.id].note) || "";
  document.querySelectorAll("button[data-l]").forEach(x =>
    x.classList.toggle("sel", !!labels[c.id] && labels[c.id].label === x.dataset.l));
  const strip = $("strip"); strip.innerHTML = "";
  inBatch.forEach((x, k) => {
    const s = document.createElement("span");
    if (labels[x.id]) s.style.background = COLORS[labels[x.id].label];
    if (b * BATCH + k === i) s.className = "cur";
    s.title = x.id + (labels[x.id] ? " · " + labels[x.id].label : "");
    s.onclick = () => { i = b * BATCH + k; show(); };
    strip.appendChild(s);
  });
  if (batchDone(b) && savedUpTo < b) {
    $("banner").style.display = "block";
    $("banner").innerHTML = `<b>Lote ${b + 1} completo.</b> Pulsa <b>Guardar CSV</b> y guárdalo como ` +
      `<code>data/pothole_pairs/closure_labels_v2.csv</code> (reemplaza el anterior: lleva todos los lotes). ` +
      `Las teclas 1–5 quedan en pausa hasta que guardes; los botones siguen funcionando.`;
  } else $("banner").style.display = "none";
}
function setLabel(l) {
  const c = CASES[i];
  labels[c.id] = { label: l, note: $("note").value.trim() };
  save();
  const b = batchOf(i);
  if (batchDone(b) && savedUpTo < b) { show(); return; }  // stop at the batch end to offer the download
  const nextOpen = CASES.findIndex((x, k) => k > i && !labels[x.id]);
  i = nextOpen >= 0 ? nextOpen : Math.min(i + 1, CASES.length - 1);
  show();
}
function csv() {
  const esc = s => '"' + String(s).replace(/"/g, '""') + '"';
  return ["pair_id,label,note"].concat(CASES.filter(c => labels[c.id])
    .map(c => [c.id, labels[c.id].label, esc(labels[c.id].note || "")].join(","))).join("\\n") + "\\n";
}
document.querySelectorAll("button[data-l]").forEach(x => x.onclick = () => setLabel(x.dataset.l));
$("note").oninput = () => { const c = CASES[i]; if (labels[c.id]) { labels[c.id].note = $("note").value.trim(); save(); } };
$("note").onkeydown = e => { if (e.key === "Enter") $("note").blur(); };
$("prev").onclick = () => { if (i > 0) i--; show(); };
$("next").onclick = () => { if (i < CASES.length - 1) i++; show(); };
$("skip").onclick = () => { const k = CASES.findIndex((x, k) => k > i && !labels[x.id]); if (k >= 0) i = k; show(); };
$("batch").onchange = () => {
  const b = +$("batch").value, k = CASES.findIndex((x, k) => batchOf(k) === b && !labels[x.id]);
  i = k >= 0 ? k : b * BATCH; show();
};
$("dl").onclick = () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv()], { type: "text/csv" }));
  a.download = "closure_labels_v2.csv";
  a.click();
  savedUpTo = Math.max(savedUpTo, ...[...Array(nBatches).keys()].filter(batchDone));
  if (batchDone(batchOf(i))) { const k = CASES.findIndex(x => !labels[x.id]); if (k >= 0) i = k; }
  show();
};
$("ul").onclick = () => $("file").click();
$("file").onchange = async () => {
  const text = await $("file").files[0].text();
  let n = 0;
  for (const line of text.split(/\\r?\\n/).slice(1)) {
    const m = line.match(/^([^,]+),([^,]+),"?(.*?)"?$/);
    if (m && LABELS.includes(m[2])) { labels[m[1]] = { label: m[2], note: m[3].replace(/""/g, '"') }; n++; }
  }
  save(); alert(`${n} etiquetas cargadas`); show();
};
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  const k = "12345".indexOf(e.key);
  if (k >= 0 && $("banner").style.display === "block") return;  // batch finished: save first
  if (k >= 0) setLabel(LABELS[k]);
  else if (e.key === "ArrowLeft") $("prev").click();
  else if (e.key === "ArrowRight") $("next").click();
  else if (e.key === "n") { e.preventDefault(); $("note").focus(); }
});
if (SOURCE) console.log("preloaded from " + SOURCE);
show();
</script></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=100)
    args = ap.parse_args()
    found = cases()
    if not found:
        print("no anonymized photos in data/private/anon/: run qc.py and prefilter.py first", file=sys.stderr)
        return 2
    done, source = preload({c["id"] for c in found})
    out = PRIVATE / "label_all.html"
    out.write_text(PAGE.replace("__CASES__", json.dumps(found)).replace("__BATCH__", str(args.batch))
                   .replace("__SOURCE__", json.dumps(source)).replace("__LABELS__", json.dumps(done)),
                   encoding="utf-8")
    batches = -(-len(found) // args.batch)
    print(f"{len(found)} cases in {batches} batches of {args.batch}, {len(done)} preloaded"
          f"{' from ' + source if source else ''} -> {out} (open it in your browser)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
