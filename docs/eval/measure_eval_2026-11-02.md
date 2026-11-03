# Pothole size and depth eval, 2026-11-02

`python scripts/eval_measure.py --stability --report docs/eval/measure_eval_2026-11-02.md` on anonymized "before" photos of real pothole repairs from a public entity in Ambato, labeled by hand (data/pothole_pairs/size_depth_labels.csv). SAM 3.1 through the Meta Model API, depth cues from Muse Spark; the code is the production one (civicpulse/measure.py, VisionStage._measure).

```
photos: 70 (0 failed) · pothole found by SAM: 39/70 (56%) · road found: 37/39 (95%) · street runs off the photo (clipped): 19/37 (51%)

SIZE
  all measured                         exact 17/37 (46%) · within one class 33/37 (89%)
  street fits in the photo (by hand)   exact 16/31 (52%) · within one class 27/31 (87%)
  not clipped (what adds priority)     exact 11/18 (61%) · within one class 16/18 (89%)
  rank correlation width_share vs label: 0.38
  size (rows: by hand, columns: measured)
                small   medium    large
       small        6        6        0
      medium        2        4        6
       large        4        2        7

DEPTH (photo only, no text)
  exact 13/35 (37%) · within one level 31/35 (89%) · no answer (model unsure or down) 4
  high by hand measured low: 2
  depth (rows: by hand, columns: measured)
                  low   medium     high
         low        4        2        2
      medium        3        6        6
        high        2        7        3

PRIORITY (photo bonus = size + depth, production weights)
  rank correlation with urgency by hand: before none (every photo had the same bonus), now 0.42
  urgency 1:  27 photos, mean bonus 0.17
  urgency 2:  23 photos, mean bonus 0.41
  urgency 3:  20 photos, mean bonus 0.68
  urgent photos in the top third by bonus: 14/20 (70%) (by chance about 33%)

STABILITY (same photo, central 85% scaled back)
  median change of width_share 0.034 · same size class 27/34 (79%)

latency per photo: median 38.7 s
cost of this run: 184 SAM requests + 39 Muse calls, about USD 0.57
```
