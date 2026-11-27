"""Phase 2 endpoints: POST/GET /jobs, GET /images, GET /costs, GET /alerts.
Covers validation at the boundary, idempotency keys and tenant isolation."""
import uuid

from sqlalchemy import func, select

from app.db.models import Job
from app.db.session import get_sessionmaker
from tests.conftest import drain, vision_json


def _job_count() -> int:
    with get_sessionmaker()() as s:
        return s.scalar(select(func.count()).select_from(Job))


def test_post_jobs_enqueues_and_returns_202_without_calling_ai(client, add_image):
    add_image(), add_image()
    resp = client.post("/jobs", json={"type": "process_images"})
    assert resp.status_code == 202
    body = resp.json()
    assert (body["status"], body["total"], body["processed"], body["progress"]) == ("queued", 2, 0, 0.0)


def test_same_idempotency_key_returns_the_same_job_once(client, add_image):
    add_image()
    headers = {"Idempotency-Key": "batch-2026-11-25"}
    first = client.post("/jobs", json={"type": "process_images"}, headers=headers)
    second = client.post("/jobs", json={"type": "process_images"}, headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["Idempotency-Replayed"] == "true"
    assert _job_count() == 1


def test_reusing_an_idempotency_key_for_a_different_request_is_409(client, add_image):
    add_image()
    headers = {"Idempotency-Key": "k1"}
    client.post("/jobs", json={"type": "process_images"}, headers=headers)
    resp = client.post("/jobs", json={"type": "process_images", "force": True}, headers=headers)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "conflict"


def test_bad_job_requests_are_422(client):
    for body in ({"type": "nope"}, {}, {"type": "process_images", "extra": 1}, {"type": "process_images",
                                                                                 "force": "maybe"}):
        resp = client.post("/jobs", json=body)
        assert resp.status_code == 422, body
    resp = client.post("/jobs", json={"type": "process_images"}, headers={"Idempotency-Key": "has spaces"})
    assert resp.status_code == 422


def test_get_job_validation_and_404(client):
    assert client.get("/jobs/not-a-uuid").status_code == 422
    assert client.get(f"/jobs/{uuid.uuid4()}").status_code == 404


def test_job_progress_after_worker_runs(client, add_image, make_worker):
    add_image()
    job_id = client.post("/jobs", json={"type": "process_images"}).json()["id"]
    drain(make_worker([vision_json()])[0])
    body = client.get(f"/jobs/{job_id}").json()
    assert (body["status"], body["succeeded"], body["progress"]) == ("succeeded", 1, 1.0)
    assert body["finished_at"] is not None
    assert client.get("/jobs").json()[0]["id"] == job_id


def test_other_tenant_cannot_see_jobs_or_images(client, add_image):
    image_id = add_image()
    job_id = client.post("/jobs", json={"type": "process_images"}).json()["id"]
    other = {"X-Tenant-Id": "other"}
    assert client.get(f"/jobs/{job_id}", headers=other).status_code == 404
    assert client.get(f"/images/{image_id}", headers=other).status_code == 404
    assert client.get("/images", headers=other).json()["total"] == 0


def test_images_table_filters_flagged(client, add_image, make_worker):
    add_image(), add_image()
    client.post("/jobs", json={"type": "process_images"})
    drain(make_worker([vision_json(), vision_json(subject="canid", confidence=0.41)])[0])

    flagged = client.get("/images", params={"status": "flagged"}).json()
    assert flagged["total"] == 1
    assert flagged["items"][0]["flag_reasons"][0].startswith("Low confidence 0.41")
    assert client.get("/images").json()["total"] == 2


def test_image_list_query_validation(client):
    assert client.get("/images", params={"status": "bogus"}).status_code == 422
    assert client.get("/images", params={"limit": 0}).status_code == 422
    assert client.get("/images", params={"offset": -1}).status_code == 422


def test_image_detail_shows_tags_and_its_ai_calls(client, add_image, make_worker):
    image_id = add_image()
    client.post("/jobs", json={"type": "process_images"})
    drain(make_worker(['{"bad": 1}', vision_json()])[0])

    body = client.get(f"/images/{image_id}").json()
    assert body["status"] == "tagged"
    assert {"kind": "subject", "tag": "red fox"} in body["tags"]
    # Every call made for this image: the rejected answer, the repaired one, then its embedding.
    assert [(c["operation"], c["status"]) for c in body["ai_calls"]] == [
        ("vision", "invalid_output"), ("vision", "ok"), ("embed", "ok")]


def test_cost_log_has_an_entry_for_every_call(client, add_image, make_worker):
    add_image(), add_image()
    job_id = client.post("/jobs", json={"type": "process_images"}).json()["id"]
    worker, fake = make_worker([vision_json(), '{"bad": 1}', vision_json()])
    drain(worker)

    report = client.get("/costs", params={"job_id": job_id}).json()
    # 3 vision calls + 2 image embeddings + 1 taxonomy-label embedding, all attributed to the job.
    assert report["calls"]["total"] == len(fake.calls) + 3 == 6
    assert all(c["job_id"] == job_id for c in report["calls"]["items"])
    assert {c["entity_type"] for c in report["calls"]["items"]} == {"image", "label"}
    statuses = {(t["operation"], t["status"]): t["calls"] for t in report["totals"]}
    assert statuses == {("vision", "ok"): 2, ("vision", "invalid_output"): 1, ("embed", "ok"): 3}
    vision_only = client.get("/costs", params={"job_id": job_id, "operation": "vision"}).json()
    assert vision_only["calls"]["total"] == len(fake.calls) == 3
    assert report["budget_usd"] == 1.0
    assert client.get("/costs", params={"operation": "bogus"}).status_code == 422


def test_alerts_endpoint_lists_job_failures(client, add_image, make_worker):
    add_image()
    job_id = client.post("/jobs", json={"type": "process_images"}).json()["id"]
    drain(make_worker(['{"bad": 1}'] * 3)[0])
    alerts = client.get("/alerts", params={"job_id": job_id}).json()
    assert alerts and any("failed schema validation" in a["message"] for a in alerts)
