# Closure eval, 2026-11-03

`python scripts/eval_closure.py --gps --pairs 68 --workers 3 --resume --report docs/eval/closure_eval_2026-11-03.md`: 1 new runs, 10 SAM 3.1 requests, about USD 0.03. Anonymized pothole repair pairs from a public entity in Ambato, labeled by hand (data/pothole_pairs/closure_labels_v2.csv); synthetic sets are marked as such in the script's docstring.

```
set               runs  resolved  persists  not verif.  top reasons
repaired            63         6        10          47  insufficient_evidence 25, region_not_visible 12, repair_vs_confuser_conflict 8  [48 by GPS]
not_repaired         4         0         2           2  insufficient_evidence 1, region_not_visible 1  [3 by GPS]
before_as_after     63         0        42          21  initial_issue_not_confirmed 20, confuser_or_occlusion 1
bad_unaligned       63         0        36          27  insufficient_evidence 14, region_not_visible 12, initial_issue_not_confirmed 1  [51 by GPS]
other_place         63         0        10          53  insufficient_evidence 32, region_not_visible 12, repair_vs_confuser_conflict 8  [51 by GPS]

FALSE RESOLVED: 0/130 negatives (goal 0)
other place said near by GPS (worst case, reviewer must catch): 0/63 RESOLVED_CANDIDATE
repaired sent to human review as candidates: 6/63
aligned runs where the region window was re-aligned locally: 52/54
```
