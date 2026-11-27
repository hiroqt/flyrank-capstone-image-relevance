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

---

## 2026-11-27 · Phase 1 corpus: 52 images from Pexels (AI-assisted)

**Where AI helped**
- Claude Code drove a browser (Orca's built-in browser) through 33 Pexels searches, read each result's photo ID, photographer and description, and shortlisted 67 candidates.
- Claude downloaded small previews, put them on contact sheets, and checked every candidate by eye before choosing 52. It then resized them to 768 px, gave them opaque names and wrote `manifest.csv`.
- Claude read the Pexels License page before downloading anything.

**Where AI was wrong / what changed**
- Search terms are not labels. "wolf" returned huskies and wolfdogs, "trail camera" returned hikers holding cameras, "shiba inu" returned a figurine, and "fox night" returned a cosplay shot. All were dropped. Each `true_class` comes from looking at the picture.
- The first manifest used Pexels' long photo URLs, which contain words like "young-black-bear-climbing…". They were switched to the short `pexels.com/photo/<id>/` form, which redirects to the same page, so no class words reach the database.

**Things I must be able to explain without help**
- _Why the corpus has look-alikes (Shiba vs fox, husky vs wolf) and a coyote, and what the system should do with each._
- _Why `true_class` must never be read by the matching code._

---

## 2026-11-27 · Phase 2 gate: real Gemini run on the corpus (AI-assisted)

**What happened**
- One-image smoke test first: `img_8808` came back as "red fox", confidence 0.95, schema-valid, in 5.7 s (1,357 tokens in, 111 out).
- **Run 1 (prompt v1):** 52/52 tagged, **0 flagged**, 0 failed. Gemini answered 503 "high demand" twice; the worker logged both attempts, backed off, and both items succeeded on attempt 2.
- **Run 2 (prompt v2):** 52/52 processed, **45 tagged, 7 flagged**, 0 failed. Cost for the run: 52 calls, 77,386 input and 4,996 output tokens, $0.036 list price (billed $0, free tier).

**Where AI was wrong / what changed**
- **The model's confidence was not calibrated.** v1's rubric said "blurry, distant, dark → 0.40–0.69", but every image scored ≥ 0.80, including a motion-blurred silhouette (0.80) and an extreme crop showing only an eye (0.85). That would fail probe P1 ("≥1 flagged, not guessed").
- **What I did not do:** lower the threshold until something got flagged. That would be fitting the threshold to these 52 images.
- **What I did:** prompt v2 caps confidence by conditions that are visible in the photo: silhouette, blur, tiny subject, darkness or partial crop → at most 0.60; plausible look-alike → at most 0.85. Bumping `PROMPT_VERSION` re-queued every image automatically, because idempotency is keyed on (model, prompt version).
- **Result:** all 7 flagged images are genuinely hard (crop, blur, silhouette, darkness, a stag from behind, a nose close-up, a fox at night). Clear photos still score 0.85–0.95.
- **Still wrong, still confident.** `img_b9d0` is a husky in a forest; Gemini says "gray wolf" at 0.80 in v2 (0.95 in v1), so it is accepted. The Phase 3 guard compares post and image subjects, so it cannot catch a wrong vision answer. This goes into the eval (Phase 4) and README limitations as a known failure.
- **Arguable.** `img_6695` (labelled fox from the photographer's description) is called "coyote". It is dark and genuinely ambiguous, and v2 flags it, so a human decides.
- **Not caught by confidence.** The tiny fox in a car park (`img_b20d`, 0.80) and the coyote (`img_ee1e`, "coyote" 0.85) are accepted. The coyote is correct, and it is not in the taxonomy; Phase 3's `subject_key` resolution is designed to mark it `unknown` and flag it.

**Things I must be able to explain without help**
- _Why a model's self-reported confidence can't be trusted as a probability, and why the caps are tied to visible conditions._
- _Why changing `PROMPT_VERSION` re-processes images without `--force`._
