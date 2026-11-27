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

_Phase 3 added a second, independent reason to flag: the subject must resolve to a known class. The coyote (`img_ee1e`, confidence 0.85, so it passed the confidence check) is now flagged because "coyote" is closest to the out-of-scope "other canid" anchor. Current state, 8 flagged:_

```
$ curl -s 'localhost:8000/images?status=flagged' | jq -c '{total}, (.items[] | {file_path, subject, subject_key, confidence, flag_reasons})'
{"total":8}
{"file_path":"data/images/img_312f.jpg","subject":"fallow deer","subject_key":"deer","confidence":0.6,"flag_reasons":["Low confidence 0.60 < 0.70: identification is uncertain, needs human review"]}
{"file_path":"data/images/img_c31a.jpg","subject":"dog","subject_key":"dog","confidence":0.5,"flag_reasons":["Low confidence 0.50 < 0.70: identification is uncertain, needs human review"]}
{"file_path":"data/images/img_ee1e.jpg","subject":"coyote","subject_key":"unknown","confidence":0.85,"flag_reasons":["Subject 'coyote' does not resolve to a known category (closest category is 'other_canid' (0.89), which is outside the taxonomy)"]}
{"file_path":"data/images/img_f4b5.jpg","subject":"Shiba Inu","subject_key":"dog","confidence":0.55,"flag_reasons":["Low confidence 0.55 < 0.70: identification is uncertain, needs human review"]}
{"file_path":"data/images/img_993d.jpg","subject":"deer","subject_key":"deer","confidence":0.55,"flag_reasons":["Low confidence 0.55 < 0.70: identification is uncertain, needs human review"]}
{"file_path":"data/images/img_1460.jpg","subject":"gray wolf","subject_key":"wolf","confidence":0.6,"flag_reasons":["Low confidence 0.60 < 0.70: identification is uncertain, needs human review"]}
{"file_path":"data/images/img_6695.jpg","subject":"coyote","subject_key":"unknown","confidence":0.6,"flag_reasons":["Low confidence 0.60 < 0.70: identification is uncertain, needs human review","Subject 'coyote' does not resolve to a known category (closest category is 'other_canid' (0.89), which is outside the taxonomy)"]}
{"file_path":"data/images/img_5766.jpg","subject":"red fox","subject_key":"fox","confidence":0.6,"flag_reasons":["Low confidence 0.60 < 0.70: identification is uncertain, needs human review"]}
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

_Reliability regression tests, added after an independent review of the Phase 3-4 code (BUILDLOG): a retried item never pays twice, and a worker that lost its claim can't overwrite, finish or re-queue the new owner's work:_

```
$ docker compose exec -T api pytest tests/test_reliability.py -v
tests/test_reliability.py::test_a_retried_force_post_job_pays_for_the_topic_once PASSED [ 16%]
tests/test_reliability.py::test_new_vision_output_never_leaves_a_stale_subject_and_a_later_job_only_redoes_the_missing_step PASSED [ 33%]
tests/test_reliability.py::test_a_superseded_worker_cannot_finish_fail_or_requeue_the_item PASSED [ 50%]
tests/test_reliability.py::test_a_superseded_worker_cannot_overwrite_the_new_owners_vision_result PASSED [ 66%]
tests/test_reliability.py::test_a_post_whose_text_changed_after_its_topic_was_read_is_picked_up_again PASSED [ 83%]
tests/test_reliability.py::test_resolver_version_changes_when_a_label_text_changes_without_a_version_bump PASSED [100%]
============================== 6 passed in 0.45s ===============================
```

### ☑ AI-4 · Vision and embedding costs are tracked per call
_Every attempt (vision, the post-topic text call, embeddings; ok, invalid, error, rate-limited) is one `ai_calls` row with tokens, latency, list-price cost, tier and attribution (tenant, job, job item, entity). The budget guard checks `spent_usd` before every call. Real totals for the whole build: 241 calls, $0.074 list price, billed $0 (free tier)._

Vision, per call (the v2 batch job, 52 images → 52 rows):

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

Embeddings, per call, including the four real 429s that were retried (`tokens_estimated: true`: the Gemini embeddings endpoint doesn't report usage, so tokens are estimated):

```
$ curl -s 'localhost:8000/costs?operation=embed&limit=100' | jq '{totals: [.totals[] | select(.operation == "embed")], one_post_embedding: ([.calls.items[] | select(.entity_type == "post")][0] | {entity_type, entity_id, job_id, model, status, input_tokens, tokens_estimated, cost_usd, tier, latency_ms})}'
{
  "totals": [
    {
      "provider": "gemini",
      "model": "gemini-embedding-001",
      "operation": "embed",
      "status": "ok",
      "calls": 105,
      "input_tokens": 5626,
      "output_tokens": 0,
      "cost_usd": 0.0008439
    },
    {
      "provider": "gemini",
      "model": "gemini-embedding-001",
      "operation": "embed",
      "status": "rate_limited",
      "calls": 4,
      "input_tokens": 0,
      "output_tokens": 0,
      "cost_usd": 0.0
    }
  ],
  "one_post_embedding": {
    "entity_type": "post",
    "entity_id": "b1167195-f538-4159-b26b-3c5cee3894c5",
    "job_id": "9059b9fc-bf6e-4322-a99d-2a89ea38f764",
    "model": "gemini-embedding-001",
    "status": "ok",
    "input_tokens": 85,
    "tokens_estimated": true,
    "cost_usd": 0.00001275,
    "tier": "free",
    "latency_ms": 482
  }
}
```

The post-topic text call (Phase 3), tracked the same way:

```
$ curl -s 'localhost:8000/costs?operation=text&limit=1' | jq '{totals, one_call: .calls.items[0] | {entity_type, entity_id, job_id, model, operation, status, input_tokens, output_tokens, cost_usd, tier}}'
{
  "totals": [
    {
      "provider": "gemini",
      "model": "gemini-3.5-flash-lite",
      "operation": "text",
      "status": "ok",
      "calls": 26,
      "input_tokens": 5716,
      "output_tokens": 749,
      "cost_usd": 0.0035873
    }
  ],
  "one_call": {
    "entity_type": "post",
    "entity_id": "b1167195-f538-4159-b26b-3c5cee3894c5",
    "job_id": "223269d0-8cb8-430f-bb54-7a17685c4d38",
    "model": "gemini-3.5-flash-lite",
    "operation": "text",
    "status": "ok",
    "input_tokens": 209,
    "output_tokens": 30,
    "cost_usd": 0.0001377,
    "tier": "free"
  }
}
```

```
$ curl -s 'localhost:8000/costs?limit=1' | jq '{budget_usd, spent_usd, remaining_usd, calls_total: .calls.total}'
{
  "budget_usd": 1.0,
  "spent_usd": 0.0742767,
  "remaining_usd": 0.9257233,
  "calls_total": 241
}
```

## Matching system

### ☑ M-1 · Image and post embeddings are stored; posts return ranked image suggestions
_Real run (Gemini `gemini-embedding-001`, 768 dimensions), computed by the background worker only. Images, posts, taxonomy labels and subject phrases are stored as pgvector rows:_

```
$ docker compose exec -T db psql -U app -d app -c "select owner_type, model, count(*), min(dim) as dim from embeddings where tenant_id = 'demo' group by owner_type, model order by owner_type;"
 owner_type |        model         | count | dim 
