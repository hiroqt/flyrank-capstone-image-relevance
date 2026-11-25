# AI Image Understanding & Content Matching Engine

FlyRank Internship · Backend Track capstone.

This service looks at an image library and understands what is in each image. It tags each image with **schema-validated** vision output and matches images to blog posts **by meaning**, not by filenames or keywords. Every candidate passes a **mismatch guard** (tags + similarity threshold + confidence) before a human sees it:

- A post about **red foxes** surfaces a red-fox image.
- A similar-looking **wolf** is **rejected**, with an explanation: *"Animal category mismatch: expected fox, detected wolf"*.
- A generic **dog** ranks poorly.
- If no image is good enough, the answer is **"no confident match"**, with reasons.

> **Status: Phase 2 in progress.** The scaffold is done: layered FastAPI app, full database schema as Alembic migrations, error handling, tenant scoping, and the vision-output schema with tests. Not built yet: the vision pipeline, background jobs, cost log, matching, guard, review API and eval. The full plan is in [`PLAN.md`](PLAN.md) and the design doc is in [`docs/DESIGN.md`](docs/DESIGN.md).

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
docker compose up -d --build      # run: Postgres+pgvector and the API on :8000 (migrations apply on boot)
docker compose exec -T api python -m app.cli seed    # seed: demo tenants
curl localhost:8000/health        # {"status":"ok","db":"ok"}
docker compose exec -T api pytest -q                 # test
```

Fully local, with no API key: set `AI_PROVIDER=ollama` and run `docker compose --profile local up -d`.

Postgres is exposed on host port `5433` (change it with `DB_HOST_PORT`) so it doesn't clash with a local Postgres on 5432.

### Tenancy
Every request is scoped to a tenant through the `X-Tenant-Id` header. If the header is missing, the tenant is `demo`. Seed creates `demo` and `other`. A malformed header returns 400; an unknown tenant returns 404.

## Evaluation

_Top-1 precision on the labeled eval set will be reported here (Phase 4)._

## Limitations

- No authentication: the tenant is selected by a header. In production it would come from an auth token.
- Exact vector search with no ANN index. That is fine at ~50 images and would not scale to millions.
- _More will be added honestly as the build progresses._

## License

MIT. Image credits will be listed in `data/images/manifest.csv`.
