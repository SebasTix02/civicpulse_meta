# What was built during the hackathon

Rules: only work built during the event counts, and judges must be able to tell it apart.
This file is the honest record. The git history is the proof: the first commit is from the
first day of the building period.

## Before the event (Oct 21–26, 2026): research and design only, no product code

| Artifact | Where it lives | How it is used here |
|---|---|---|
| Colab notebook testing SAM 3.1 on pothole photos (intake + before/after closure rules, thresholds) | Not in this repo | The closure rules and thresholds in `services/orchestrator/civicpulse/closure/decision.py` re-implement what the notebook runs taught us; the code is new |
| Design documents in Spanish: plan v3.2, architecture, risks and plan B, UX flows and wireframes, database E-R diagram | Not in this repo | Specification for what we build |
| API contract (OpenAPI 3.1) and JSON Schemas | `packages/contracts/` (copied unchanged on day 1) | The contract the code is tested against (`tests/test_contracts.py`) |
| Civic category catalog v0.1 (21 categories, prompts, es/en names) | `packages/catalog/civic_catalog.json` (copied unchanged on day 1) | Seed of the `categories` table |

## Built during the event

Everything else. Log, newest last:

### Kickoff evening (Oct 26, after the 12:00 ET kickoff) and day 1 (Oct 27)

- Monorepo scaffold, CI (orchestrator tests + lint, database migrations on PostGIS, web build).
- Database: full schema (catalog, geography, RBAC, citizens, consensus, stakeholders, closure, moderation, config) with sandbox isolation by composite foreign keys; RLS; JWT permissions hook; `issue_capture_nonce`, `submit_report` (atomic, idempotent, geofence, 15 m pin rule, rate limits), `get_demo_status`; storage buckets with nonce-bound uploads; SQL smoke test.
- Catalog sync: `catalog.json` + `domains/*.yaml` → idempotent seed SQL.
- Orchestrator: Muse-only LLMRouter with circuit breaker and JSON-retry, spec stage with keyword fallback, asymmetric closure decision (with tests), webhook API, Modal app.
- PWA (es/en): live-camera report flow wired to Supabase (anonymous session, GPS, nonce, resize + EXIF strip, upload, submit), public cluster list, open GeoJSON feed.
- Data: anonymized index of the Ambato pothole repair pairs; private downloader; Colab notebook that anonymizes every photo with SAM 3.1 open weights (people and plates blurred, metadata dropped) before any API sees it.

### Day 2 (Oct 28)

- Vision stage: local anonymization on the CPU, SAM 3.1 grounding through the Meta Model API, Muse Spark crop check, and a second wider view before a report is confirmed.
- Pipeline: sweeper for stuck reports, live report status for the citizen, spatial clustering with weighted consensus, routing of validated clusters to the stakeholders that cover them.
- Moderation signals: reused photos (hash + perceptual hash) and impossible travel between a device's reports.
- Simulator v1 (consensus scenarios with simulated devices) and five bus-stop categories in the catalog.
- Data: hand labels for every real before/after pothole pair (five labels, 629 pairs) and a robustness test of the vision stage on degraded photos.

### Day 3 (Oct 29)

- Photo-quality warning in the PWA (dark, overexposed, blurry) before sending.
- Stakeholder brief for each dispatch, citing real reports and places, translated to the reader's language.
- Stakeholder panel: invite-only sign-in, inbox, case view.
- Closure: the crew's repair photo is verified (SAM 3.1 on both photos, alignment, Muse Spark comparison) and a person approves it; a GPS path for photos taken from another angle, with a same-place question.
- Fictitious test stakeholders (RRT, WST, SBX) instead of real institutions.

### Day 4 (Oct 30)

- Replay demo: record a real run and play it back without Supabase; health cron for honest demo levels; public map of the cases.
- PWA and panel: readable errors, "my reports", the wireframe design, whole photos with a viewer, camera button on phones.

### Days 5–6 (Oct 31 – Nov 1)

- Public area and actors' panel split.
- Judges' sandbox at `/sandbox`: anonymous sessions, sample photos, simulated neighbours, the full cycle with daily quotas and cleanup.
- Closure: local re-alignment around each region; evaluation on real pairs with a Markdown report (`docs/eval/`).

### Day 7 (Nov 2)

- Closure: the AFTER check outlines the repair patch next to the hole, with its own shape; re-evaluation of the aligned sets.
- README: results on real data and four real repair pairs.
- Muse Glimmer: LoRA fine-tune for stage `spec` (`services/glimmer`), with synthetic reports labeled by Muse Spark Open and a deterministic grader.
- Voice reports: dictation in the report and sandbox forms with Muse Voice Transcribe (orchestrator `/transcribe`, daily quota in migration 21, audio never stored).
- De facto bus stops: official stops from OpenStreetMap (migration 22, `scripts/import_osm_stops.py`); a validated waiting or crowding case more than 200 m from any stop gets an orange ring on the public map, and the stops show from zoom 13.
- First OSM import: 759 nodes (195 named) over the canton. Migration 23: de facto only up to 1 km (farther is probably unmapped), re-imports replace the set; the script merges nodes closer than 15 m. The map says "mapped stop", not "official stop".
- Sandbox: two consensus paths (quick, or each simulated neighbour placed by hand with its kind: own phone, same Wi‑Fi, unclear photo; outside the case radius it opens another case). Sandbox pins must fall inside the city boundary (migration 24).
- The seed box also covered parts of Pelileo, Cevallos and Tisaleo: migration 25 shrinks it, `scripts/import_city_boundary.py` loads the canton polygon, the sandbox map shades everything outside it. The path (quick or by hand) is now chosen in the report form. Dictation can be cancelled while recording or transcribing.
- Minimal moderation panel `/panel/admin` (migration 26): flagged / hidden / recent reports with hide, keep and restore (the case recounts, and leaves the map when no visible report is left), the catalog's status (activate / retire; the orchestrator reads it every minute) and the history from audit_log. Roles from `invite_actor.py --role city_admin --city ambato` and `--role catalog_editor`.
