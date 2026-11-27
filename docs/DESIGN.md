# Design: AI Image Understanding & Content Matching Engine

*FlyRank Backend Track capstone · Phase 1 design doc · revised after Phase 3 (what changed and why: BUILDLOG.md, 2026-11-27)*

## Problem
Blog posts need the right image. Matching on filenames or keywords fails, and "closest vector wins" is worse because it confidently pairs a wolf with a fox post. This service reads ~50 images with a vision model and stores **validated, structured metadata** for each. It embeds image descriptions and post text into one semantic space and ranks images per post. Every candidate then passes a **mismatch guard** (tags + similarity threshold + confidence) before a human reviews it. When nothing is good enough, the service answers **"no confident match"** and explains why. Quality is measured with **top-1 precision** on a small hand-labeled eval set.

## Image metadata schema (validated with Pydantic; `extra="forbid"`)
```json
{ "subject": "red fox", "category": "animal",
  "attributes": ["orange fur", "wild", "forest"],
  "caption": "A red fox standing in a forest", "confidence": 0.94 }
```
Invalid output is retried with a repair prompt; if it still fails, the image is marked `failed` and an alert fires. It is never stored as tagged. `confidence < 0.70` or an unresolvable subject → `flagged`, and flagged images are never suggested.

## Matching strategy and guard rules
- **Embed:** image = `caption + subject + attributes`; post = `title + body`; one embedding model with task type `SEMANTIC_SIMILARITY`.
- **Rank:** cosine similarity (pgvector `<=>`, exact search), every image of the tenant, per post.
- **`subject_key`** (what is this about?): both sides are reduced to a short subject phrase (the image's vision `subject`; the post's topic, read by a structured and validated text call), embedded, and matched to the nearest taxonomy label (fox, wolf, dog, bear, deer). It must clear a minimum similarity and lead the next in-scope label by a margin; if the nearest label is an out-of-scope anchor (cat, bird, other canid, not an animal…), it is `unknown`.
  *Revised in Phase 3:* comparing a whole post with short label descriptions gave fox/wolf margins of 0.003–0.014, and a nearest-label rule with no "none of the above" sent "domestic cat" to dog. The topic phrase + anchors fixed both (measured in BUILDLOG).
- **Guard:** a pure function. A candidate is ACCEPTED only if **all** of these pass, and every failing check becomes a reason:
  1. the image is not flagged and `confidence ≥ τ_conf`
  2. the post subject is known
  3. `post.subject_key == image.subject_key` → otherwise *"Animal category mismatch: expected fox, detected wolf"*
  4. `similarity ≥ τ_sim`
- The suggestion is the top ACCEPTED candidate; if there is none, the answer is `no_confident_match` with reasons. `τ_sim` is tuned by sweeping the eval set: zero cross-species false accepts, then the best top-1 precision, then general posts still answered, then the most refusals (result: 0.78).

## Data model (PostgreSQL 16 + pgvector, Alembic migrations, every table has `tenant_id`)
| Table | Purpose | Key indexes |
|---|---|---|
| images | file, sha256, status, subject, subject_key, category, caption, confidence, flag_reasons | unique(tenant, sha256); (tenant, status); (tenant, subject_key) |
| image_tags | subject / category / attribute tags | (tenant, tag) |
| posts | slug, title, body, topic, subject_key | unique(tenant, slug) |
| embeddings | owner (image·subject·post·label), model, vector | unique(tenant, owner_type, owner_id, model) |
| suggestions | post × image, rank, similarity, verdict, reasons, checks | unique(tenant, post, image, guard_version); (tenant, post, rank) |
| reviews | approve / reject per suggestion | unique(suggestion_id) |
| jobs, job_items | batch progress, retries, backoff | (status, next_run_at) |
| ai_calls | per-call cost log: provider, model, op, tokens, cost, attribution | (tenant, created_at); (job_id) |
| alerts, idempotency_keys | failure alerts; replay-safe POSTs | (tenant, created_at); pk(tenant, key) |

## API surface (FastAPI; tenant via `X-Tenant-Id`; bad input → 4xx, never 500)
`POST /jobs` (202, Idempotency-Key) · `GET /jobs/{id}` · `GET /images[?status=flagged]` · `GET /images/{id}` · `POST|GET /posts` · **`GET /posts/{id}/images`** (ranked + guarded) · **`POST /posts/{id}/candidates`** (force one image through the guard) · `GET /suggestions` · `GET /suggestions/{id}/explain` · `POST /suggestions/{id}/approve|reject` (Idempotency-Key) · `GET /costs` · `GET /alerts` · `GET /health`

## Layer sketch
```
HTTP      app/api/          routers, request validation, error → 4xx mapping, tenant dependency
Logic     app/services/     pipeline · embedding · taxonomy · guard (pure) · matching · review · evaluation · snapshot
Data      app/repositories/ tenant-scoped SQLAlchemy queries          ── PostgreSQL + pgvector
AI        app/providers/    gemini | ollama | fake, wrapped by tracked.py (cost log + budget guard)
Jobs      app/jobs/         worker container: SKIP LOCKED claim, backoff+jitter, rate limit, alerts
```

## Explicit non-goal
**Not an image search engine.** There is no free-text search endpoint and no frontend build. The system answers only one question: *"Which image fits this post, and is it good enough?"* Model comparison is also out of scope; it is a stretch goal at most.
