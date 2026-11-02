# Muse Glimmer for stage `spec`

A small specialist for the router's last model link: Muse Glimmer 30B (open weights, Apache 2.0)
fine-tuned with LoRA to turn a citizen report into the spec JSON, with Muse Spark Open as the
teacher. No Fireworks needed: labels come from Baseten, training and serving run on Modal.

| Step | Command | Cost |
|---|---|---|
| 1. Data | `python services/glimmer/make_dataset.py --dry-run`, then without `--dry-run` | about USD 4 of Baseten |
| 2. Check | `modal run services/glimmer/train_modal.py --probe-only` | cents (CPU) |
| 3. Train + eval | `modal run services/glimmer/train_modal.py` | about USD 4 to 6 of Modal (1 h of H100) |
| 4. Serve (optional) | `modal deploy services/glimmer/serve_modal.py`, then `GLIMMER_*` in `civicpulse-llm` | only while it runs |

- Data is synthetic (no citizen text). Batch 0 of each category is held out, so test reports are
  never near copies of training reports. A label is kept only when the teacher agreed with the
  category the report was written for.
- The grader is deterministic (`services/orchestrator/civicpulse/spec_grade.py`): valid JSON,
  category, severity within 1, personal-data flag, SAM prompts when the problem is visual. It
  scores the keyword fallback, base Glimmer and tuned Glimmer on the same held-out reports.
- Results: `docs/eval/glimmer_spec_eval.md`.
