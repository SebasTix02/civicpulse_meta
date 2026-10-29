# Pothole repair pairs (Ambato)

Real before/after photos of pothole repairs from the pothole reporting service
of a public entity in Ambato (Ecuador), December 2025 to October 2026. Used with the
data owner's permission for evaluating CivicPulse's closure verification.

| | |
|---|---|
| Cases | 1,262 (after dropping 5 test rows) |
| With before **and** after photo | 1,079 |
| Status | attended 1,075 · reassigned 122 · received 64 · in progress 1 |
| Institutions | city, province, water utility |

## What is in git and what is not

| File | In git | Content |
|---|---|---|
| `pairs.csv` | yes | Anonymized index: `pair_id`, status, parish, urban/rural, institution, crew, coordinates rounded to ~110 m, dates without time |
| `data/private/pairs_manifest.csv` | **no** | `pair_id` → source id, exact coordinates, photo URLs |
| `data/private/raw/` | **no** | Original photos |
| `data/private/raw.zip` | **no** | Originals packed for your own Google Drive |
| `data/private/anon/` | **no** | Anonymized photos (no metadata; people, faces and close vehicles blurred) |
| The source export (`.xlsx`) | **no** | Has citizen names and phone numbers. Never commit it |

## Steps

Everything is anonymized **before** any Meta Model API call: the hackathon's free API credits may be
used by Meta for product improvement, so the API only ever sees anonymized copies.

```bash
python data/pothole_pairs/prepare.py ~/Downloads/Admin.xlsx   # 1. index + private manifest
python data/pothole_pairs/download.py --limit 50              # 2. originals + raw.zip (your computer only)
python data/pothole_pairs/qc.py                               # 3. drop logo placeholders, flag reused photos
python data/pothole_pairs/prefilter.py                        # 4. anonymize on your CPU, nothing uploaded
```

`qc.py` gives every photo a status: `ok`, `placeholder` (the provider's logo the app stores when
no photo was taken: 445 of the first 2,341 downloads) or `reused` (the same file attached to several
cases, so it cannot prove what happened at that place). `pairs.csv` gets `usable_as` = `pair`,
`same_photo`, `before_only`, `after_only` or `none`; only `pair` counts for the closure metric, single
photos still feed the intake evaluation. `same_photo` (393 of 494 cases with two good photos) means the
crew uploaded one identical file as both before and after: it proves nothing, so only 101 real pairs are left.

`label_all.py` writes `data/private/label_all.html`, a local page to label by hand every case with
both photos anonymized (629), in batches of 100 (`--batch`). Its CSV is `closure_labels_v2.csv`,
ids, labels and notes only. Labels from the first full pass (2026-10-28):

| Label | Meaning | Cases |
|---|---|---|
| `repaired` | the pothole in "before" is fixed in "after", same place | 68 |
| `not_repaired` | same place, the pothole is still there (bach_0006 and bach_0060: partial repairs) | 4 |
| `not_comparable` | other place, too blurred, or cannot tell | 81 |
| `already_repaired` | the street already looks fixed in "before" | 410 |
| `same_photo` | "after" is the same picture as "before" | 66 |

Most `already_repaired` cases (337) are `same_photo` files showing a repaired street: the crew
uploaded the "after" picture twice. Notes mark another angle or heavy blur on many `repaired` pairs.

`banned.txt` lists photos removed by hand because they are not of the road (a drawing, an identity
document). `download.py` skips them, `qc.py` marks them `banned` and deletes their anonymized copy,
and `prefilter.py` never processes them. Delete the originals from `data/private/raw/` yourself.

`prefilter.py` (`pip install "opencv-python-headless<5" pillow numpy`) runs two small OpenCV Zoo
detectors on your CPU: YuNet for faces and NanoDet-Plus for people and vehicles, on the whole photo
plus four tiles, with low thresholds. Every detected box is padded, pixelated and blurred (the whole
person; the whole vehicle when it is close enough for a plate to be legible). Each photo is then
saved to `data/private/anon/` with orientation applied, at most 1600 px, and the JPEG rebuilt
without EXIF. About one second per photo; `--max-minutes` and `--resume` split a long run.

- `data/private/prefilter.csv`: decision (`clean` or `blurred`), what was found, and
  `blurred_share`. Above ~0.4 the blur may also hide the pothole: check those and keep them out of
  the evals.
- `data/private/review/clean_NN.jpg` and `blurred_NN.jpg`: contact sheets with the photo name under
  each thumbnail. Skim them for anyone the detectors missed (residual risk: a person very small or
  partly hidden).

On a first sample of 40 real photos, 3 out of 4 had a person or a vehicle, which is why the
default is to blur here instead of sending those to Colab.

**Optional, finer masks:** `prefilter.py --sam` leaves flagged photos untouched in `needs_sam.zip`.
Upload it to `MyDrive/civicpulse/` and run [`anonymize_sam31_colab.ipynb`](anonymize_sam31_colab.ipynb)
on a free T4: SAM 3.1 open weights segment every **person** and **license plate** (plus an OpenCV
face detector as a safety net) and blur only those pixels; resumable across Colab sessions. Review
its contact sheet, then unzip `anon.zip` into `data/private/anon/`.

## How CivicPulse uses it

1. **Closure eval (main metric):** the hand labels in `closure_labels_v2.csv`. The `repaired`
   pairs are the positives; real `not_repaired` pairs are too few, so the negatives come from
   other sources (see the plan). Goal: **zero false "resolved"**.
2. **Intake eval:** the before photos check that SAM 3.1 finds the pothole.
3. **Seed of the public map:** attended cases go through the real pipeline (day 8) so the map
   shows history, never with names, phones or exact coordinates.

Photos never go through the Meta API Contributor tier (it may use data for training).
