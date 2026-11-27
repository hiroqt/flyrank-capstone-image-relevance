# AI Image Understanding & Content Matching Engine

FlyRank Internship · Backend Track capstone.

This service looks at an image library and understands what is in each image. It tags each image with **schema-validated** vision output and matches images to blog posts **by meaning**, not by filenames or keywords. Every candidate passes a **mismatch guard** (tags + similarity threshold + confidence) before a human sees it:

- A post about **red foxes** surfaces a red-fox image.
- A similar-looking **wolf** is **rejected**, with an explanation: *"Animal category mismatch: expected fox, detected wolf"*.
- A generic **dog** ranks poorly.
- If no image is good enough, the answer is **"no confident match"**, with reasons.

It is **not an image search engine** (no free-text search, no frontend). It is a decision system that would rather refuse than guess.

> **Status: all four phases built and run on real data.** 52 Pexels photos and 26 posts were processed by Gemini through the background worker (241 AI calls, $0.074 at list price, billed $0 on the free tier). **Top-1 precision: 100.0% (15/15)** on the labeled eval set, zero cross-species false accepts, 119 tests. Every requirement is proven in [`EVIDENCE.md`](EVIDENCE.md); how it was built (and where AI was wrong) is in [`BUILDLOG.md`](BUILDLOG.md). Plan: [`PLAN.md`](PLAN.md) · design doc: [`docs/DESIGN.md`](docs/DESIGN.md).

## Architecture

```
                 ┌──────────────────────── background worker (batch jobs, retries, cost log) ────────────────────────┐
Images ─────────►│ vision model ─► {subject, category, attributes, caption, confidence} ─► Pydantic validation     │
                 │      invalid ─► repair-retry ─► still invalid: failed + alert       low confidence: flagged     │
                 │ embed(caption + subject + attributes) ─► image vector                                            │
                 │ embed(subject) ─► nearest taxonomy label ─► subject_key (fox…) or unknown: flagged               │
Posts  ─────────►│ text model ─► {subject, category, confidence} (validated) ─► embed(subject) ─► subject_key      │
                 │ embed(title + body) ─► post vector                                                               │
                 └──────────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                                                ▼  PostgreSQL + pgvector (no AI on the request path)
GET /posts/:id/images ─► similarity ranking (cosine, post vector × image vectors)
                      ─► mismatch guard, every check runs:
                           image_confidence · post_subject_known · subject_match (tags) · similarity >= threshold
                      ─► suggestion (ranked, explained)   or   "no_confident_match" + reasons
                      ─► review API: approve / reject / explain        (every AI call ─► ai_calls cost log + budget guard)
```

| Layer | Folder | Responsibility |
|---|---|---|
| HTTP | `app/api/` | Routers, request validation, error → 4xx mapping, tenant dependency, review table |
| Logic | `app/services/` | `pipeline` (worker items), `embedding`, `taxonomy` (subject resolution), **`guard` (pure)**, `matching`, `review`, `evaluation`, `snapshot` |
| Data | `app/repositories/`, `app/db/` | Tenant-scoped queries, SQLAlchemy models, Alembic migrations |
| AI | `app/providers/` | Gemini / Ollama / fake behind one interface; `tracked.py` = cost log + budget guard |
| Jobs | `app/jobs/` | Postgres-backed queue (`FOR UPDATE SKIP LOCKED`), retries with backoff, rate limiting, alerts |

## Run it

You need Docker. Python runs inside the containers.

```bash
cp .env.example .env                       # add your free Gemini AI Studio key (no credit card)
docker compose up -d --build --wait        # run: Postgres+pgvector, API on :8000 (migrations on boot), worker
docker compose exec -T api python -m app.cli seed             # seed: tenants, 52 images, 26 posts (idempotent)
docker compose exec -T api python -m app.cli process --wait   # batch jobs: images (vision + embeddings), posts
docker compose exec -T api python -m app.cli eval             # top-1 precision (probe P5)
docker compose exec -T api pytest -q                          # 119 tests, offline (fake AI + recorded outputs)
```

`process` takes about 8–10 minutes on the free tier (the worker paces itself with `VISION_RPM` / `EMBED_RPM`). Interactive API docs: http://localhost:8000/docs · review table: http://localhost:8000/admin/review

