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

1. **Report.** A citizen describes the problem in any language, typing or dictating it (Muse Voice Transcribe; the audio is not kept), and takes a live photo. No account: an anonymous session is the device identity.
2. **Understand.** Muse Spark turns the text into an English spec (category, severity, what it looks like) merged with a curated catalog of 21 civic categories.
3. **See.** SAM 3.1 segments what the spec describes; Muse Spark checks each crop. A photo never rejects a report; it only adds evidence. Faces and plates are blurred before anything is public.
4. **Agree.** Reports cluster in space; weighted consensus (device trust, distinct devices, reused-photo and GPS checks) validates a cluster.
5. **Route.** Validated clusters go to the responsible stakeholder with a brief that cites real reports and places. Bus-stop cases are measured against the stops mapped in OpenStreetMap: people waiting 200 m to 1 km from the nearest stop show on the public map as a *de facto stop*, a hint for where the city should plan one. Farther than 1 km we only say the area has no mapped stops, because OSM is a community map, dense downtown and sparse in rural parishes, not the transit office's register.
6. **Close.** An after photo is aligned to the before photo; SAM 3.1 and Muse Spark compare the same region. Doubt (shadow, water, occlusion, bad alignment) never becomes "resolved".

## Meta models

| Model | Where | Role |
|---|---|---|
| **SAM 3.1** | Meta Model API (fallback: official weights on a Modal GPU) | Where the problem is, counting people, anonymization |
| **Muse Spark Open** | Baseten (hackathon credits; fallback hosts Fireworks / Modal Endpoints) | Text → spec, crop and before/after classification, briefs |
| **Muse Spark 1.3** | Meta Model API | Second provider for the same prompts (Meta credits are kept for SAM 3.1) |
| **Muse Voice Transcribe** | Meta Model API | Voice reports: a clip of up to 60 s becomes text the citizen reviews before sending; daily quota per device |
| **Muse Glimmer** | Open weights on Modal, LoRA fine-tuned ([services/glimmer](services/glimmer)) | Specialist for text → spec, trained and measured but not served live (see below), trained on Muse Spark Open's labels: 52/52 categories right on held-out reports vs 94% base and 36% for keywords ([eval](docs/eval/glimmer_spec_eval.md)); after it, catalog keywords and human review |

Muse Spark Open runs at `reasoning_effort=low` for every stage: these are short, well-defined tasks, and more thinking costs tokens without better answers (the same result Fireworks reported for Muse on easy benchmarks).

### Why tuned Glimmer is a fallback and not the first choice

Text → spec, the stage Glimmer was tuned for. Measured numbers come from our runs; the rest is
marked as an estimate.

| Option | Category right | Cost per spec | Cost while idle | First answer | Notes |
|---|---|---|---|---|---|
| **Muse Spark Open on Baseten** (first today) | 92% agreement with the intended category on 154 synthetic reports (first labeling run) | about USD 0.0036 (1,511 tokens in, 411 out, measured) | 0 | about 3.5 s | Pay per token, nothing to run |
| Muse Spark 1.3, Meta Model API (second) | not measured on this set | Meta credits, list USD 1.25 / M input | 0 | about 15 s (hidden reasoning) | Credits kept for SAM 3.1 |
| **Tuned Muse Glimmer on Modal**, scales to zero | **52 / 52** held-out reports (base Glimmer 94%) | GPU time only while it runs | 0 | minutes (estimate: 60 GB of weights load on a cold H100) | Too slow to start for the router's 90 s timeout |
| Tuned Muse Glimmer on Modal, always on | same | about USD 4 per H100 hour (estimate) | about USD 96 per day | seconds | Cheaper than Baseten only above about 1,100 specs per hour |
| Catalog keywords (last link) | 36% | 0 | 0 | instant | Then a person reviews |

The 52 held-out reports were kept only where the teacher agreed with the intended category, so they
are easier than real reports. Training it cost about USD 4 of Baseten labels plus one H100 on Modal
(13 minutes of training).

When a city runs CivicPulse at volume, or has to keep reports on its own servers, tuned Glimmer
becomes the better first choice: open weights, no data leaves the city, and it matches its teacher on this
task. At pilot volume (a few hundred reports a day), paying per token is far cheaper than an always-on
GPU.

## Results on real data

Closure check on anonymized before/after pothole repairs from a public entity in Ambato, labeled
by hand, plus synthetic negatives (`scripts/eval_closure.py --gps`, 84 runs, about USD 1.63 of
Meta credits, 2026-11-01). Full table and the comparison with the previous run:
[docs/eval/closure_eval_2026-11-01.md](docs/eval/closure_eval_2026-11-01.md). After the AFTER crop started outlining the
repair patch itself, the aligned sets were run again (44 runs, about USD 0.86): still 0 / 24 false
"resolved" ([docs/eval/closure_eval_2026-11-02.md](docs/eval/closure_eval_2026-11-02.md)).

| | Result |
|---|---|
| False "resolved" on cases that are not fixed (goal 0) | **0 / 44** |
| Another place's repair, said near by GPS (worst case) | 0 / 20 candidates |
| Real repairs sent to a human as "looks fixed" | 5 / 20 (4 / 20 on the re-run of 2026-11-02) |
| The rest of the real repairs | "cannot verify" (other angle, blur): a human decides |

The AI never closes a case: at best it marks a candidate, and a person approves it.

### Four real repairs, four honest answers

All photos are anonymized (faces, people and close vehicles blurred, no metadata). Each pair is
the BEFORE photo from the report and the AFTER photo from the repair crew.

| Pair | What the system said | Why |
|---|---|---|
| **bach_0211** | Candidate, a human confirms | Photos aligned; the AFTER crop outlines the new asphalt patch next to the hole, with its own shape; Muse: no issue (0.96) |
| **bach_0007** | Candidate, a human confirms | Shot from another angle, so no alignment; the AFTER GPS is close, SAM finds the patch, Muse says no issue and that both photos show the same place |
| **bach_0209** | Cannot verify | Another angle and a weak match: the system will not guess, a human decides |
| **bach_0051** | Cannot verify | Aligned, but something covers part of the region (confuser or occlusion) |

**bach_0211**, before and after, then the region the classifier compared (left BEFORE, right AFTER):

![bach_0211 before and after](docs/img/eval/bach_0211_pair.jpg)
![bach_0211 region check](docs/img/eval/bach_0211_check.jpg)

**bach_0007**, before and after, then the region check:

![bach_0007 before and after](docs/img/eval/bach_0007_pair.jpg)
![bach_0007 region check](docs/img/eval/bach_0007_check.jpg)

**bach_0209**, before and after:

![bach_0209 before and after](docs/img/eval/bach_0209_pair.jpg)

**bach_0051**, before and after, then the region check:

![bach_0051 before and after](docs/img/eval/bach_0051_pair.jpg)
![bach_0051 region check](docs/img/eval/bach_0051_check.jpg)

Try these same pairs in the judges' sandbox at `/sandbox`. There, neighbours confirm a report in one of two ways: the quick path (three good simulated neighbours) or by hand, placing each neighbour on the map as a phone of its own, a phone on the visitor's Wi‑Fi or a photo that shows nothing, so the consensus rules can be seen at work. Pins stay inside the pilot city.

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
- City boundary: `python scripts/import_city_boundary.py` loads the Ambato canton polygon from OpenStreetMap (Nominatim); every report, real or sandbox, must fall inside it.
- Official bus stops: `python scripts/import_osm_stops.py` loads them from OpenStreetMap. Bus stops © OpenStreetMap contributors, available under the [ODbL](https://www.openstreetmap.org/copyright).

## License

MIT
