# Build Plan: AI Image Understanding & Content Matching Engine

**Source of truth:** *FlyRank Internship · Backend Track · Capstone Brief: AI Image Understanding & Content Matching Engine* (11 pages).
Every item below cites the brief section it comes from (§N). If this plan and the brief ever disagree, the brief wins.

**One-line goal (§1):** look at an image library, understand each image, tag it, and match images to blog posts by meaning, not by filenames or keywords. A red-fox post gets the red-fox photo and never the wolf. When nothing is good enough, the system says so and explains why.

---

## 0 · Decisions at a glance

| Decision | Choice | Brief reference / reason |
|---|---|---|
| Language + framework | **Python 3.12 + FastAPI** | §10 allows Node+Express or Python+FastAPI. Python gets Pydantic's `model_validate_json()` (§14), which is named in the brief. |
| Schema validation | **Pydantic v2** | §10 |
| Vision model | **`gemini-3.5-flash-lite`**, free tier (AI Studio key, no card); set by `GEMINI_VISION_MODEL` | §10, §14. One vision model is enough (§7). The pricing page (checked 2026-11-25) routes `gemini-2.5-flash` to it from 2026-12-01, and it lists image input explicitly. |
| Local / offline path | **Ollama** (`moondream` or `llava`, plus `all-minilm`), switched on with `AI_PROVIDER=ollama` | §10. Lets a stranger run it with no key. |
| Embeddings | **`gemini-embedding-001`**, 768 dimensions, task type `SEMANTIC_SIMILARITY` | §14 (Phase 3 resource). One embedding model is enough (§7). `gemini-embedding-2` doesn't accept `task_type`. |
| Database | **PostgreSQL 16 + pgvector** via Docker (`pgvector/pgvector:pg16`) | §10: "pgvector optional at ~50 images". Uses exact cosine search; no ANN index is needed at this size. |
| Migrations | **Alembic** | §13 shared requirement #4: "schema as migrations" |
| Background jobs | **Postgres-backed job queue** (`jobs` + `job_items`, `FOR UPDATE SKIP LOCKED`) in a separate `worker` container | §4 part 4, §13 shared #3. No Redis, so there is one fewer service to run. |
| Run command | `docker compose up -d --build` | §11: "A stranger can run it" |
| Repo | **`flyrank-capstone-image-relevance`**, public from day one, MIT license, default branch `main` | §11 |
| Corpus | **50 images, 5 subject classes** (red fox, wolf, dog, bear, deer) plus 4–5 deliberately hard images, from Unsplash/Pexels, resized, committed with a manifest | §3, §7 (≥40 images, ≥4 categories) |
| Explicit non-goal | **This is not an image search engine.** No free-text search and no frontend build. | §1, §7 |

> **Wording note.** §7 uses "categories" to mean subject classes (fox, wolf, dog…). The §4 schema field `category` is coarser (`"animal"`). This plan calls the fine-grained class **`subject_key`** and keeps `category` exactly as the schema shows it.

---

## 1 · Traceability: the contract

### 1a. §6 Requirements: where each is built and what proves it (one proof per box goes in `EVIDENCE.md`)

| # | §6 requirement | Built in | Proof to paste in EVIDENCE.md |
|---|---|---|---|
| AI-1 | Vision output is validated against a schema; invalid responses are never trusted | Phase 2 · `domain/schemas.py`, `services/pipeline.py` | Output of `test_invalid_vision_output_is_rejected`, plus an `ai_calls` row with `status=invalid_output` |
| AI-2 | Low-confidence classifications are flagged, not accepted | Phase 2 · `services/pipeline.py` | curl `GET /images?status=flagged` showing ≥1 image with a reason |
| AI-3 | Images are processed through a batch background job with retries | Phase 2 · `jobs/worker.py` | curl `GET /jobs/{id}` (progress counts), plus output of `test_item_retried_then_succeeds` |
| AI-4 | Vision and embedding costs are tracked per call | Phase 2 · `providers/tracked.py` | curl `GET /costs?job_id=…` with per-call rows and totals |
| M-1 | Image and post embeddings are stored; posts return ranked image suggestions | Phase 3 · `services/ranking.py` | curl `GET /posts/{fox-post}/images` |
| M-2 | Semantic matching works for equivalent concepts: "red fox" matches "Vulpes vulpes" | Phase 3 | curl `GET /posts/{vulpes-post}/images`, where the post never uses the word "fox", showing a fox image ranked first |
| S-1 | The guard rejects wrong recommendations: the wolf-on-a-fox-post case provably fails | Phase 3 · `services/guard.py` | curl `POST /posts/{fox-post}/candidates {wolf image}` → `REJECTED`, plus a unit test |
| S-2 | Rejections include a human-readable explanation | Phase 3 | Same transcript: `"Animal category mismatch: expected fox, detected wolf"` |
| S-3 | When no image clears the bar, the system answers "no confident match" with reasons | Phase 3 | curl `GET /posts/{penguin-post}/images` → `no_confident_match` with reasons |
| B-1 | Database models for images, tags, embeddings, posts, suggestions, approvals/rejections, with the required indexes | Phase 2 migration (+ Phase 3/4 additions) | `\d+` psql output, or the migration file listing its indexes |
| B-2 | API endpoints are validated; the review workflow (approve / reject / inspect why) exists | Phase 4 · `api/` | curl of a 422 on bad input, plus approve, reject, and explain transcripts |
| Q-1 | A labeled eval set measures top-1 precision, and the number is in the README | Phase 4 · `services/eval.py` | `python -m app.cli eval` output, plus the README excerpt |
| Q-2 | README with architecture explanation and diagram; the §11 required files present | Phase 4 | `ls` of the repo root, plus the README diagram section |

