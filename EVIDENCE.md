# EVIDENCE

Brief §6: *"Done = every box below ticked, with one pasted proof per box."* Claims without evidence score as not done.
Each proof is **real pasted output** (a test name + output, a curl transcript, or a log line), dated with the commit it came from.

Legend: ☐ not started · ◐ partial (proof below covers part of it) · ☑ done

---

## AI processing

### ☑ AI-1 · Vision model produces structured output validated against a schema; invalid responses are never trusted
_Real run, 2026-11-27: 52 corpus photos, `gemini-3.5-flash-lite`, prompt `v2`. Gemini is asked for JSON matching the schema, and every answer is validated again with Pydantic (`ImageMetadata`, `extra="forbid"`) before anything is saved. All 52 real answers passed validation, so the invalid-output path is proven by the tests below (bad JSON, missing field, `confidence` 1.7, extra field, …: rejected, repaired, and never saved)._

```
$ curl -s localhost:8000/images/2663a755-2f4c-44d8-abf5-b807dd1d87e1 -H 'X-Tenant-Id: demo' | jq '{file_path, status, subject, category, caption, confidence, flag_reasons, prompt_version, tags: [.tags[] | "\(.kind): \(.tag)"]}'
{
  "file_path": "data/images/img_8808.jpg",
  "status": "tagged",
  "subject": "red fox",
  "category": "animal",
  "caption": "A close-up portrait of a red fox looking directly at the camera with an alert expression.",
  "confidence": 0.95,
  "flag_reasons": [],
  "prompt_version": "v2",
  "tags": [
    "attribute: forward-facing",
    "attribute: orange fur",
    "attribute: outdoor",
    "attribute: pointed ears",
    "attribute: portrait",
    "attribute: white chest",
    "category: animal",
    "subject: red fox"
  ]
}
```

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

### ☑ AI-2 · Low-confidence classifications are flagged instead of accepted
_Real run (probe P1): 7 of 52 images were flagged rather than accepted. Each one is genuinely hard: an extreme crop (`img_993d`), heavy motion blur (`img_c31a`), a silhouette at sunset (`img_1460`), a fox in near-darkness that the model called "coyote" (`img_6695`), a stag seen only from behind (`img_312f`), a black-and-white nose close-up (`img_f4b5`), and a fox at night (`img_5766`). The model's own guess is kept but marked as unconfirmed, never silently accepted._

```
$ curl -s 'localhost:8000/images?status=flagged' -H 'X-Tenant-Id: demo' | jq -c '{total}, (.items[] | {file_path, subject, confidence, flag_reasons})'
{"total":7}
{"file_path":"data/images/img_312f.jpg","subject":"fallow deer","confidence":0.6,"flag_reasons":["Low confidence 0.60 < 0.70: identification is uncertain, needs human review"]}
{"file_path":"data/images/img_c31a.jpg","subject":"dog","confidence":0.5,"flag_reasons":["Low confidence 0.50 < 0.70: identification is uncertain, needs human review"]}
{"file_path":"data/images/img_f4b5.jpg","subject":"Shiba Inu","confidence":0.55,"flag_reasons":["Low confidence 0.55 < 0.70: identification is uncertain, needs human review"]}
{"file_path":"data/images/img_993d.jpg","subject":"deer","confidence":0.55,"flag_reasons":["Low confidence 0.55 < 0.70: identification is uncertain, needs human review"]}
{"file_path":"data/images/img_1460.jpg","subject":"gray wolf","confidence":0.6,"flag_reasons":["Low confidence 0.60 < 0.70: identification is uncertain, needs human review"]}
{"file_path":"data/images/img_6695.jpg","subject":"coyote","confidence":0.6,"flag_reasons":["Low confidence 0.60 < 0.70: identification is uncertain, needs human review"]}
{"file_path":"data/images/img_5766.jpg","subject":"red fox","confidence":0.6,"flag_reasons":["Low confidence 0.60 < 0.70: identification is uncertain, needs human review"]}
```

```
tests/test_worker_pipeline.py::test_low_confidence_is_flagged_not_accepted PASSED
tests/test_api_phase2.py::test_images_table_filters_flagged PASSED
```

### ☑ AI-3 · Images are processed through a batch background job with retries
_Real runs, 2026-11-27. The CLI enqueues a `process_images` job and returns; the separate `worker` container does the AI calls and the CLI only polls progress (the `running` progress lines are filtered out with `grep -v` below)._