**No API key?** `python -m app.cli seed --snapshot` replays the recorded AI outputs in [`eval/snapshot.json`](eval/snapshot.json) (vision tags, post topics and every vector from the real run) into the `demo` tenant with no AI calls, so probes P2–P5 work immediately. Fully local is also possible: `AI_PROVIDER=ollama` and `docker compose --profile local up -d`.

Postgres is on host port `5433` (`DB_HOST_PORT`) so it doesn't clash with a local Postgres on 5432.

## The acceptance probes, as commands

All requests default to the `demo` tenant (header `X-Tenant-Id`).

```bash
# P1  batch job: every image gets schema-valid tags; the hard ones are flagged, not guessed
curl -X POST localhost:8000/jobs -H 'Content-Type: application/json' -H 'Idempotency-Key: p1' \
     -d '{"type":"process_images","force":true}'                    # 202 + job id; the worker does the work
curl localhost:8000/jobs/<job_id>                                    # total / processed / succeeded / flagged / failed
curl 'localhost:8000/images?status=flagged'                          # each with its flag_reasons

# P2  the red-fox article: a fox ranks first, wolf and dog clearly lower (see by_subject)
curl localhost:8000/posts/the-behavior-of-red-foxes/images

# P3  force the wolf onto the fox post: REJECTED, "Animal category mismatch: expected fox, detected wolf"
WOLF=$(curl -s 'localhost:8000/images?subject_key=wolf&limit=1' | jq -r '.items[0].id')
curl -X POST localhost:8000/posts/the-behavior-of-red-foxes/candidates \
     -H 'Content-Type: application/json' -d "{\"image_id\": \"$WOLF\"}"

# P4  a post with no suitable image: "no_confident_match" + reasons
curl localhost:8000/posts/emperor-penguins-huddle/images

# P5  the eval script: prints the same number as this README
docker compose exec -T api python -m app.cli eval

# P6  the cost log: every vision / text / embedding call, attributed to tenant, job and entity
curl 'localhost:8000/costs?limit=20'

# Review workflow: the queue, inspect why, decide
curl 'localhost:8000/suggestions?review=pending'
curl localhost:8000/suggestions/<id>/explain
curl -X POST localhost:8000/suggestions/<id>/approve -H 'Idempotency-Key: r1' \
     -H 'Content-Type: application/json' -d '{"reviewer":"editor","note":"good"}'
```

The semantic-matching proof (M-2): `curl localhost:8000/posts/vulpes-vulpes-in-european-cities/images`. That post never contains the word "fox", and foxes still rank first.

## How it decides

### 1. The vision model is useful but unreliable
1. Gemini is asked for structured JSON (`response_json_schema`) with exactly the brief's five fields, and every answer is **validated again** with Pydantic (`extra="forbid"`, ranges, enum).
2. An invalid answer gets a repair prompt (up to `VISION_MAX_REPAIRS`). If it is still invalid, the image is `failed` and an alert is raised. Invalid output is never stored.
3. A valid answer is **flagged**, not accepted, if `confidence < VISION_MIN_CONFIDENCE` **or** its subject does not resolve to a known class. Two independent signals, because self-reported confidence is not calibrated: the first prompt version scored a motion-blurred silhouette at 0.80. Prompt v2 caps confidence by conditions visible in the photo (silhouette, blur, tiny subject, darkness, partial crop).

### 2. Subject resolution: what is this about?
Both sides of a match are reduced to a short subject phrase: the image's vision `subject` ("gray wolf"), and a post's **topic**, read by a structured, validated text call ("Vulpes vulpes" → "red fox"). The phrase is embedded and compared with the taxonomy in [`config/taxonomy.yaml`](config/taxonomy.yaml):
- the nearest **in-scope** label (fox, wolf, dog, bear, deer) becomes the `subject_key`, if it scores ≥ `label_min` and leads the next class by ≥ `label_margin`;
- if the nearest label is an **out-of-scope anchor** (other wild canid, cat, bird, sea life, other mammal, not an animal), the key is `unknown`. Without these anchors, nearest-label matching has no "none of the above": "domestic cat" resolved to *dog* and "coral reef" to *deer*.

Measured: real classes lead by 0.049–0.12; "coyote" sits 0.004 between fox and wolf, so the coyote photo is flagged instead of guessed.

