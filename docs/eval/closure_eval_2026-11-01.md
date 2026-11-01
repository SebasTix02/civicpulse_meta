# Closure eval, 2026-11-01

`python scripts/eval_closure.py --gps --figures --report docs/eval/closure_eval_2026-11-01.md`: 84 new runs, 612 SAM 3.1 requests, about USD 1.63. Anonymized pothole repair pairs from a public entity in Ambato, labeled by hand (data/pothole_pairs/closure_labels_v2.csv); synthetic sets are marked as such in the script's docstring.

```
set               runs  resolved  persists  not verif.  top reasons
repaired            20         5         3          12  insufficient_evidence 6, region_not_visible 5, confuser_or_occlusion 1  [13 by GPS]
not_repaired         4         0         2           2  insufficient_evidence 1, region_not_visible 1  [3 by GPS]
before_as_after     20         0        14           6  initial_issue_not_confirmed 5, confuser_or_occlusion 1
bad_unaligned       20         0        13           7  region_not_visible 5, insufficient_evidence 2  [15 by GPS]
other_place         20         0         2          18  insufficient_evidence 13, region_not_visible 5  [15 by GPS]

FALSE RESOLVED: 0/44 negatives (goal 0)
other place said near by GPS (worst case, reviewer must catch): 0/20 RESOLVED_CANDIDATE
repaired sent to human review as candidates: 5/20
aligned runs where the region window was re-aligned locally: 16/17
```

## Compared with the run of 2026-10-29 (same 84 pairs and sets)

| | 2026-10-29 | 2026-11-01 |
|---|---|---|
| False "resolved" on negatives (goal 0) | 0/43 | **0/44** |
| Real repairs sent to a human as candidates | 7/20 | 5/20 |
| Another place's repair, said near by GPS (worst case) | 4/19 candidates | **0/20** |
| Aligned runs re-aligned locally around the region | – | 16/17 |

What changed in between:

- **Same-place question** (f65722e): a candidate found by GPS, without photo alignment, now also
  needs Muse to say both full photos show the same place (≥ 0.7). That removed all 4 wrong-place
  candidates and cost 2 real ones (bach_0044, bach_0048, now "insufficient evidence"). Every
  other verdict is the same as on 2026-10-29.
- **Local re-alignment** (f5327db): the global homography follows the facades, so on the road the
  AFTER outline could land on the sidewalk or above the patch. The window around each region is
  now re-aligned; it ran in 16 of the 17 aligned runs. No verdict changed: it fixes where the
  outline is drawn in the AFTER crop the classifier and the reviewer see.

Only 3 of the 20 real repairs could be aligned at all (the crews often shot the AFTER from
another angle); the other candidates come from the GPS path.
