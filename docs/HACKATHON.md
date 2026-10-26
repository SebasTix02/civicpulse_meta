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
- Data: anonymized index of the Ambato pothole repair pairs; private downloader + anonymizer (EXIF strip, face and plate blur).
