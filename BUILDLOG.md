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

---

## 2026-11-27 · Phase 3: embeddings, subject resolution, ranking, the guard (AI-assisted)

**Where AI helped**
- Claude Code wrote the matching engine: embedding jobs for images and posts, `subject_key` resolution, pgvector ranking, the pure guard (`app/services/guard.py`), the posts/candidates endpoints, and their tests.
- Claude wrote the 26 posts in `data/posts.yaml` from what the photos show (not from the vision captions), including the "Vulpes vulpes" post that never uses the word fox.

**Where AI was wrong / what changed** (every number below was measured on the real Gemini embeddings)
- **Post subjects from the whole post vector did not work.** The first design matched a post's title+body vector against the short taxonomy descriptions. The red-fox post led fox over wolf by only 0.014, the Vulpes post by 0.010, the arctic-fox post resolved to *wolf*, and the coral-reef post to *deer*. Titles alone were better but still sent "Keeping indoor cats busy" to *dog*.
- **Fix, part 1: a topic phrase for posts.** A structured, validated text call now reads each post and returns `{subject, category, confidence}` (e.g. "Vulpes vulpes" → "red fox"). It is cost-tracked as `operation=text`, retried with a repair prompt like vision output, and resolved exactly like an image's subject. Short phrases resolve cleanly: real classes lead by 0.049–0.12.
- **Fix, part 2: "none of the above".** Nearest-label matching always picks *some* label: "domestic cat" → dog with a 0.074 lead, "coral reef" → deer. Adding generic out-of-scope anchors (other canid, cat, bird, sea life, other mammal, not an animal) fixed it. The margin rule now compares in-scope labels only; otherwise "Siberian husky" lost to the "not an animal" anchor by 0.018, which is noise.
- **Coyote:** "coyote" sits 0.004 between fox and wolf (and now resolves to the other-canid anchor), so the coyote photo is flagged instead of guessed. The independent signal the plan wanted: it does not depend on the model's confidence.
- The offline fake embedder (bag of words) hashed "red" into the same bucket as a word in the *bird* description, so "red fox" looked like a bird and 8 tests failed. Unit tests now use their own tiny taxonomy and a wider hash space, so they don't depend on the wording of `config/taxonomy.yaml`.
- The API ranked with the *configured* embedding model, so posts embedded by another model (a recording, or the fake provider in tests) got a 409. Ranking now prefers the configured model and otherwise uses the model the post was actually embedded with.
- An unquoted colon in a post title broke the YAML and the seed silently loaded no posts; quoted, and the eval checks every labeled slug exists.
- `EMBED_RPM=60` triggered four real 429s from Gemini during a burst; the worker retried all four successfully. The default is now 30.
- **Correction to the earlier entry about `httpx2`:** the blocked change tried to add `httpx2` *directly*. It is installed anyway, legitimately: Google's `google-genai` 3.6.1 declares `httpx2>=2.0.0` (published at github.com/pydantic/httpx2). `uv.lock` has had it since the Phase 2 commit. Nothing was added by hand.

**Things I must be able to explain without help**
- _Why the guard runs every check instead of stopping at the first failure._
- _Why a nearest-label classifier needs out-of-scope anchors, and why the margin ignores them._
- _Why the post's subject comes from a short phrase and not from the post's own vector._

---

## 2026-11-27 · Phase 4: review API, eval set, threshold sweep (AI-assisted)

**Where AI helped**
- Claude Code wrote the review API (queue, explain, approve/reject with Idempotency-Key and 409 on contradiction, an HTML review table), the eval (`app/services/evaluation.py`, `cli eval --sweep`), the snapshot recorder, and the README/EVIDENCE.

