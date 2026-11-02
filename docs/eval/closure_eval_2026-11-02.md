# Closure eval, 2026-11-02 (after f606f31, aligned sets only)

`python scripts/eval_closure.py --sets repaired,not_repaired,before_as_after --gps --figures --report docs/eval/closure_eval_2026-11-02.md`: 44 new runs, 320 SAM 3.1 requests, about USD 0.86. Anonymized pothole repair pairs from a public entity in Ambato, labeled by hand (data/pothole_pairs/closure_labels_v2.csv); synthetic sets are marked as such in the script's docstring.

```
set               runs  resolved  persists  not verif.  top reasons
repaired            20         4         3          13  insufficient_evidence 7, region_not_visible 5, confuser_or_occlusion 1  [13 by GPS]
not_repaired         4         0         2           2  insufficient_evidence 1, region_not_visible 1  [3 by GPS]
before_as_after     20         0        14           6  initial_issue_not_confirmed 5, confuser_or_occlusion 1

FALSE RESOLVED: 0/24 negatives (goal 0)
repaired sent to human review as candidates: 4/20
aligned runs where the region window was re-aligned locally: 16/17
```

## Compared with the run of 2026-11-01 (same 44 pairs)

| | 2026-11-01 | 2026-11-02 |
|---|---|---|
| False "resolved" on negatives (goal 0) | 0/24 | **0/24** |
| Real repairs sent to a human as candidates | 5/20 | 4/20 |
| Aligned runs re-aligned locally around the region | 16/17 | 16/17 |

What changed in between:

- **The repair has its own shape** (f606f31): on the aligned path the AFTER crop no longer copies the
  hole's outline from the BEFORE photo. It outlines the new asphalt patch SAM finds next to the
  hole (within a quarter of the hole's size, at least 15 px), the classifier is told that outline
  is the repair, and `region_checks[].repair_box_xyxy` records it. A patch far from the hole does
  not count. bach_0211, the case where the outline used to land on the sidewalk, is still a
  candidate (Muse "no issue" 0.96, was 0.92).
- **The one candidate lost, bach_0050, is not from this change.** It goes through the GPS path,
  which f606f31 did not touch: SAM found the repair and Muse said "no issue" (0.94, was 0.90)
  both times, but this time the same-place question on the full photos did not reach 0.7, so it
  became "insufficient evidence". That is run-to-run variance of the safety gate, on the safe side.
- Every other verdict is the same as on 2026-11-01. The other_place and bad_unaligned sets were not
  re-run: they only use the GPS path, which did not change.