```
$ docker compose exec -T api python -m app.cli process --wait --timeout 1200 2>&1 | grep -v "^  running"
Enqueued job 6f57804f-3ae9-45b7-b8f7-44ada93eba9f: 52 image(s) to process (provider=gemini, model=gemini-3.5-flash-lite)
  queued    0/52 processed · 0 tagged · 0 flagged · 0 failed
  succeeded 52/52 processed · 45 tagged · 7 flagged · 0 failed
```

```
$ curl -s localhost:8000/jobs/6f57804f-3ae9-45b7-b8f7-44ada93eba9f -H 'X-Tenant-Id: demo' | jq .
{
  "id": "6f57804f-3ae9-45b7-b8f7-44ada93eba9f",
  "type": "process_images",
  "status": "succeeded",
  "force": false,
  "total": 52,
  "processed": 52,
  "succeeded": 45,
  "flagged": 7,
  "failed": 0,
  "error": null,
  "created_at": "2026-11-27T02:31:52.484679Z",
  "started_at": "2026-11-27T02:31:53.235200Z",
  "finished_at": "2026-11-27T02:38:10.203977Z",
  "progress": 1.0
}
```

_Retries for real: during the first (v1-prompt) run, Gemini answered **503 "high demand"** twice. Each failed attempt was logged, the item was retried after a backoff, and both succeeded on attempt 2:_

```
$ curl -s 'localhost:8000/costs?job_id=2528f43e-92d2-4620-89c5-633a3a107077&limit=100' -H 'X-Tenant-Id: demo' | jq -c '.calls.items[] | select(.status != "ok" or .attempt > 1) | {entity_id, attempt, status, latency_ms, error}'
{"entity_id":"3343637f-c73c-4cd4-b142-ac9277f91492","attempt":1,"status":"error","latency_ms":4226,"error":"Gemini API 503: This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later."}
{"entity_id":"afaed574-d4b0-42a8-a004-ef29f8bfbbd1","attempt":1,"status":"error","latency_ms":1886,"error":"Gemini API 503: This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later."}
{"entity_id":"3343637f-c73c-4cd4-b142-ac9277f91492","attempt":2,"status":"ok","latency_ms":5258,"error":null}
{"entity_id":"afaed574-d4b0-42a8-a004-ef29f8bfbbd1","attempt":2,"status":"ok","latency_ms":5182,"error":null}
```

```
$ docker compose exec -T db psql -U app -d app -c "select ji.attempts, ji.status, split_part(i.file_path,'/',3) as file from job_items ji join images i on i.id = ji.item_id where ji.job_id = '2528f43e-92d2-4620-89c5-633a3a107077' and ji.attempts > 1;"
 attempts |  status   |     file     
----------+-----------+--------------
        2 | succeeded | img_e083.jpg
        2 | succeeded | img_a69a.jpg
(2 rows)
```

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
_Vision: real, one cost row per call. 52 images → 52 rows for this job, with tokens, latency, list-price cost and tier. `spent_usd` is the tenant total across both runs, and the budget guard checks it before every call. Still to paste: embedding cost rows, which arrive with Phase 3 through the same `TrackedEmbedder`._

```
$ curl -s 'localhost:8000/costs?job_id=6f57804f-3ae9-45b7-b8f7-44ada93eba9f&limit=100' -H 'X-Tenant-Id: demo' | jq '{budget_usd, spent_usd, remaining_usd, totals, calls_listed: .calls.total, first_call: .calls.items[0] | {entity_type, entity_id, model, attempt, status, input_tokens, output_tokens, cost_usd, tier, latency_ms}}'
{
  "budget_usd": 1.0,
  "spent_usd": 0.0698455,
  "remaining_usd": 0.9301545,
  "totals": [
    {
      "provider": "gemini",
      "model": "gemini-3.5-flash-lite",
      "operation": "vision",
      "status": "ok",
      "calls": 52,
      "input_tokens": 77386,
      "output_tokens": 4996,
      "cost_usd": 0.0357058
    }
  ],
  "calls_listed": 52,
  "first_call": {
    "entity_type": "image",
    "entity_id": "06705df7-6f78-4298-b8dc-7c937f8ed895",
    "model": "gemini-3.5-flash-lite",
    "attempt": 1,
    "status": "ok",
    "input_tokens": 1500,
    "output_tokens": 104,
    "cost_usd": 0.00071,
    "tier": "free",
    "latency_ms": 1862
  }
}
```

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
