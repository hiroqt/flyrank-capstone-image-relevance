# AI Image Understanding & Content Matching Engine

FlyRank Internship · Backend Track capstone.

This service looks at an image library and understands what is in each image. It tags each image with **schema-validated** vision output and matches images to blog posts **by meaning**, not by filenames or keywords. Every candidate passes a **mismatch guard** (tags + similarity threshold + confidence) before a human sees it:

- A post about **red foxes** surfaces a red-fox image.
- A similar-looking **wolf** is **rejected**, with an explanation: *"Animal category mismatch: expected fox, detected wolf"*.
- A generic **dog** ranks poorly.
- If no image is good enough, the answer is **"no confident match"**, with reasons.

> **Status: Phase 2 built; the gate run on the real corpus is pending.** Done: the vision pipeline (Gemini structured output, Pydantic validation, repair-retry, low-confidence flagging), the background worker (retries, backoff, rate limiting, failure alerts, idempotency), the per-call cost log with a budget guard, and the jobs, images, costs and alerts endpoints, with 82 tests. Not built yet: matching, the guard, the review API and eval (Phases 3–4). The full plan is in [`PLAN.md`](PLAN.md) and the design doc is in [`docs/DESIGN.md`](docs/DESIGN.md).

## Architecture

```
Images ─(batch job)→ Vision Model → {subject, category, attributes, caption, confidence} → images + image_tags
          └─ embed(caption + subject + attributes) ──────────────→ embeddings (image)
Posts  ─(batch job)→ embed(title + body) ─────────────────────────→ embeddings (post)

GET /posts/:id/images
  → Similarity ranking (cosine, pgvector)
  → Mismatch Guard (subject tags + similarity threshold + confidence)
       ├─ suggested image (ranked, explained)
       └─ "no_confident_match" + reasons
  → Review API: approve / reject / explain

Every AI call → cost log (ai_calls) + budget guard
```

| Layer | Folder | Responsibility |
|---|---|---|
| HTTP | `app/api/` | Routers, request validation, error → 4xx mapping, tenant dependency |
| Logic | `app/services/` | Pipeline, ranking, guard (pure), review, eval, costs |
| Data | `app/repositories/`, `app/db/` | Tenant-scoped queries, SQLAlchemy models |
| AI | `app/providers/` | Gemini / Ollama / fake clients behind one interface, cost-tracked |
| Jobs | `app/jobs/` | Background worker: retries, backoff, rate limiting, alerts |

## Run it

You need Docker. Python runs inside the container.

```bash
cp .env.example .env              # add your free Gemini AI Studio key (no credit card)
docker compose up -d --build --wait   # run: Postgres+pgvector, API on :8000 (migrations on boot), worker
docker compose exec -T api python -m app.cli seed            # seed: tenants + every image in data/images
docker compose exec -T api python -m app.cli process --wait  # batch job: tag every image (probe P1)
curl localhost:8000/images?status=flagged                    # low-confidence images, flagged not guessed
curl localhost:8000/costs                                    # per-call cost log (probe P6)
docker compose exec -T api pytest -q                         # test (offline: uses a fake provider)
```

The batch job can also be started over HTTP. It returns `202` immediately and the worker does the AI calls:

```bash
curl -X POST localhost:8000/jobs -H 'Content-Type: application/json' \
     -H 'Idempotency-Key: batch-1' -d '{"type":"process_images"}'
curl localhost:8000/jobs/<job_id>       # progress: total / processed / tagged / flagged / failed
curl localhost:8000/alerts              # failure alerts from the worker
```

Interactive API docs: http://localhost:8000/docs

Fully local, with no API key: set `AI_PROVIDER=ollama` and run `docker compose --profile local up -d`, then `docker compose exec ollama ollama pull moondream`.

### How the vision pipeline treats the model
The model is useful but unreliable, and every answer is checked:
1. Gemini is asked for structured JSON (`response_json_schema`), and the answer is **validated again** with Pydantic (`extra="forbid"`, ranges, enum).
2. An invalid answer is retried with a repair prompt (up to `VISION_MAX_REPAIRS`). If it is still invalid, the image is set to `failed` and an alert is raised. Bad output is never stored.
3. A valid answer with `confidence < VISION_MIN_CONFIDENCE` is **flagged** for human review, not accepted.
4. Every attempt, including invalid and failed ones, is a row in the cost log.

### Cost tracking
Every vision and embedding attempt writes one `ai_calls` row with the provider, model, tokens, latency, status and attribution (tenant, job, item, image). `cost_usd` is the **list-price equivalent** from `config/pricing.yaml`. On the Gemini free tier, nothing is billed (`tier=free`); the number keeps the habit and drives the budget guard. Before every call, the worker checks `AI_BUDGET_USD`. If the call would exceed it, the job stops with an alert.

Postgres is exposed on host port `5433` (change it with `DB_HOST_PORT`) so it doesn't clash with a local Postgres on 5432.

### Tenancy
Every request is scoped to a tenant through the `X-Tenant-Id` header. If the header is missing, the tenant is `demo`. Seed creates `demo` and `other`. A malformed header returns 400; an unknown tenant returns 404.

## Evaluation

_Top-1 precision on the labeled eval set will be reported here (Phase 4)._

## Limitations

- No authentication: the tenant is selected by a header. In production it would come from an auth token.
- The model's self-reported confidence is not calibrated. Flagging relies on it, plus (from Phase 3) an independent subject-resolution check.
- Embedding token counts are estimated (chars/4), because the Gemini embeddings endpoint doesn't report usage. Those rows are marked `tokens_estimated=true`.
- The budget guard is checked per call, without a cross-worker lock. With several workers, spend can overshoot by at most one call per worker.
- Exact vector search with no ANN index. That is fine at ~50 images and would not scale to millions.
- _More will be added honestly as the build progresses._

## License

MIT. Image credits will be listed in `data/images/manifest.csv`.