**Where AI was wrong / what changed**
- **The first eval was too easy to tune anything.** With 15 specific posts and 4 off-topic negatives, every threshold from 0.30 to 0.84 scored the same: the subject check refused all the negatives on its own. Added 4 hard negatives (a known class, a species with no photo: polar bear, moose, dalmatian, fennec fox) so similarity has a real job.
- **Then the sweep picked 0.84, which would have broken probe P2.** General posts score lower than specific ones: "The behavior of red foxes" peaks at 0.828 and the Vulpes post at 0.788. Fennec fox → red fox scores 0.798, as high as Vulpes → red fox: similarity cannot see species inside a class. The eval now has general (class-level) posts, and the rule is: zero cross-species errors, best top-1, general posts still answered, most refusals → **0.78**.
- **Metric bug:** the first version counted a brown bear on the polar-bear post as *cross-species*. It is a same-class false accept; now counted separately. Cross-species false accepts are 0 at every threshold with the full guard, up to 5 with similarity alone.
- **Reproducibility (P5):** Gemini isn't deterministic, so the eval runs on recorded AI outputs (`eval/snapshot.json`) replayed into an isolated `eval` tenant with no AI calls. A test fails if the README, `eval/results.json` and a fresh eval disagree.
- **Known limitations left honest, not tuned away:** 3 of 4 hard negatives still get a same-class photo; the husky the vision model calls "gray wolf" stays wrong.

**Things I must be able to explain without help**
- _What top-1 precision measures here, and why 100% on 15 posts is not 100% in general._
- _Why the sweep's first rule is zero cross-species false accepts, and what the similarity-only columns show._
- _Why the eval runs on a recording instead of re-calling Gemini._

---

## 2026-11-27 · Independent review of the Phase 3-4 code (AI-assisted)

A separate Claude agent reviewed the uncommitted Phase 3-4 diff for correctness bugs only, without being told my conclusions, and reproduced each finding with a throwaway test. It found 3 confirmed and 5 plausible bugs. All 8 were real; all are fixed, and the confirmed ones have regression tests (`tests/test_reliability.py`).

**Confirmed (and what changed)**
- **A retried `force` post job paid for the topic call twice.** On retry, `force` re-ran the text call even though this same job had already paid for and committed it. The topic now records the job that wrote it, and the embedding step relies on the text hash instead of `force`.
- **Re-tagging could leave a stale subject_key forever.** If an image went from "gray wolf" to "red fox" and the next step then failed (e.g. the budget guard), `subject_key` stayed `wolf` and no later job picked it up, so the image could be accepted for wolf posts. New vision output (or a new post topic) now clears the subject until it is re-resolved; a NULL resolver version makes the next job finish it.
- **A worker that lost its claim could still finish or re-queue the item.** Completion only checked `status='running'`, which the new owner's claim also satisfies. The attempt number is now a claim token in every update, and mid-item commits row-lock and re-check ownership first.

**Plausible (fixed anyway)**
- The worker read the taxonomy/thresholds once at startup while the API re-reads them per request: an edit without a restart would re-enqueue everything forever. The worker now re-reads them per item.
- Editing a label's text without bumping the taxonomy version didn't re-resolve stored keys: the resolver version now includes a hash of every label text.
- Changing `EMBED_DIMENSIONS` would mix vector sizes (a 500 in ranking): a vector of the wrong size is now stale, and ranking only compares same-size vectors.
- A permanent embedding error after a successful vision call marked the image `failed`, so the next job paid for vision again: the valid result is now kept (flagged) and only the missing step is redone.
- A post edited while a job was reading it could keep the old topic: the enqueue now compares the topic's text hash with a SQL `sha256` of the current text.

After the fixes, re-resolving all 78 images and posts under the new resolver version made **zero** AI calls (241 before and after), and the eval result is unchanged.

---

## 2026-11-27 · Clean-machine test

A fresh clone of the public repo, run as a separate Docker project with an empty database and only the README's commands: it booted healthy, 119 tests passed, and the Gemini batch jobs processed all 52 images and 26 posts with no failures (157 calls, $0.040 list price). Probes P2–P6 passed on the fresh outputs, and the eval gave 100.0% (15/15) both on the recording and on the live data. Gemini's non-determinism was visible (the husky came back as "wolfdog" this time and was flagged), which is exactly why the README number comes from the recording. Full transcript: EVIDENCE.md, "Clean-machine run".
