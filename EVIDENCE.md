# EVIDENCE

Brief §6: *"Done = every box below ticked, with one pasted proof per box."* Claims without evidence score as not done.
Each proof is **real pasted output** (a test name + output, a curl transcript, or a log line), dated with the commit it came from.

Legend: ☐ not started · ◐ partial (proof below covers part of it) · ☑ done

---

## AI processing

### ◐ AI-1 · Vision model produces structured output validated against a schema; invalid responses are never trusted
_Partial: the schema and its validation tests exist. The vision call that uses them lands in Phase 2._

```
$ docker compose exec -T api pytest tests/test_schemas.py -v
tests/test_schemas.py::test_brief_example_is_valid PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[missing required field] PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[confidence above 1] PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[confidence below 0] PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[confidence not a number] PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[category outside the enum] PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[no attributes] PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[too many attributes] PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[caption too short] PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[empty subject] PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[unexpected extra field] PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[prose instead of JSON] PASSED
tests/test_schemas.py::test_invalid_vision_output_is_rejected[empty response] PASSED
tests/test_schemas.py::test_whitespace_is_stripped PASSED
```

### ☐ AI-2 · Low-confidence classifications are flagged instead of accepted
_Proof to paste: `curl localhost:8000/images?status=flagged` showing ≥1 image with its flag reason._

### ☐ AI-3 · Images are processed through a batch background job with retries
_Proof to paste: `curl localhost:8000/jobs/{id}` with progress counts, plus `test_item_retried_then_succeeds` output._

### ☐ AI-4 · Vision and embedding costs are tracked per call
_Proof to paste: `curl "localhost:8000/costs?job_id=…"` with per-call rows and totals._

## Matching system

### ☐ M-1 · Image and post embeddings are stored; posts return ranked image suggestions
_Proof to paste: `curl localhost:8000/posts/the-behavior-of-red-foxes/images`._

### ☐ M-2 · Semantic matching works for equivalent concepts: "red fox" matches "Vulpes vulpes"
_Proof to paste: the "Vulpes vulpes" post (which never uses the word "fox") ranking a fox image first._

## Safety layer

### ☐ S-1 · The mismatch guard rejects incorrect recommendations: the wolf-on-a-fox-post scenario provably fails
_Proof to paste: `POST /posts/the-behavior-of-red-foxes/candidates {"image_id": <wolf>}` → `REJECTED`, plus the guard unit test._

### ☐ S-2 · Rejections include a human-readable explanation
_Proof to paste: the same transcript showing `"Animal category mismatch: expected fox, detected wolf"`._

### ☐ S-3 · When no image clears the bar, the system answers "no confident match" with reasons
_Proof to paste: `curl localhost:8000/posts/emperor-penguins-huddle/images`._

## Backend

### ◐ B-1 · Database models for images, tags, embeddings, posts, suggestions, approvals/rejections, with the required indexes
_Partial: all tables and indexes exist as Alembic migration `0001`. The repositories that use them land with each phase._

```
$ docker compose exec -T api pytest tests/test_persistence.py -v
tests/test_persistence.py::test_models_and_migrations_are_in_sync PASSED
tests/test_persistence.py::test_required_indexes_exist PASSED
tests/test_persistence.py::test_pgvector_extension_installed PASSED
tests/test_persistence.py::test_same_image_twice_in_one_tenant_is_rejected_but_allowed_across_tenants PASSED
tests/test_persistence.py::test_invalid_status_is_rejected_by_check_constraint PASSED
tests/test_persistence.py::test_seed_is_idempotent PASSED
tests/test_persistence.py::test_migrations_downgrade_and_upgrade_cleanly PASSED
```

### ☐ B-2 · API endpoints validated; the review workflow (approve / reject / inspect why) exists
_Partial groundwork: the boundary tests in `tests/test_api_boundary.py` prove the 4xx contract (400/404/405/413/422, generic 500). Proof to paste: approve, reject and explain transcripts._

## Quality & documentation

### ☐ Q-1 · A small labeled evaluation dataset measures top-1 precision, and the number is in the README
_Proof to paste: `python -m app.cli eval` output, plus the README excerpt with the same number._

### ☐ Q-2 · README with architecture explanation and diagram; the required files from §11 present
_Proof to paste: `ls` of the repo root showing README.md, capstone.yaml, EVIDENCE.md, BUILDLOG.md, .env.example._
