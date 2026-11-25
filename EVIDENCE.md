# EVIDENCE

Brief §6: *"Done = every box below ticked, with one pasted proof per box."* Claims without evidence score as not done.
Each proof is **real pasted output** (a test name + output, a curl transcript, or a log line), dated with the commit it came from.

Legend: ☐ not started · ◐ partial (proof below covers part of it) · ☑ done

---

## AI processing

### ◐ AI-1 · Vision model produces structured output validated against a schema; invalid responses are never trusted
_Mechanism proven by tests (scripted fake provider). Still to paste: a real Gemini run on the corpus (`GET /images/{id}`)._

```
$ docker compose exec -T api pytest tests/test_schemas.py tests/test_worker_pipeline.py -v
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
tests/test_schemas.py::test_json_schema_sent_to_the_model_matches_the_pydantic_contract PASSED
tests/test_worker_pipeline.py::test_valid_output_tags_image_and_records_one_cost_row PASSED
tests/test_worker_pipeline.py::test_invalid_output_is_repaired_and_every_attempt_is_logged PASSED
tests/test_worker_pipeline.py::test_output_still_invalid_after_repairs_is_never_trusted PASSED
```

### ◐ AI-2 · Low-confidence classifications are flagged instead of accepted
_Mechanism proven. Still to paste: `curl localhost:8000/images?status=flagged` after the real corpus run (probe P1)._

```
tests/test_worker_pipeline.py::test_low_confidence_is_flagged_not_accepted PASSED
tests/test_api_phase2.py::test_images_table_filters_flagged PASSED
```

### ◐ AI-3 · Images are processed through a batch background job with retries
_Mechanism proven. Still to paste: `curl localhost:8000/jobs/{id}` from the real corpus run._

```
tests/test_worker_pipeline.py::test_transient_error_is_retried_with_backoff_then_succeeds PASSED
tests/test_worker_pipeline.py::test_rate_limit_is_logged_and_retried PASSED
tests/test_worker_pipeline.py::test_gives_up_after_max_attempts_and_raises_failure_alert PASSED
tests/test_worker_pipeline.py::test_permanent_provider_error_is_not_retried PASSED
tests/test_worker_pipeline.py::test_missing_api_key_fails_items_with_a_clear_alert PASSED
tests/test_worker_pipeline.py::test_mixed_batch_finishes_partial PASSED
tests/test_worker_pipeline.py::test_rerun_without_force_skips_done_images_and_force_reprocesses PASSED
tests/test_worker_pipeline.py::test_item_handed_to_two_workers_is_paid_and_counted_once PASSED
tests/test_worker_pipeline.py::test_stale_running_items_are_requeued PASSED
tests/test_api_phase2.py::test_post_jobs_enqueues_and_returns_202_without_calling_ai PASSED
tests/test_api_phase2.py::test_same_idempotency_key_returns_the_same_job_once PASSED
tests/test_api_phase2.py::test_job_progress_after_worker_runs PASSED
tests/test_api_phase2.py::test_alerts_endpoint_lists_job_failures PASSED
```

### ◐ AI-4 · Vision and embedding costs are tracked per call
_Vision cost rows are proven, including the budget guard. Embedding calls go through the same `TrackedEmbedder` from Phase 3. Still to paste: `curl "localhost:8000/costs?job_id=…"` from the real corpus run (probe P6)._

```
tests/test_worker_pipeline.py::test_budget_guard_stops_the_job_before_any_paid_call PASSED
tests/test_api_phase2.py::test_cost_log_has_an_entry_for_every_call PASSED
tests/test_api_phase2.py::test_image_detail_shows_tags_and_its_ai_calls PASSED
tests/test_providers.py::test_cost_uses_list_price_per_million_tokens PASSED
```

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
_Groundwork: `tests/test_api_boundary.py` and `tests/test_api_phase2.py` prove the 4xx contract (400/404/405/409/413/422, generic 500) and tenant isolation. Still to paste: approve, reject and explain transcripts (Phase 4)._

## Quality & documentation

### ☐ Q-1 · A small labeled evaluation dataset measures top-1 precision, and the number is in the README
_Proof to paste: `python -m app.cli eval` output, plus the README excerpt with the same number._

### ☐ Q-2 · README with architecture explanation and diagram; the required files from §11 present
_Proof to paste: `ls` of the repo root showing README.md, capstone.yaml, EVIDENCE.md, BUILDLOG.md, .env.example._
