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
| `data/private/anon/` | **no** | Anonymized photos (EXIF removed, faces and plates blurred) |
| The source export (`.xlsx`) | **no** | Has citizen names and phone numbers. Never commit it |

## Steps

```bash
python data/pothole_pairs/prepare.py ~/Downloads/Admin.xlsx      # index + private manifest
python data/pothole_pairs/download_and_anonymize.py --limit 50   # needs access to the source server
```

## How CivicPulse uses it

1. **Closure eval (main metric):** a hand-labelled subset (`eval/closure_labels.csv`, day 2)
   with `repaired / not_repaired / not_comparable`. Goal: **zero false "resolved"**.
2. **Intake eval:** the before photos check that SAM 3.1 finds the pothole.
3. **Seed of the public map:** attended cases go through the real pipeline (day 8) so the map
   shows history, never with names, phones or exact coordinates.

Photos never go through the Meta API Contributor tier (it may use data for training).
