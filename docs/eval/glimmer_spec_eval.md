# Muse Glimmer for stage `spec`, 2026-11-02

LoRA rank 16 on the text layers of Muse Glimmer 30B, 110 steps over 440 synthetic reports labeled by Muse Spark Open (teacher kept only when it agreed with the intended category). One H100 on Modal, training 792 s. Held out: 52 reports from batches never trained on. Greedy decoding, scored by `civicpulse/spec_grade.py` (no LLM judge).

| | json_valid | category | severity_within_1 | personal_data | prompts_when_visual |
|---|---|---|---|---|---|
| keywords | 100% | 36% | 86% | 21% | 31% |
| glimmer_base | 100% | 94% | 100% | 100% | 100% |
| glimmer_tuned | 100% | 100% | 100% | 100% | 100% |

Category: tuned fixed 3 answers of the base model and broke 0.

Checks: valid spec JSON; category equals the intended one (unknown ids count as other); severity within 1 of the teacher; personal-data flag equals the teacher's; SAM prompts present when the problem is photo-verifiable. `keywords` is today's last fallback when every model fails.

## Reading the numbers

- The base model already does well (94% category) because the production prompt carries the whole
  catalog. The 3 it got wrong: road cracks → broken sidewalk, fallen tree → blocked sidewalk, service
  outage → broken streetlight. The tuned model got all 52 right and broke none.
- The other gain is the format: the tuned model answers on the user channel at once, with no
  reasoning turn, so a spec is one short generation.
- The held-out reports are synthetic and the teacher agreed with their intended category, so they
  are easier than real reports. 52 reports are few; read 94% → 100% as "at least as good, never
  worse", not as a precise gain.
- The fair comparison for the system is the last link before human review: today, when every
  hosted Muse provider fails, catalog keywords get the category right 36% of the time.

Cost: Baseten labels about USD 4; Modal, one H100 for the 60 GB download, 13 min of training and
the evaluation.