### 1b. §13 Layer 2 acceptance probes: how an evaluator runs each one

| Probe | What the evaluator does | Our command / endpoint | Pass condition |
|---|---|---|---|
| **P1** | Runs the batch job on the corpus | `POST /jobs {"type":"process_images"}` (or `python -m app.cli process --wait`) → `GET /jobs/{id}` | Every image has schema-valid tags; **≥1 image `flagged`** (low confidence), not guessed |
| **P2** | Queries images for the "red fox" article | `GET /posts/the-behavior-of-red-foxes/images` | A fox image is rank 1; wolf and dog rank **clearly** lower (the similarity gap is shown) |
| **P3** | Forces the wolf as a candidate for the fox post | `POST /posts/the-behavior-of-red-foxes/candidates {"image_id": <wolf>}` | `REJECTED`, reason `"Animal category mismatch: expected fox, detected wolf"` |
| **P4** | Queries a post with no suitable image | `GET /posts/emperor-penguins-huddle/images` | `"status": "no_confident_match"` with reasons (similarity below threshold / subjects don't match) |
| **P5** | Runs the eval script | `docker compose exec -T api python -m app.cli eval` | Top-1 precision printed and **equal to the number in the README** |
| **P6** | Checks the cost log | `GET /costs` | Every vision and embedding call has a cost entry, attributed to tenant, job, and entity |

### 1c. §13 Shared requirements that every capstone must show

| # | Requirement | How this project meets it |
|---|---|---|
| 1 | Layered architecture: data / logic / HTTP separated | `api/` (HTTP) → `services/` (logic) → `repositories/` (data). AI clients sit behind `providers/`. Routers never touch SQL. |
| 2 | Validation at the boundary: bad input gets a clean 4xx, never a 500 | Pydantic request models; UUID path params (422); unknown id (404); conflicting review (409); oversized body (413); a global handler maps domain errors to 4xx. Tests fire garbage at every endpoint. |
| 3 | ≥1 background job: retries + failure alert | `worker` container; exponential backoff with jitter; `max_attempts`; dead items create an `alerts` row, an `ERROR` log, and an optional `ALERT_WEBHOOK_URL` POST |
| 4 | Real persistence: migrations, the right indexes, isolated tenants | Alembic; every table has `tenant_id` and indexes lead with it; every repository call needs a tenant. Tenant comes from the `X-Tenant-Id` header (defaults to `demo`). An isolation test shows tenant B gets a 404 on tenant A's post. |
| 5 | Idempotency where it matters: a retried action happens once | `Idempotency-Key` on `POST /jobs` and approve/reject (`idempotency_keys` table replays the stored response). Images are deduped by `sha256`. Vision is skipped if metadata already exists for `(sha256, model, prompt_version)`. Suggestions are upserted on `(post_id, image_id, guard_version)`. |
| 6 | Secrets clean: env only, encrypted if stored, never logged | `GEMINI_API_KEY` lives only in `.env` and is read as a pydantic-settings `SecretStr`. A log-redaction filter keeps it out of logs, and a test asserts it never appears. No secrets are stored in the DB. |
| 7 | Cost tracked when AI is used: per call, attributed, with a budget guard | `ai_calls` table with one row per attempt; prices come from `config/pricing.yaml`; `AI_BUDGET_USD` is checked **before** every call, and if it would be exceeded the job stops and an alert is raised. |

### 1d. §11 Required files

`README.md` · `capstone.yaml` · `EVIDENCE.md` · `BUILDLOG.md` · `.env.example`, plus `LICENSE` (MIT) and `.gitignore` (the "DO" list).

---

## 2 · Architecture

The flow follows §5 exactly. Two embedding streams meet at ranking, and everything passes the guard before a human sees it:

```
Images ─(batch job)→ Vision Model → {subject, category, attributes, caption, confidence}
          │                          │ validate (Pydantic) ── invalid → retry w/ repair → fail+alert
          │                          │ confidence < τ_conf  → status=flagged (never suggested)
          │                          ▼
          │                     image_metadata + image_tags
          └─ embed(caption + subject + attributes) ─────────→ embeddings (owner=image)

Posts ─(batch job)→ embed(title + body) ────────────────────→ embeddings (owner=post)
taxonomy labels ─→ embed(label text) ───────────────────────→ embeddings (owner=label)

GET /posts/:id/images
  → Similarity ranking (cosine: image vectors × post vector, top-k)
  → Mismatch Guard (subject/category tags + similarity threshold + confidence)
       ├─ ACCEPTED → suggestion (ranked, explained)
       └─ none accepted → "no_confident_match" + reasons
  → Review API: approve / reject / explain

Every provider call ─→ tracked wrapper ─→ ai_calls (cost, tokens, latency, attribution) + budget guard
```

### Code layout (layered, §13 #1)

```
app/
  api/            HTTP only: routers, request/response models, error handlers, tenant dependency
  services/       logic: pipeline.py, ranking.py, guard.py (pure, no I/O), review.py, eval.py, costs.py
  repositories/   data access: SQLAlchemy, always tenant-scoped
  providers/      base.py (protocols) · gemini.py · ollama.py · fake.py (tests) · tracked.py (cost + budget)
  jobs/           worker.py (loop) · queue.py (enqueue/claim) · retry.py (backoff, rate limiter)
  domain/         Pydantic: ImageMetadata, GuardDecision, enums
  db/             models.py, session.py
  config.py       pydantic-settings (SecretStr for keys)
  cli.py          seed | process [--wait] | eval [--sweep]
migrations/       Alembic
config/           taxonomy.yaml · guard.yaml (tuned thresholds) · pricing.yaml
data/images/      committed corpus (opaque names, e.g. img_7f3a.jpg) + manifest.csv (source, photographer, license)
data/posts.yaml   demo + eval posts
eval/labels.yaml  post → the one correct image (or null for "no match expected")
tests/
docs/DESIGN.md    the Phase 1 one-page design doc
docker-compose.yml · Dockerfile · capstone.yaml · README.md · EVIDENCE.md · BUILDLOG.md · .env.example · LICENSE
```

Images get **opaque filenames** on purpose. That way nothing can match on a filename (§1: "not filenames or keywords"). The true labels exist only in `eval/labels.yaml`.

---

## 3 · Data model (§6 B-1, §13 #4)

All tables have `tenant_id`, `created_at`, `updated_at`.

| Table | Key columns | Indexes / constraints |
|---|---|---|
| `tenants` | `id`, `name` | pk |
| `images` | `sha256`, `file_path`, `source_url`, `license`, `photographer`, `status` (`pending·processing·tagged·flagged·failed`), `subject`, `subject_key`, `subject_key_score`, `category`, `caption`, `confidence`, `flag_reasons jsonb`, `vision_model`, `prompt_version`, `raw_response jsonb` | **unique(tenant_id, sha256)** · ix(tenant_id, status) · ix(tenant_id, subject_key) |
| `image_tags` | `image_id`, `tag`, `kind` (`subject·category·attribute`) | pk(image_id, kind, tag) · **ix(tenant_id, tag)** |
| `posts` | `slug`, `title`, `body`, `subject_key`, `subject_key_score`, `status` | **unique(tenant_id, slug)** |
| `embeddings` | `owner_type` (`image·post·label`), `owner_id`, `model`, `dim`, `text_hash`, `vector` (pgvector) | **unique(owner_type, owner_id, model)** · ix(tenant_id, owner_type, model) |
| `suggestions` | `post_id`, `image_id`, `rank`, `similarity`, `verdict` (`accepted·rejected`), `reasons jsonb`, `checks jsonb`, `thresholds jsonb`, `guard_version` | **unique(tenant_id, post_id, image_id, guard_version)** · ix(tenant_id, post_id, rank) |
| `reviews` (approvals/rejections) | `suggestion_id`, `decision` (`approved·rejected`), `note`, `reviewer` | **unique(suggestion_id)** · ix(tenant_id, decision) |
| `jobs` | `type`, `status` (`queued·running·succeeded·partial·failed`), `total`, `processed`, `succeeded`, `flagged`, `failed`, `error` | ix(tenant_id, status) |
| `job_items` | `job_id`, `item_type`, `item_id`, `status`, `attempts`, `max_attempts`, `next_run_at`, `locked_at`, `last_error` | unique(job_id, item_id) · **ix(status, next_run_at)** (worker claim) |
| `ai_calls` (cost log) | `job_id`, `job_item_id`, `provider`, `model`, `operation` (`vision·embed`), `entity_type`, `entity_id`, `attempt`, `status` (`ok·invalid_output·error·rate_limited`), `input_tokens`, `output_tokens`, `tokens_estimated`, `cost_usd numeric(12,8)`, `tier` (`free·paid·local`), `latency_ms` | ix(tenant_id, created_at) · ix(job_id) · ix(entity_type, entity_id) |
| `alerts` | `job_id`, `level`, `message` | ix(tenant_id, created_at) |
| `idempotency_keys` | `key`, `request_hash`, `status_code`, `response jsonb` | pk(tenant_id, key) |

No ANN (HNSW) index on vectors: exact search over about 50 rows is instant. Say so in the README limitations (§10: "in-DB arrays fine at this scale").

---

## 4 · Vision schema and flagging (§4 part 1, §6 AI-1/AI-2)

The schema has exactly the five fields from §4 and is strict:

```python
class Category(str, Enum):
    animal = "animal"; plant = "plant"; landscape = "landscape"
    person = "person"; food = "food"; object = "object"; other = "other"

class ImageMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject:    str   = Field(min_length=2, max_length=60)        # "red fox"
    category:   Category                                          # "animal"
    attributes: list[str] = Field(min_length=1, max_length=10)    # ["orange fur", "wild", "forest"]
    caption:    str   = Field(min_length=10, max_length=200)      # "A red fox standing in a forest"
    confidence: float = Field(ge=0.0, le=1.0)                     # 0.94
```

- The schema is sent to Gemini as `response_schema` (structured output). **It is validated again** with `ImageMetadata.model_validate_json()`, because the provider's structured mode is not trusted on its own.
- **Invalid output** → retry with a repair prompt that includes the validation error (max 2 repairs). Every attempt is logged in `ai_calls` with `status=invalid_output`. After the last failure the image is set to `status=failed` and an alert is raised. It is **never** saved as tagged.
- **Flagged, not accepted** when either:
  1. `confidence < τ_conf` (starts at 0.70; tuned in Phase 4), or
  2. the subject can't be resolved to a taxonomy `subject_key` with enough margin (see §5). This is a second signal that does **not** depend on the model's self-reported confidence. *(It needs embeddings, so it is added in Phase 3.)*

  Flagged images remain visible in `GET /images?status=flagged` and are never auto-suggested.
- The prompt gives a confidence rubric: lower confidence for blurry or distant shots, partial animals, and look-alike species. The prompt is versioned (`prompt_version`) so results can be reproduced.

---

## 5 · Matching and the mismatch guard (§4 parts 2–3, §6 M-1…S-3)

### Embedding text
- **Image:** `caption + subject + attributes`, embedded once per image.
- **Post:** `title + body`.
- Both use task type `SEMANTIC_SIMILARITY`, so captions and post text land in one comparable space (§14).

### Ranking
`ORDER BY embedding <=> :post_vector LIMIT k` over the tenant's images that have the same `model`. The query returns `similarity = 1 − cosine_distance`. Ranking is **pure similarity**, which keeps it transparent; the guard then decides each candidate.

### `subject_key`: what the image shows vs. what the post is about
`config/taxonomy.yaml` holds one short description per class:

```yaml
fox:  "fox — small wild canid with a bushy tail and pointed ears"
wolf: "wolf — large wild canid that lives and hunts in packs"
dog:  "domestic dog — a pet dog of any breed"
bear: "bear — large heavy mammal such as a brown, black or grizzly bear"
deer: "deer — hoofed grazing mammal with antlers, including fawns and elk"
```

Each label is embedded once (and cost-tracked).

- Image `subject_key` = the nearest label to `embed(subject)`.
- Post `subject_key` = the nearest label to the post vector.
- Both need `similarity ≥ τ_label` **and** a lead of at least `δ_margin` over the runner-up; otherwise the key is `unknown`.

The labels deliberately **do not contain "Vulpes vulpes" or "red fox"**. That keeps the M-2 proof honest: the embedding has to do the semantic work.

### The guard: `services/guard.py`, a pure function with no I/O

§1 says: "Build the guard as its own small module first."

```
evaluate(post, image, similarity, cfg) -> GuardDecision{verdict, reasons[], checks[]}
```

It runs **every** check (no short-circuit), so the explanation lists everything that failed:

| Check | Fails when | Human-readable reason |
|---|---|---|
| `image_confidence` | image is `flagged`/`failed` or `confidence < τ_conf` | `Image classification is low-confidence (0.48 < 0.70); flagged for review` |
| `post_subject_known` | post `subject_key = unknown` | `Could not tell what the post is about (best label 'fox' at 0.41 < 0.55)` |
| `subject_match` (tag validation) | `post.subject_key ≠ image.subject_key` | **`Animal category mismatch: expected fox, detected wolf`** (the exact §4 wording) |
| `similarity` | `similarity < τ_sim` | `Semantic similarity 0.41 is below the threshold 0.62` |

- `ACCEPTED` only if all checks pass. Each `checks[]` entry stores `{rule, passed, observed, threshold}`. This is what `GET /suggestions/{id}/explain` returns ("inspect *why*", §4 part 5).
- **Post level:** the suggestion is the highest-ranked `ACCEPTED` candidate. If there is none, the response is `{"status": "no_confident_match", "reasons": [...]}` with a summary, e.g. *"best similarity 0.41 < 0.62; top candidates' subjects [wolf, dog, bear] don't match expected fox"* (§6 S-3, P4).

### Response shape: `GET /posts/{id}/images`

```json
{
  "post_id": "…", "post_subject": "fox", "status": "match",
  "suggestion": { "image_id": "…", "rank": 1, "similarity": 0.81, "verdict": "ACCEPTED", "reasons": [] },
  "candidates": [
    { "image_id": "…", "rank": 1, "similarity": 0.81, "subject": "red fox", "verdict": "ACCEPTED", "reasons": [] },
    { "image_id": "…", "rank": 4, "similarity": 0.63, "subject": "gray wolf", "verdict": "REJECTED",
      "reasons": ["Animal category mismatch: expected fox, detected wolf"] }
  ],
  "thresholds": { "sim": 0.62, "conf": 0.70, "label": 0.55, "margin": 0.05 }
}
```

### Threshold tuning ("a precision number, not a feeling", §2)
`python -m app.cli eval --sweep` steps `τ_sim` from 0.30 to 0.90 in increments of 0.02. For each value it records: top-1 precision, coverage, correct-refusal rate on negative posts, and **cross-species false accepts** (e.g. a wolf accepted on a fox post).

Pick the τ with the highest precision subject to **zero cross-species false accepts**. Commit it to `config/guard.yaml` with the date and the eval-set hash, and put the sweep table in the README.

---

## 6 · Background processing (§4 part 4, §6 AI-3, §13 #3, #5)

- **Enqueue:** `POST /jobs {"type": "process_images" | "process_posts", "force": false}` with an `Idempotency-Key` returns `202 {job_id}`. It never blocks the request. The CLI `process --wait` enqueues and then polls.
- **Worker** (its own container) claims work with `SELECT … FROM job_items WHERE status='queued' AND next_run_at <= now() ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1`.
- **Retries:** transport errors, 5xx, and 429 back off exponentially with jitter (`base · 2^attempt ± jitter`, `max_attempts=3`). Invalid schema output gets a repair-retry (§4).
- **Failure alert:** an item that exhausts its retries is marked `failed`, and the system writes an `alerts` row, an `ERROR` log, and an optional webhook POST. The job ends `partial`/`failed`.
- **Progress:** `GET /jobs/{id}` returns `total / processed / succeeded / flagged / failed` and the status.
- **Rate limiting:** a token bucket per provider keeps the free tier's RPM in check. Set `VISION_RPM` and `EMBED_RPM` from the Gemini free-tier page (§14).
- **Stale-lock recovery:** `running` items with `locked_at` older than N minutes go back to the queue (this covers a worker crash).
- **Idempotent re-runs:** an image that already has metadata for `(sha256, model, prompt_version)` is skipped unless `force=true`, so a retried job never pays twice.

---

## 7 · Cost tracking and budget guard (§2, §4 part 4, §6 AI-4, §13 #7, P6)

- **Every** provider call goes through `providers/tracked.py`. That wrapper writes one `ai_calls` row per attempt, whatever the outcome (ok, invalid_output, error, rate_limited).
- Tokens come from the Gemini response's usage metadata. Where usage isn't returned (some embedding calls), they are estimated as chars/4 and stored with `tokens_estimated=true`.
- `cost_usd` uses the list prices in `config/pricing.yaml` (USD per 1M tokens), and `tier=free` records that nothing was actually billed. The README explains: *"Billed cost is $0 on the free tier; we log list-price-equivalent cost to keep the habit and enforce a budget."* Ollama calls log `tier=local` and `cost_usd=0`, but tokens and latency are still recorded.
- **Budget guard:** before each call, the wrapper checks `spent + estimated_next > AI_BUDGET_USD`. If true, it raises `BudgetExceeded`, the job stops, and an alert is raised.
- `GET /costs?job_id=&operation=` returns per-call rows plus totals by operation and model.
- Test: with a fake provider, `count(ai_calls) == count(provider invocations)`.

---

## 8 · API surface (§4 part 5, §6 B-2)

All routes are tenant-scoped through `X-Tenant-Id` (default `demo`), and all are validated.

| Method & path | Purpose | Probe / box |
|---|---|---|
| `GET /health` | Liveness, DB reachable | run check |
| `POST /jobs` | Enqueue `process_images` / `process_posts` (Idempotency-Key) → 202 | P1, AI-3 |
| `GET /jobs/{id}` | Progress, counts, status | P1, AI-3 |
| `GET /images?status=&subject_key=` | Image table (shows `flagged`) | P1, AI-2 |
| `GET /images/{id}` | Metadata, tags, flag reasons, its AI calls | AI-1 |
| `POST /posts` · `GET /posts` · `GET /posts/{id_or_slug}` | Posts (create enqueues embedding) | M-1 |
| `GET /posts/{id}/images?limit=5` | Ranked, guarded suggestions; persists suggestions | P2, P4, M-1, M-2, S-3 |
| `POST /posts/{id}/candidates` `{image_id}` | Force one image through the guard | P3, S-1, S-2 |
| `GET /suggestions?verdict=&review=pending` | Review queue (the "table") | B-2 |
| `GET /suggestions/{id}/explain` | Inspect why: every check, observed value vs threshold | B-2 |
| `POST /suggestions/{id}/approve` · `/reject` | Human decision (Idempotency-Key; 409 if it contradicts an earlier decision) | B-2, §13 #5 |
| `GET /costs` | Per-call cost log and totals | P6, AI-4 |
| `GET /alerts` | Job failure alerts | §13 #3 |
| `GET /admin/review` *(optional)* | Server-rendered HTML table; no frontend build (§7) | B-2 |

Error contract: `{"error": {"code": "…", "message": "…"}}`. Malformed id → 422. Unknown id or another tenant's data → 404. Conflict → 409. Oversized body → 413. Clients never see a 500 for bad input.

---

## 9 · Corpus, posts and eval set (§3, §7, §6 Q-1)

**Corpus (~50 images, ≤ ~4 MB total, committed):**

| Class | Count | Variety, so specific posts can single out one image |
|---|---|---|
| red fox | 10 | pouncing in snow, kit at den, city street at night, curled asleep, autumn leaves, arctic fox (white), … |
| wolf | 10 | howling, pack in snow, close-up face, running, … |
| dog | 10 | include a **Shiba Inu** (fox look-alike) and a **husky** (wolf look-alike) to stress the guard |
| bear | 8 | brown bear fishing, black bear in tree, … |
| deer | 8 | fawn, stag with antlers, deer in fog, … |
| **hard / ambiguous** | 4–5 | blurry night camera-trap shot, distant canid silhouette, coyote (not in the taxonomy), heavily cropped fur. These guarantee P1's "≥1 flagged" |

- Sources are Unsplash / Pexels only. Check both license pages (§14). `data/images/manifest.csv` records `file, source_url, photographer, license, true_class`.
- Resize to ≤768 px on the long side (JPEG q≈80, about 60–80 KB each). This keeps the repo small (§11 DON'T) and reduces vision tokens. Optional: `scripts/download_corpus.py` rebuilds the corpus from the manifest.

**Posts (`data/posts.yaml`, ~18):**
- **Probe posts:** "The behavior of red foxes" (P2/P3). "Vulpes vulpes: an adaptable survivor in European cities", written **without the word "fox"** (M-2). "Wild fox species of the northern hemisphere" (M-2).
- **12+ specific eval posts**, each pointing at exactly one image, e.g. "How red foxes hunt mice under the snow" → the snow-pounce image.
- **3 negative posts with no suitable image:** emperor penguins, sourdough baking, a coral reef (P4, S-3).

**Eval set (`eval/labels.yaml`):** ≥10 posts, each with **the one correct image** (§7), plus the negative posts labeled `correct_image: null`. Grow it a little as you go; approved reviews are a good source of new labels.

**Metric definitions (stated in the README):**
- **Headline top-1 precision** (§15): over the positive posts, the share whose first suggested image is the labeled one. A refusal counts as a miss.
- **Secondary metrics:** precision-when-answering, coverage, correct-refusal rate on negative posts, and cross-species false accepts.
- `cli eval` writes `eval/results.json`, and the README number is copied from it. A test fails if the two differ, which keeps P5 honest.

---

## 10 · The build, phase by phase (§8)

The phases run in order and each one ends with its gate. If you are short on time, cut scope (§7) rather than skipping a phase. **Commit at least once per working session** so every phase is visible in the history (§11).

### Before Phase 1: ground rules (§3, §11), ~30 min
- [ ] `gh repo create flyrank-capstone-image-relevance --public`, then connect this folder as its remote.
- [ ] Rename the branch: `git branch -m master main`. The brief says "keep `main` always runnable".
- [ ] Add `.gitignore` (`.env`, `.venv/`, `__pycache__/`, `node_modules/`) **before any `.env` exists**. Add `LICENSE` (MIT), `.env.example`, and an empty `BUILDLOG.md`.
- [ ] Get a Gemini key from AI Studio (Google account, no card). If anything asks for a card, stop (§10 iron rule).
- [ ] Note: local Python is 3.9, so everything runs in Docker (`python:3.12-slim`).

### Phase 1 · Design (≈4–6 h)
- [ ] Image metadata schema: lock `ImageMetadata` exactly as §4 shows it (§4 of this plan).
- [ ] Matching strategy and guard rules sketched (§5 of this plan).
- [ ] Database design (§3 of this plan).
- [ ] Initial ~50-image dataset gathered, licensed, resized, given opaque filenames, recorded in the manifest.
- [ ] *(Recommended by §1)* Write `services/guard.py` as a pure module, with unit tests on hand-written fixtures: fox post + wolf image → `REJECTED` with the exact reason.
- [ ] Review and own `docs/DESIGN.md` (drafted). It must cover problem, data model, API surface, layer sketch, and one explicit non-goal.

**GATE:** the one-page design document is committed to the repository.
Commit: `docs: phase 1 design doc, schema, guard rules`.

### Phase 2 · Image understanding pipeline (≈10–14 h)
- [ ] Scaffold: FastAPI, pydantic-settings, `docker-compose.yml` (db with healthcheck, api, worker, optional `ollama` profile), Dockerfile, Alembic initial migration (all §3 tables and indexes), tenant dependency, error handlers, `/health`.
- [ ] Providers: `VisionProvider` / `EmbeddingProvider` protocols, implemented by Gemini (`response_schema`, `SEMANTIC_SIMILARITY`), Ollama (`format=<json schema>`), and Fake (deterministic, for tests).
- [ ] `tracked.py`: an `ai_calls` row for every attempt, `pricing.yaml`, budget guard.
- [ ] Vision processing job with structured output validation: Pydantic validate → repair-retry → `failed` + alert; flagging rules.
- [ ] Batch processing with retries: `jobs`/`job_items`, SKIP LOCKED worker, backoff with jitter, rate limiter, stale-lock recovery, progress counts, failure alerts, idempotent enqueue.
- [ ] Endpoints: `POST /jobs`, `GET /jobs/{id}`, `GET /images`, `GET /images/{id}`, `GET /costs`, `GET /alerts`.
- [ ] CLI: `seed` (demo tenant, images registered with sha256, posts), `process --wait`.
- [ ] Tests: bad JSON / missing field / `confidence: 1.7` / extra field are rejected and never saved; retry → success; retry → fail → alert; one cost row per call; 4xx on bad input.
- [ ] Fill EVIDENCE.md boxes **AI-1…AI-4**. Add a BUILDLOG entry.

**GATE:** all images are tagged by the batch job and costs are visible (paste `GET /jobs/{id}` and `GET /costs`).
Commits like: `feat(jobs): postgres job queue with retries and alerts`, `feat(vision): gemini structured output + pydantic validation`.

### Phase 3 · Matching engine (≈12–16 h)
- [ ] Embeddings for images and posts (plus taxonomy labels), stored idempotently by `(owner, model, text_hash)`.
- [ ] `subject_key` resolution for images and posts (label similarity + margin; otherwise `unknown`, and unknown images are flagged).
- [ ] Similarity search and ranking (pgvector `<=>`, top-k).
- [ ] The mismatch guard with explanations: wire the pure guard into `GET /posts/{id}/images` and `POST /posts/{id}/candidates`; overall `match` / `no_confident_match`; upsert suggestions.
- [ ] Write the probe posts and negative posts. Set initial thresholds from them (final tuning happens in Phase 4).
- [ ] Tests: wolf on a fox post is rejected with the exact reason; no-match response shape; the Vulpes post ranks a fox first (fake embeddings in unit tests; real ones in an evidence transcript).
- [ ] Fill EVIDENCE.md boxes **M-1, M-2, S-1, S-2, S-3**.

**GATE:** the fox post ranks a fox first, and the guard refuses the wolf (curl transcripts in EVIDENCE.md).

### Phase 4 · Production layer (≈8–12 h)
- [ ] Review API: `GET /suggestions`, `GET /suggestions/{id}/explain`, approve/reject with Idempotency-Key and 409 on contradiction. Optional `/admin/review` HTML table.
- [ ] Evaluation dataset: `eval/labels.yaml` (≥10 positive + negatives); `cli eval` produces `eval/results.json`; `--sweep` picks thresholds; commit `config/guard.yaml`.
- [ ] README: what it does, architecture diagram (§2 of this plan), exact run and seed steps, a probe walkthrough (P1–P6 as copy-paste curls), **the top-1 precision number and sweep table**, and an honest limitations section.
- [ ] `capstone.yaml` (below), EVIDENCE.md complete (all 13 boxes), BUILDLOG.md honest.
- [ ] Hardening pass against the §13 shared table: fuzz bad input on every route (→ 4xx), tenant isolation test, secret-not-logged test.
- [ ] **Clean-machine test:** fresh clone → `docker compose up -d --build` → seed → process → P1–P6 all pass.

**GATE:** eval precision measured.

### FINAL SELF-CHECK (§8)
Go through §6, tick every box, and confirm each has a pasted proof in `EVIDENCE.md`. Then run through §11's non-negotiables, the required files, and §13 P1–P6 one last time from a clean clone.

### Submit (§12)
- [ ] Confirm the repo is public and `main` is green. **Never force-push over the history** (§11 DON'T).
- [ ] Paste the GitHub repo link into the **portal submission form**. **Don't upload a ZIP** or the codebase; that is the most common cause of submission errors.
- [ ] Review is asynchronous. Any questions come back through the portal.

### `capstone.yaml` (draft)
```yaml
name: flyrank-capstone-image-relevance
run: docker compose up -d --build
seed: docker compose exec -T api python -m app.cli seed && docker compose exec -T api python -m app.cli process --wait
test: docker compose exec -T api pytest -q
base_url: http://localhost:8000
endpoints:
  - GET  /health
  - POST /jobs
  - GET  /jobs/{id}
  - GET  /images?status=flagged
  - GET  /posts/the-behavior-of-red-foxes/images
  - POST /posts/the-behavior-of-red-foxes/candidates
  - GET  /posts/emperor-penguins-huddle/images
  - GET  /suggestions
  - GET  /suggestions/{id}/explain
  - POST /suggestions/{id}/approve
  - POST /suggestions/{id}/reject
  - GET  /costs
```

### `.env.example` (draft)
```
AI_PROVIDER=gemini                 # gemini | ollama
GEMINI_API_KEY=your-ai-studio-key-here
GEMINI_VISION_MODEL=gemini-flash-model-id   # use the current Flash id shown in AI Studio
GEMINI_EMBED_MODEL=gemini-embedding-model-id
OLLAMA_URL=http://ollama:11434
OLLAMA_VISION_MODEL=moondream
OLLAMA_EMBED_MODEL=all-minilm
DATABASE_URL=postgresql+psycopg://app:app@db:5432/app
AI_BUDGET_USD=1.00
VISION_RPM=8
EMBED_RPM=60
ALERT_WEBHOOK_URL=
LOG_LEVEL=INFO
```

---

## 11 · Stretch goals: only after every §6 box is green (§9)

"One polished stretch beats three half-stretches." **Recommended:** *a test suite for schema validation, mismatch rejection, and matching accuracy.* It deepens tests that already exist, runs offline with the fake provider plus recorded embeddings, and fits the reviewers' principle: "correct, resilient, and well tested".
Alternatives: automatic alt text from the caption and attributes; near-duplicate detection by embedding distance; human-in-the-loop agent QA for uncertain matches; fallback image generation.

---

## 12 · Risks and mitigations

| Risk | Mitigation |
|---|---|
| Self-reported confidence is poorly calibrated (the model says 0.95 for everything), so P1 shows nothing flagged | Deliberately hard images; a confidence rubric in the prompt; a **second, independent flag** (taxonomy margin / unknown subject). Document it in the limitations. |
| Fox and wolf vectors are close, so similarity alone can't separate them | The guard decides on `subject_key` agreement, not similarity alone. Report the measured fox–wolf similarity gap in the README. |
| Zero-shot post subject is unreliable for long or mixed posts | Ambiguous posts become `unknown`, which correctly produces "no confident match". Fallback: a structured-output text call that extracts the post topic (cost-tracked). |
| Free-tier 429s / daily caps | Token-bucket rate limiter, backoff, resumable jobs, idempotent skip of done images |
| Model ids change over time | All model ids come from env; README says to check AI Studio |
| Evaluator has no Gemini key | README covers the AI Studio key (free, no card) or `docker compose --profile local up` with Ollama. `test:` runs fully offline on the fake provider. |
| Switching providers changes the embedding dimension | `embeddings.model` column and filter; re-embedding when switching is documented |
| Local Python 3.9 | Everything runs in Docker on Python 3.12 |

---

## 13 · Owning the AI-assisted parts (§3)

Log in `BUILDLOG.md` that this plan and `docs/DESIGN.md` were drafted with Claude Code from the brief, and what you changed after reviewing them. As you build, note where AI helped, where it was wrong, and what you changed. You must be able to explain any 2–3 lines the evaluator picks. The areas most likely to be probed are `guard.py`, the worker's claim query, and the threshold sweep, so make sure you know those cold.