### 3. The mismatch guard (`app/services/guard.py`, pure)
Ranking is pure cosine similarity (pgvector, exact search). The guard then decides each candidate and **runs every check**, so a rejection explains everything that failed:

| Check | Fails when | Reason given |
|---|---|---|
| `image_confidence` | the image is flagged/failed or below the confidence threshold | `Image classification is not trusted, flagged for human review: …` |
| `post_subject_known` | the post's subject is `unknown` | `Could not tell what the post is about (closest category is 'bird' …)` |
| `subject_match` (tag validation) | post and image subjects differ | **`Animal category mismatch: expected fox, detected wolf`** |
| `similarity` | cosine similarity < `similarity_min` (0.78) | `Semantic similarity 0.747 is below the threshold 0.78` |

The suggestion is the highest-ranked **ACCEPTED** image. If there is none, the answer is `no_confident_match` with post-level reasons (similarity below threshold / subjects don't match / candidates flagged). Every decision is stored as a `suggestion` with its checks and thresholds, so `GET /suggestions/{id}/explain` can answer "why?" later, and a human can approve or reject it (409 if a decision contradicts an earlier one).

### 4. Batch discipline
All AI work runs in the `worker` container, never on the request path. Jobs live in Postgres (`jobs` / `job_items`, claimed with `FOR UPDATE SKIP LOCKED`). Transient errors (503, 429, timeouts) retry with exponential backoff and jitter. Permanent ones fail fast with an alert (DB row, ERROR log, optional webhook). A crashed worker's items are re-queued. A paid result is committed before the next step, so a retry never pays twice. Re-running a job only does missing work: images and posts already done by the same model, prompt and text are skipped, and a taxonomy change re-resolves subjects without any AI call.

### 5. Cost tracking
Every attempt (vision, text, embedding, including failures) writes one `ai_calls` row with provider, model, tokens, latency, status, and attribution (tenant, job, job item, image/post/label). `cost_usd` is the **list-price equivalent** from `config/pricing.yaml`; on the Gemini free tier nothing is billed (`tier=free`), but the number keeps the habit and drives the **budget guard**: before every call the worker checks `AI_BUDGET_USD`, and if the call would exceed it, the job stops with an alert. The whole build used 241 calls, $0.074 at list price.

### 6. Tenancy, validation, idempotency, secrets
- **Tenancy:** every request is scoped by `X-Tenant-Id` (default `demo`). Every table has `tenant_id`, every query filters by it, and the indexes for tenant queries lead with it (the worker's claim index is deliberately cross-tenant). Another tenant's data is a 404.
- **Validation:** bad input is a clean 4xx with one error shape `{"error": {"code", "message"}}`: 422 for bad bodies/params, 404, 409, 413 for oversized bodies. Never a 500.
- **Idempotency:** `Idempotency-Key` on `POST /jobs` and on approve/reject replays the stored response. Images dedupe by sha256. Suggestions upsert on (post, image, guard version).
- **Secrets:** the key lives only in `.env` (git-ignored), is a `SecretStr`, and is redacted from logs. A test checks it.

## Evaluation

**Top-1 precision: 100.0% (15/15)**: of the 15 posts that each have exactly one correct image, the first suggested image is the labeled one for all 15 (`docker compose exec -T api python -m app.cli eval`, which writes [`eval/results.json`](eval/results.json)).

**The eval set** ([`eval/labels.yaml`](eval/labels.yaml)) was labeled by looking at the photos:
- **15 specific posts**, each with *one* correct image (images with a near-twin, such as the two salmon-fishing bears, were left out because a post about them would have two right answers);
- **3 general posts** (the probe posts, including the Vulpes one), where any photo of the class is right;
- **4 off-topic posts** (penguins, sourdough, coral reef, indoor cats): the right answer is "no confident match";
- **4 hard negatives**: a known class but a species with no photo (polar bear, moose, dalmatian, fennec fox).

**How the threshold was chosen** (`eval --sweep`, `similarity_min` from 0.30 to 0.90): zero cross-species false accepts first, then the highest top-1 precision, then every general post still answered, then the most refusals, then the middle of that plateau. Result: **0.78**, saved to `config/guard.yaml`.

| similarity_min | top-1 | general posts | off-topic refused | hard negatives refused | cross-species (guard) | similarity only: false accepts | similarity only: cross-species |
|---|---|---|---|---|---|---|---|
| 0.60 | 100.0% | 3/3 | 4/4 | 0/4 | **0** | 8/8 | 5 |
| 0.70 | 100.0% | 3/3 | 4/4 | 0/4 | **0** | 7/8 | 4 |
| 0.76 | 100.0% | 3/3 | 4/4 | 0/4 | **0** | 4/8 | 1 |
| **0.78** | **100.0%** | **3/3** | **4/4** | **1/4** | **0** | 3/8 | 1 |
| 0.80 | 100.0% | 2/3 | 4/4 | 3/4 | **0** | 2/8 | 1 |
| 0.84 | 100.0% | 0/3 | 4/4 | 4/4 | **0** | 0/8 | 0 |
| 0.90 | 46.7% | 0/3 | 4/4 | 4/4 | **0** | 0/8 | 0 |

What the numbers say:
- **The fox/wolf boundary holds at every threshold.** The guard made zero cross-species errors across the whole sweep; similarity alone made up to 5 (at 0.78 it would give the polar-bear post the *arctic fox* photo: snow and white fur). On the red-fox post, the best fox scores 0.828 at rank 1, the best wolf 0.785 at rank 11, the best dog 0.747 at rank 28. The closest wolf is above the similarity threshold, and it is the subject check that rejects it.
- **The 15 specific posts are easy for ranking**: the labeled image ranks first by similarity alone for all of them. The guard's value shows on the negatives and the cross-species column, not on top-1.
- **The margin is thin.** At 0.78 the Vulpes post's best accepted fox scores 0.788; at 0.80 that general post is refused.
- The eval runs on recorded AI outputs ([`eval/snapshot.json`](eval/snapshot.json)) replayed into an isolated `eval` tenant, so the number reproduces exactly, for free, without a key. `eval --tenant demo` scores live data instead; since Gemini isn't deterministic, a fresh re-processing can shift a caption and a score slightly. A test fails if this README, `eval/results.json` and a fresh eval disagree.

## Limitations

- **Species inside a class are not told apart.** The taxonomy is class-level (fox, wolf, dog, bear, deer), as the brief's categories are. The polar-bear post gets a brown bear, the moose post a red deer, the fennec post a red fox: 3 of 4 hard negatives are same-class false accepts. A finer taxonomy (species keys) would be the next step.
- **A wrong vision answer stays wrong.** The husky in a forest (`img_b9d0`) is tagged "gray wolf" at confidence 0.80. The guard trusts the subject, so that image could be suggested for a wolf post.
- **The eval set is small and written by one person** (15 + 3 + 8 posts); 100% on 15 posts is not 100% in general. The posts were written from what the photos show, not from the captions.
- **Self-reported confidence is not calibrated.** The prompt's caps helped (7 hard photos flagged instead of 0), but 2 of 6 deliberately hard photos still passed on confidence; the coyote was caught by the subject check instead.
- No authentication: the tenant comes from a header. In production it would come from an auth token.
- Embedding token counts are estimated (chars/4), because the Gemini embeddings endpoint doesn't report usage (`tokens_estimated=true`).
- The budget guard has no cross-worker lock: with several workers, spend can overshoot by at most one call per worker.
- Exact vector search, no ANN index: instant at ~50 images, would not scale to millions.
- `GET /posts/{id}/images` writes: it upserts the suggestions it returns so they can be reviewed. It is idempotent (same ids on repeat), but it is not a pure read.

## Repository map

| Path | What |
|---|---|
| `app/` | the service (layers above) |
| `migrations/` | Alembic schema: 4 migrations, every index explicit |
| `config/` | `taxonomy.yaml`, `guard.yaml` (tuned thresholds), `pricing.yaml` |
| `data/images/`, `data/posts.yaml` | the 52-photo corpus with credits, and the 26 posts |
| `eval/` | `labels.yaml`, `snapshot.json` (recorded AI outputs), `results.json` |
| `tests/` | 119 tests: schema, boundary, worker, guard, matching on real recorded vectors, review, eval |
| `README.md` · `capstone.yaml` · `EVIDENCE.md` · `BUILDLOG.md` · `.env.example` | the required submission files |

## License

MIT for the code. The 52 corpus photos are from Pexels under the [Pexels License](https://www.pexels.com/license/); each photographer is credited in [`data/images/manifest.csv`](data/images/manifest.csv).