------------+----------------------+-------+-----
 image      | gemini-embedding-001 |    52 | 768
 label      | gemini-embedding-001 |    11 | 768
 post       | gemini-embedding-001 |    26 | 768
 subject    | gemini-embedding-001 |    78 | 768
(4 rows)
```

_Ranked, guarded suggestions for a post (probe P2). A fox is rank 1; the top four are accepted foxes; the flagged fox at rank 5 is refused. `by_subject` shows the wolf and dog rank clearly lower: best wolf at rank 11 (0.785), best dog at rank 28 (0.747):_

```
$ curl -s localhost:8000/posts/the-behavior-of-red-foxes/images | jq '{status, post_subject, suggestion: (.suggestion | {rank, file_path, subject, similarity, verdict}), candidates: [.candidates[] | {rank, file_path, subject_key, similarity, verdict}], by_subject: [.by_subject[] | {subject_key, best_rank, best_similarity}]}'
{
  "status": "match",
  "post_subject": "fox",
  "suggestion": {
    "rank": 1,
    "file_path": "data/images/img_bf3d.jpg",
    "subject": "red fox",
    "similarity": 0.8281,
    "verdict": "ACCEPTED"
  },
  "candidates": [
    {
      "rank": 1,
      "file_path": "data/images/img_bf3d.jpg",
      "subject_key": "fox",
      "similarity": 0.8281,
      "verdict": "ACCEPTED"
    },
    {
      "rank": 2,
      "file_path": "data/images/img_e083.jpg",
      "subject_key": "fox",
      "similarity": 0.8183,
      "verdict": "ACCEPTED"
    },
    {
      "rank": 3,
      "file_path": "data/images/img_1869.jpg",
      "subject_key": "fox",
      "similarity": 0.8148,
      "verdict": "ACCEPTED"
    },
    {
      "rank": 4,
      "file_path": "data/images/img_8808.jpg",
      "subject_key": "fox",
      "similarity": 0.8138,
      "verdict": "ACCEPTED"
    },
    {
      "rank": 5,
      "file_path": "data/images/img_5766.jpg",
      "subject_key": "fox",
      "similarity": 0.8135,
      "verdict": "REJECTED"
    }
  ],
  "by_subject": [
    {
      "subject_key": "fox",
      "best_rank": 1,
      "best_similarity": 0.8281
    },
    {
      "subject_key": "wolf",
      "best_rank": 11,
      "best_similarity": 0.7847
    },
    {
      "subject_key": "unknown",
      "best_rank": 14,
      "best_similarity": 0.7666
    },
    {
      "subject_key": "deer",
      "best_rank": 15,
      "best_similarity": 0.7655
    },
    {
      "subject_key": "dog",
      "best_rank": 28,
      "best_similarity": 0.7473
    },
    {
      "subject_key": "bear",
      "best_rank": 34,
      "best_similarity": 0.734
    }
  ]
}
```

### ☑ M-2 · Semantic matching works for equivalent concepts: "red fox" matches "Vulpes vulpes"
_The Vulpes post never contains the word "fox":_

```
$ curl -s localhost:8000/posts/vulpes-vulpes-in-european-cities | jq -r '.title, .body' | grep -ci 'fox'
0
```

_Its topic is read as "red fox" and resolves to the `fox` class:_

```
$ curl -s localhost:8000/posts/vulpes-vulpes-in-european-cities | jq '{title, topic: .topic | {subject, category, confidence}, subject_key, subject_key_detail: .subject_key_detail | {best, best_score, runner_up, runner_up_score}}'
{
  "title": "Vulpes vulpes: an adaptable survivor in European cities",
  "topic": {
    "subject": "red fox",
    "category": "animal",
    "confidence": 0.99
  },
  "subject_key": "fox",
  "subject_key_detail": {
    "best": "fox",
    "best_score": 0.8579,
    "runner_up": "bear",
    "runner_up_score": 0.7757
  }
}
```

_Foxes rank 1, 2 and 3 by meaning alone, and the top two are the night and city-car-park foxes ("cities… at night"). The rank-1 fox is flagged (low confidence), so the guard suggests the rank-2 red fox:_

```
$ curl -s 'localhost:8000/posts/vulpes-vulpes-in-european-cities/images?limit=3' | jq '{status, post_subject, suggestion: (.suggestion | {rank, file_path, subject, similarity, verdict}), candidates: [.candidates[] | {rank, file_path, subject, similarity, verdict, reasons}]}'
{
  "status": "match",
  "post_subject": "fox",
  "suggestion": {
    "rank": 2,
    "file_path": "data/images/img_bf3d.jpg",
    "subject": "red fox",
    "similarity": 0.7885,
    "verdict": "ACCEPTED"
  },
  "candidates": [
    {
      "rank": 1,
      "file_path": "data/images/img_5766.jpg",
      "subject": "red fox",
      "similarity": 0.7939,
      "verdict": "REJECTED",
      "reasons": [
        "Image classification is not trusted, flagged for human review: Low confidence 0.60 < 0.70: identification is uncertain, needs human review"
      ]
    },
    {
      "rank": 2,
      "file_path": "data/images/img_bf3d.jpg",
      "subject": "red fox",
      "similarity": 0.7885,
      "verdict": "ACCEPTED",
      "reasons": []
    },
    {
      "rank": 3,
      "file_path": "data/images/img_b20d.jpg",
      "subject": "red fox",
      "similarity": 0.7823,
      "verdict": "ACCEPTED",
      "reasons": []
    }
  ]
}
```

## Safety layer

### ☑ S-1 · The mismatch guard rejects incorrect recommendations: the wolf-on-a-fox-post scenario provably fails
_Probe P3, with the hardest wolf: `img_351e` is the wolf closest to the fox post (0.785), **above** the 0.78 similarity threshold, and confidently tagged. Similarity alone would accept it; the subject check is the only failing check:_

```
$ curl -s -X POST localhost:8000/posts/the-behavior-of-red-foxes/candidates -H 'Content-Type: application/json' -d '{"image_id": "bbe27fc1-f79c-45ee-8970-c86614372201"}' | jq '{post_subject, file_path, subject, subject_key, rank, similarity, verdict, reasons, checks: [.checks[] | {rule, passed, threshold}], suggestion_id}'
{
  "post_subject": "fox",
  "file_path": "data/images/img_351e.jpg",
  "subject": "gray wolf",
  "subject_key": "wolf",
  "rank": 11,
  "similarity": 0.7847,
  "verdict": "REJECTED",
  "reasons": [
    "Animal category mismatch: expected fox, detected wolf"
  ],
  "checks": [
    {
      "rule": "image_confidence",
      "passed": true,
      "threshold": 0.7
    },
    {
      "rule": "post_subject_known",
      "passed": true,
      "threshold": "a known subject"
    },
    {
      "rule": "subject_match",
      "passed": false,
      "threshold": "same subject"
    },
    {
      "rule": "similarity",
      "passed": true,
      "threshold": 0.78
    }
  ],
  "suggestion_id": "c546f336-4b1b-4435-8a9c-79eb153dcbb4"
}
```

_The guard as a pure module, tested on hand-written facts (`tests/test_guard.py`):_

```
$ docker compose exec -T api pytest tests/test_guard.py -v 2>&1 | grep -E 'PASSED|FAILED|passed|failed'
tests/test_guard.py::test_the_brief_scenario_wolf_on_a_fox_post_is_rejected_with_the_exact_reason PASSED [  7%]
tests/test_guard.py::test_the_right_animal_with_enough_similarity_is_accepted PASSED [ 14%]
tests/test_guard.py::test_every_check_runs_so_the_explanation_lists_every_failure PASSED [ 21%]
tests/test_guard.py::test_each_check_records_what_it_saw_and_the_threshold PASSED [ 28%]
tests/test_guard.py::test_a_flagged_image_is_never_accepted_even_for_the_right_post PASSED [ 35%]
tests/test_guard.py::test_an_unknown_post_subject_is_refused_with_its_reason PASSED [ 42%]
tests/test_guard.py::test_an_image_whose_subject_is_not_a_known_category_is_refused PASSED [ 50%]
tests/test_guard.py::test_no_match_summary_gives_similarity_and_subject_reasons PASSED [ 57%]
tests/test_guard.py::test_resolve_picks_the_nearest_in_scope_label PASSED [ 64%]
tests/test_guard.py::test_resolve_is_unknown_when_torn_between_two_classes PASSED [ 71%]
tests/test_guard.py::test_resolve_is_unknown_when_the_nearest_label_is_out_of_scope PASSED [ 78%]
tests/test_guard.py::test_resolve_is_unknown_when_nothing_is_close PASSED [ 85%]
tests/test_guard.py::test_a_non_animal_subject_never_resolves_to_an_animal_class PASSED [ 92%]
tests/test_guard.py::test_taxonomy_rejects_a_key_that_is_both_in_and_out_of_scope PASSED [100%]
============================== 14 passed in 0.46s ==============================
```

### ☑ S-2 · Rejections include a human-readable explanation
_The same rejection, reading the stored decision back through the review API:_

```
$ curl -s localhost:8000/suggestions/c546f336-4b1b-4435-8a9c-79eb153dcbb4/explain | jq '{post_slug, file_path, image_subject, verdict, summary, reasons}'
{
  "post_slug": "the-behavior-of-red-foxes",
  "file_path": "data/images/img_351e.jpg",
  "image_subject": "gray wolf",
  "verdict": "REJECTED",
  "summary": "Refused: Animal category mismatch: expected fox, detected wolf",
  "reasons": [
    "Animal category mismatch: expected fox, detected wolf"
  ]
}
```

_Every check also records what it observed and its threshold, so a rejection lists everything that failed (`test_every_check_runs_so_the_explanation_lists_every_failure` above)._

### ☑ S-3 · When no image clears the bar, the system answers "no confident match" with reasons
_Probe P4: the post resolves to `bird` (outside the taxonomy), and no image is similar enough. Both reasons the brief names are given:_

```
$ curl -s localhost:8000/posts/emperor-penguins-huddle/images | jq '{status, post_subject, suggestion, reasons, best_candidate: .candidates[0] | {rank, file_path, subject, similarity, verdict, reasons}}'
{
  "status": "no_confident_match",
  "post_subject": "unknown",
  "suggestion": null,
  "reasons": [
    "Could not tell what the post is about (closest category is 'bird' (0.80), which is outside the taxonomy)",
    "Best similarity 0.747 is below the threshold 0.78"
  ],
  "best_candidate": {
    "rank": 1,
    "file_path": "data/images/img_351e.jpg",
    "subject": "gray wolf",
    "similarity": 0.7468,
    "verdict": "REJECTED",
    "reasons": [
      "Could not tell what the post is about (closest category is 'bird' (0.80), which is outside the taxonomy)",
      "Subject cannot be confirmed: the post's subject is unknown",
      "Semantic similarity 0.747 is below the threshold 0.78"
    ]
  }
}
```

## Backend

### ☑ B-1 · Database models for images, tags, embeddings, posts, suggestions, approvals/rejections, with the required indexes
_Schema as Alembic migrations (never `create_all`); every table has `tenant_id`; unique constraints enforce the idempotency rules (sha256 per tenant, one vector per owner + model, one suggestion per post + image + guard version, one review per suggestion):_

```
$ ls migrations/versions/
0001_initial_schema.py
0002_images_last_job_id.py
0003_subject_resolution.py
0004_post_topic.py
```

```
$ docker compose exec -T db psql -U app -d app -At -c "select tablename || ': ' || string_agg(indexname, ', ' order by indexname) from pg_indexes where schemaname = 'public' and tablename in ('images','image_tags','embeddings','posts','suggestions','reviews') group by tablename order by tablename;"
embeddings: ix_embeddings_tenant_id_owner_type_model, pk_embeddings, uq_embeddings_tenant_id_owner_type_owner_id_model
image_tags: ix_image_tags_tenant_id_tag, pk_image_tags
images: ix_images_tenant_id_status, ix_images_tenant_id_subject_key, pk_images, uq_images_tenant_id_sha256
posts: pk_posts, uq_posts_tenant_id_slug
reviews: ix_reviews_tenant_id_decision, pk_reviews, uq_reviews_suggestion_id
suggestions: ix_suggestions_tenant_id_post_id_rank, pk_suggestions, uq_suggestions_tenant_id_post_id_image_id_guard_version
```

```
$ docker compose exec -T api pytest tests/test_persistence.py -q 2>&1 | tail -1
7 passed in 0.36s
```

_(`test_models_and_migrations_are_in_sync`, `test_required_indexes_exist`, `test_migrations_downgrade_and_upgrade_cleanly` and others, listed under Phase 2 above.)_

### ☑ B-2 · API endpoints validated; the review workflow (approve / reject / inspect why) exists
_The review queue (accepted, not yet reviewed):_

```
$ curl -s 'localhost:8000/suggestions?post_id=d439ecb2-4422-430d-9b29-0b4b5b176d47&verdict=accepted&review=pending&limit=2' | jq '{total, items: [.items[] | {id, post_slug, file_path, rank, similarity, verdict, review}]}'
{
  "total": 4,
  "items": [
    {
      "id": "b9397398-838d-4e4c-8110-33a4e4bfa162",
      "post_slug": "the-behavior-of-red-foxes",
      "file_path": "data/images/img_bf3d.jpg",
      "rank": 1,
      "similarity": 0.8281,
      "verdict": "ACCEPTED",
      "review": null
    },
    {
      "id": "ed1eace6-6ab0-497f-abc4-4c0a1a11e6fb",
      "post_slug": "the-behavior-of-red-foxes",
      "file_path": "data/images/img_e083.jpg",
      "rank": 2,
      "similarity": 0.8183,
      "verdict": "ACCEPTED",
      "review": null
    }
  ]
}
```

_Approve (with an Idempotency-Key), replay the same request (stored response, not a second review), then a contradicting decision (409):_

```
$ curl -s -i -X POST localhost:8000/suggestions/b9397398-838d-4e4c-8110-33a4e4bfa162/approve -H 'Idempotency-Key: review-fox-1' -H 'Content-Type: application/json' -d '{"reviewer": "editor", "note": "perfect for the intro"}' | grep -iE '^HTTP|^idempotency|^\{' 
HTTP/1.1 200 OK
{"suggestion_id":"b9397398-838d-4e4c-8110-33a4e4bfa162","verdict":"ACCEPTED","review":{"decision":"approved","note":"perfect for the intro","reviewer":"editor","created_at":"2026-11-27T03:21:53.210742Z"},"overrides_guard":false}
```

```
$ curl -s -i -X POST localhost:8000/suggestions/b9397398-838d-4e4c-8110-33a4e4bfa162/approve -H 'Idempotency-Key: review-fox-1' -H 'Content-Type: application/json' -d '{"reviewer": "editor", "note": "perfect for the intro"}' | grep -iE '^HTTP|^idempotency|^\{' 
HTTP/1.1 200 OK
idempotency-replayed: true
{"review":{"note":"perfect for the intro","decision":"approved","reviewer":"editor","created_at":"2026-11-27T03:21:53.210742Z"},"verdict":"ACCEPTED","suggestion_id":"b9397398-838d-4e4c-8110-33a4e4bfa162","overrides_guard":false}
```

```
$ curl -s -i -X POST localhost:8000/suggestions/b9397398-838d-4e4c-8110-33a4e4bfa162/reject -H 'Content-Type: application/json' -d '{"reviewer": "someone-else"}' | grep -iE '^HTTP|^\{' 
HTTP/1.1 409 Conflict
{"error":{"code":"conflict","message":"Suggestion b9397398-838d-4e4c-8110-33a4e4bfa162 was already approved; it cannot also be rejected"}}
```

_Inspect why it was selected: every check, observed value vs threshold, and the human decision:_

```
$ curl -s localhost:8000/suggestions/b9397398-838d-4e4c-8110-33a4e4bfa162/explain | jq '{verdict, summary, checks: [.checks[] | {rule, passed, observed, threshold}], review}'
{
  "verdict": "ACCEPTED",
  "summary": "Selected: 'red fox' matches the post's subject 'fox' with similarity 0.828, and every guard check passed.",
  "checks": [
    {
      "rule": "image_confidence",
      "passed": true,
      "observed": {
        "status": "tagged",
        "confidence": 0.95
      },
      "threshold": 0.7
    },
    {
      "rule": "post_subject_known",
      "passed": true,
      "observed": "fox",
      "threshold": "a known subject"
    },
    {
      "rule": "subject_match",
      "passed": true,
      "observed": {
        "detected": "fox",
        "expected": "fox",
        "image_subject": "red fox"
      },
      "threshold": "same subject"
    },
    {
      "rule": "similarity",
      "passed": true,
      "observed": 0.8281,
      "threshold": 0.78
    }
  ],
  "review": {
    "decision": "approved",
    "note": "perfect for the intro",
    "reviewer": "editor",
    "created_at": "2026-11-27T03:21:53.210742Z"
  }
}
```

_The review table (HTML, no frontend build), filtered to approved:_

```
$ curl -s -i 'localhost:8000/admin/review?review=approved' | grep -E '^HTTP|<h1>|<td><code>the-behavior' | head -4
HTTP/1.1 200 OK
.ok{color:#0a6b2d;font-weight:600}.no{color:#a3121b;font-weight:600}code{font-size:12px}</style></head><body><h1>Review queue: demo</h1><p>1 suggestion(s). Inspect one with <code>GET /suggestions/{id}/explain</code>; decide with <code>POST /suggestions/{id}/approve</code> or <code>/reject</code>.</p><table><tr><th>Post</th><th>Image</th><th>Rank</th><th>Similarity</th><th>Guard</th><th>Reasons</th><th>Human review</th><th>Suggestion id</th></tr><tr><td><code>the-behavior-of-red-foxes</code><br>expects fox</td><td><code>img_bf3d.jpg</code><br>red fox</td><td>1</td><td>0.828</td><td><span class="ok">ACCEPTED</span></td><td>&mdash;</td><td>approved</td><td><code>b9397398-838d-4e4c-8110-33a4e4bfa162</code></td></tr></table></body></html>
```

_Validation at the boundary: bad input is a clean 4xx, never a 500:_

```
$ curl -s -i -X POST localhost:8000/posts/the-behavior-of-red-foxes/candidates -H 'Content-Type: application/json' -d '{"image_id": "not-a-uuid"}' | grep -iE '^HTTP|^\{' 
HTTP/1.1 422 Unprocessable Entity
{"error":{"code":"validation_error","message":"Request validation failed","details":[{"loc":["body","image_id"],"msg":"Input should be a valid UUID, invalid character: found `n` at 0","type":"uuid_parsing"}]}}
```

_Covered by tests: `tests/test_api_boundary.py`, `tests/test_api_phase2.py`, `tests/test_api_phase3.py` (`test_post_and_matching_boundary_validation`, `test_review_endpoints_validate_input`, `test_posts_are_tenant_isolated`) and `tests/test_matching_real_embeddings.py::test_review_approve_reject_explain_and_conflicts`._

## Quality & documentation

### ☑ Q-1 · A small labeled evaluation dataset measures top-1 precision, and the number is in the README
_`eval/labels.yaml`: 15 posts with one correct image each (the headline metric), 3 general posts, 4 off-topic and 4 hard negatives. Probe P5:_

```
$ docker compose exec -T api python -m app.cli eval 2>&1 | grep -v INFO
Eval set e3 (sha256 5588934555dd): 15 posts with one correct image, 3 general posts, 4 off-topic posts, 4 hard negatives
Source: recorded AI outputs eval/snapshot.json (2026-11-27T03:12:08+00:00), replayed into tenant 'eval' · embeddings gemini-embedding-001
Guard g1 · similarity_min 0.78 · confidence_min 0.7 · label_min 0.6 · label_margin 0.02

Top-1 precision: 100.0% (15/15)
  precision when answering 15/15 = 100.0% · coverage 15/15
  general posts answered with the right class 3/3 · off-topic refused 4/4 · hard negatives refused 1/4
  cross-species false accepts: 0 (similarity alone: 1)

  post                                       kind          expected      suggested       sim rank*  result
  arctic-fox-siblings-at-play                one image     img_9949.jpg  img_9949.jpg  0.938     1  ok
  city-foxes-after-dark                      one image     img_b20d.jpg  img_b20d.jpg  0.876     1  ok
  a-fox-kit-near-the-den                     one image     img_2efb.jpg  img_2efb.jpg  0.893     1  ok
  what-wolves-carry-home                     one image     img_c08a.jpg  img_c08a.jpg  0.910     1  ok
  inside-a-wolfs-mouth                       one image     img_1db1.jpg  img_1db1.jpg  0.877     1  ok
  the-white-wolves-of-the-arctic             one image     img_e1f1.jpg  img_e1f1.jpg  0.879     1  ok
  wolves-at-rest                             one image     img_4fa4.jpg  img_4fa4.jpg  0.908     1  ok
  golden-retriever-bandana-style             one image     img_8fc1.jpg  img_8fc1.jpg  0.896     1  ok
  shih-tzu-puppy-energy                      one image     img_a8cd.jpg  img_a8cd.jpg  0.905     1  ok
  raising-a-husky-puppy                      one image     img_5dce.jpg  img_5dce.jpg  0.849     1  ok
  deer-on-misty-roads                        one image     img_2e09.jpg  img_2e09.jpg  0.899     1  ok
  fallow-deer-in-a-summer-meadow             one image     img_b229.jpg  img_b229.jpg  0.924     1  ok
  red-deer-in-a-winter-herd                  one image     img_416d.jpg  img_416d.jpg  0.933     1  ok
  black-bears-resting-in-trees               one image     img_35f8.jpg  img_35f8.jpg  0.879     1  ok
  a-bear-cub-explores-the-forest-floor       one image     img_727b.jpg  img_727b.jpg  0.901     1  ok
  the-behavior-of-red-foxes                  class-level   fox           img_bf3d.jpg  0.828     -  ok
  vulpes-vulpes-in-european-cities           class-level   fox           img_bf3d.jpg  0.788     -  ok
  wild-fox-species-of-the-northern-hemispher class-level   fox           img_9949.jpg  0.829     -  ok
  emperor-penguins-huddle                    off-topic     -             no match        -       -  ok (refused)
  sourdough-starter-basics                   off-topic     -             no match        -       -  ok (refused)
  coral-reef-bleaching                       off-topic     -             no match        -       -  ok (refused)
  keeping-indoor-cats-busy                   off-topic     -             no match        -       -  ok (refused)
  polar-bears-on-the-sea-ice                 hard negative bear          img_94fa.jpg  0.794     -  false accept (same class: bear)
  moose-in-the-boreal-forest                 hard negative deer          img_416d.jpg  0.837     -  false accept (same class: deer)
  dalmatian-spots                            hard negative dog           no match        -       -  ok (refused)
  fennec-fox-desert-life                     hard negative fox           img_bf3d.jpg  0.798     -  false accept (same class: fox)
  * rank of the labeled image by pure similarity, before the guard

Wrote eval/results.json
```

_The README shows the same number:_

```
$ grep -n 'Top-1 precision: 100.0% (15/15)' README.md | head -3
14:> **Status: all four phases built and run on real data.** 52 Pexels photos and 26 posts were processed by Gemini through the background worker (241 AI calls, $0.074 at list price, billed $0 on the free tier). **Top-1 precision: 100.0% (15/15)** on the labeled eval set, zero cross-species false accepts, 113 tests. Every requirement is proven in [`EVIDENCE.md`](EVIDENCE.md); how it was built (and where AI was wrong) is in [`BUILDLOG.md`](BUILDLOG.md). Plan: [`PLAN.md`](PLAN.md) · design doc: [`docs/DESIGN.md`](docs/DESIGN.md).
139:**Top-1 precision: 100.0% (15/15)**: of the 15 posts that each have exactly one correct image, the first suggested image is the labeled one for all 15 (`docker compose exec -T api python -m app.cli eval`, which writes [`eval/results.json`](eval/results.json)).
```

_`tests/test_matching_real_embeddings.py::test_p5_eval_reproduces_the_committed_results_and_the_readme_number` fails if the README, `eval/results.json` and a fresh eval disagree. The threshold sweep and how 0.78 was chosen are in the README's Evaluation section._

### ☑ Q-2 · README with architecture explanation and diagram; the required files from Section 11 present
_The README has the architecture diagram, layers table, exact run + seed steps, a probe walkthrough (P1–P6 as commands), the eval number and sweep, and a limitations section. The required files:_

```
$ ls -1 README.md capstone.yaml EVIDENCE.md BUILDLOG.md .env.example LICENSE .gitignore
.env.example
.gitignore
BUILDLOG.md
capstone.yaml
EVIDENCE.md
LICENSE
README.md
```
