# CivicPulse

**Report any city problem with a photo. Meta's SAM 3.1 and Muse Spark check it, neighbors confirm it, and the city closes it, with AI that never calls a problem "fixed" when in doubt.**

Built for the [Meta Global AI Developer Hackathon](https://cerebralvalley.ai/e/meta-ai-developer-hackathon), track *Build for Your World*. Pilot city: Ambato, Ecuador.

> **Hackathon provenance.** Every line of code in this repository was written during the
> building period (Oct 27 – Nov 4, 2026). Before the event we only did research and design:
> a Colab notebook testing SAM 3.1 on pothole photos, and design documents (plan,
> architecture, API contract, database diagram, UX flows). See
> [docs/HACKATHON.md](docs/HACKATHON.md) for exactly what existed before, what was built
> during the event, and a day-by-day log. The git history is the record.

## How it works

```
citizen (PWA, es/en) ──► Supabase (Postgres + PostGIS + RLS, Storage, Auth)
                               │ Database Webhook
                               ▼
                    Orchestrator (FastAPI on Modal)
     received ─► spec_ready ─► vision_done ─► clustered ─► validated ─► dispatched ─► resolved
                   │              │                                        ▲
         Muse Spark 1.3      SAM 3.1 masks +                     before/after check:
         text → spec JSON    Muse Spark crop check               SAM 3.1 on both photos,
                                                                 homography, classifier,
                                                                 asymmetric decision
```

1. **Report.** A citizen describes the problem in any language and takes a live photo. No account: an anonymous session is the device identity.
2. **Understand.** Muse Spark turns the text into an English spec (category, severity, what it looks like) merged with a curated catalog of 21 civic categories.
3. **See.** SAM 3.1 segments what the spec describes; Muse Spark checks each crop. A photo never rejects a report; it only adds evidence. Faces and plates are blurred before anything is public.
4. **Agree.** Reports cluster in space; weighted consensus (device trust, distinct devices, reused-photo and GPS checks) validates a cluster.
5. **Route.** Validated clusters go to the responsible stakeholder with a brief that cites real reports and places.
6. **Close.** An after photo is aligned to the before photo; SAM 3.1 and Muse Spark compare the same region. Doubt (shadow, water, occlusion, bad alignment) never becomes "resolved".

## Meta models

| Model | Where | Role |
|---|---|---|
| **SAM 3.1** | Meta Model API (fallback: official weights on a Modal GPU) | Where the problem is, counting people, anonymization |
| **Muse Spark 1.3** | Meta Model API | Text → spec, crop and before/after classification, briefs |
| **Muse Spark Open** | Baseten / Fireworks / Modal Endpoints | Fallback for the same prompts |
| **Muse Glimmer** | Open weights | Cheap last model fallback; after it, catalog keywords and human review |

## Repository

| Path | What |
|---|---|
| `apps/web` | Next.js PWA: public map, `/report`, `/sandbox`, `/panel`, open GeoJSON feed. UI in Spanish and English |
| `services/orchestrator` | Python: webhook API, LLMRouter, SAMService, intake pipeline, closure decision, Modal app |
| `supabase` | Migrations (schema, RLS, RPCs, storage), seeds, SQL tests |
| `packages/catalog` | Civic category catalog (seed of the `categories` table) |
| `packages/contracts` | API contract (OpenAPI) and JSON Schemas shared by web and orchestrator |
| `domains` | Domain packs (transit, roads, water, general): stakeholders and closed action lists |
| `data/pothole_pairs` | Anonymized index of 1,079 real before/after pothole pairs from Ambato (evaluation) |
| `scripts` | Catalog sync, DB test runner |

## Run it

```bash
# Orchestrator tests
cd services/orchestrator && pip install -e ".[dev]" && pytest

# Database: any empty Postgres 16 + PostGIS
DATABASE_URL=postgres://postgres@localhost:5432/cp_test scripts/test_db.sh
# or the full local Supabase stack
supabase start && supabase db reset

# Web
cd apps/web && npm ci && npm run dev
```

Copy `.env.example` to `.env` and fill in the keys. Production: Vercel (web), Supabase (data), Modal (orchestrator).

## Data and privacy

- Citizens are anonymous. Raw photos stay in a private bucket; only anonymized copies (faces and plates blurred, EXIF removed) can be public.
- The Ambato pothole pairs are used with the data owner's permission. Names, phone numbers, source ids and exact coordinates never enter this repository; see [data/pothole_pairs/README.md](data/pothole_pairs/README.md).
- The Meta Model API only receives anonymized photos and redacted text: people, faces and plates are blurred locally (SAM 3.1 open weights) before any API call.

## License

MIT
