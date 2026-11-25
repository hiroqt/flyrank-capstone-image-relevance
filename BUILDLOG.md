# BUILDLOG: AI-usage log

Brief §3: "Use AI tools freely, but keep BUILDLOG.md honest: where AI helped, where it was wrong, what you changed."
One entry per working session. Honesty is graded; perfection is not.

---

## 2026-11-25 · Planning, design doc, Phase 2 skeleton

**Where AI helped**
- Claude Code (Opus 5.5) read the capstone brief PDF and drafted `PLAN.md`, a traceability plan that maps every §6 requirement, §13 probe and shared requirement to a build location and an evidence item.
- Claude Code drafted `docs/DESIGN.md`, the Phase 1 one-page design doc.
- Claude Code scaffolded the Phase 2 skeleton: FastAPI app, layered folders, config, Docker Compose, the Alembic initial migration, error handling, tenant dependency, the `ImageMetadata` schema and the first tests.

**Where AI was wrong / what I changed**
- Claude tried to silence a Starlette deprecation warning by replacing the `httpx` test dependency with a package called `httpx2`, which had not been verified. The change was blocked before it ran and was dropped. `httpx` stays, and the warning is harmless. Lesson: never add a dependency just because a warning names it.
- Claude's first body-size middleware raised a plain exception. FastAPI's body parser would have turned that into a generic 400 for streamed (chunked) bodies. It was changed to raise a FastAPI `HTTPException(413)`, and `test_oversized_chunked_body_is_413` now covers it.
- _To fill in after reviewing: list anything you disagreed with, corrected, or rewrote._

**Things I must be able to explain without help**
- _To fill in: e.g. why the guard runs every check instead of stopping at the first failure; why there is a unique index on (tenant_id, sha256)._

---

## 2026-11-25 · Phase 2: vision pipeline, worker, cost log (AI-assisted)

**Where AI helped**
- Claude Code wrote the Phase 2 code: provider adapters (Gemini via `google-genai`, Ollama, fake), the cost ledger and budget guard, the Postgres job queue and worker, the vision pipeline, the jobs/images/costs/alerts endpoints, and 47 new tests (82 total).
- A web lookup of the Gemini pricing page set the defaults: `gemini-2.5-flash` is routed to `gemini-3.5-flash-lite` from 2026-12-01, so the vision default is Flash-Lite (free tier, image input, $0.30/$2.50 per 1M). `gemini-embedding-001` is the embedding model, because `gemini-embedding-2` does not accept `task_type=SEMANTIC_SIMILARITY`.

**Where AI was wrong / what changed**
- The first worker design would have **double-counted** an item if a stale-lock recovery handed it to a second worker while the first was still running. Completion, retry and failure now only apply while the item is still `running`. `test_item_handed_to_two_workers_is_paid_and_counted_once` covers it.
- The cost log line printed Decimal zero as `$0E-8`; it's now formatted to 6 decimal places.
- The real-Gemini run has **not** happened yet: there was no `.env` in the project folder when this was built. Only the no-key failure path and the fake provider were run end to end.

**Things I must be able to explain without help**
- _Why `FOR UPDATE SKIP LOCKED` lets several workers share one queue safely._
- _Why an invalid model answer is retried in the same item (repair prompt), but a 503 is retried later with backoff._
- _Why cost rows are written in their own transaction._
