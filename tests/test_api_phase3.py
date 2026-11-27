"""Posts, the post batch job, and boundary validation for the matching + review endpoints (fake AI).
Shared requirements #2 (clean 4xx), #3 (background job), #4 (tenant isolation), #5 (idempotency)."""
import json
import uuid

from sqlalchemy import select

from app.db.models import AICall, Post
from app.db.session import get_sessionmaker
from tests.conftest import TEST_THRESHOLDS, drain, vision_json


def topic_json(**overrides) -> str:
    data = {"subject": "red fox", "category": "animal", "confidence": 0.95}
    data.update(overrides)
    return json.dumps(data)


def _post(slug="foxes-at-dusk", title="The red fox at dusk",
          body="At dusk the red fox leaves the forest to hunt; a wild fox with orange fur."):
    return {"slug": slug, "title": title, "body": body}


def _post_row(slug):
    with get_sessionmaker()() as s:
        return s.scalar(select(Post).where(Post.slug == slug))


def test_create_post_enqueues_its_embedding_job_and_the_worker_reads_its_topic(client, make_worker):
    created = client.post("/posts", json=_post())
    assert created.status_code == 201
    body = created.json()
    assert (body["status"], body["slug"]) == ("pending", "foxes-at-dusk")
    worker, fake = make_worker()
    fake.topics = [topic_json()]
    drain(worker)

    post = client.get("/posts/foxes-at-dusk").json()
    assert (post["status"], post["subject_key"], post["topic"]["subject"]) == ("embedded", "fox", "red fox")
    job = client.get(f"/jobs/{body['job_id']}").json()
    assert (job["type"], job["status"], job["succeeded"]) == ("process_posts", "succeeded", 1)
    with get_sessionmaker()() as s:
        ops = [(c.operation, c.entity_type) for c in s.scalars(select(AICall).order_by(AICall.id))]
    assert ("text", "post") in ops and ("embed", "post") in ops  # every call has a cost row


def test_invalid_topic_output_is_repaired_and_never_trusted(client, make_worker):
    client.post("/posts", json=_post())
    worker, fake = make_worker()
    fake.topics = ['{"subject": "red fox"}', topic_json(confidence=1.7), topic_json()]
    drain(worker)
    assert _post_row("foxes-at-dusk").topic["subject"] == "red fox"
    with get_sessionmaker()() as s:
        statuses = [c.status for c in s.scalars(select(AICall).where(AICall.operation == "text").order_by(AICall.id))]
    assert statuses == ["invalid_output", "invalid_output", "ok"]


def test_a_post_about_food_never_resolves_to_an_animal(client, make_worker):
    client.post("/posts", json=_post(slug="sourdough", title="Sourdough", body="Feed the starter flour and water."))
    worker, fake = make_worker()
    fake.topics = [topic_json(subject="sourdough bread", category="food")]
    drain(worker)
    post = _post_row("sourdough")
    assert post.subject_key == "unknown"
    assert post.subject_key_detail["reason"] == "'sourdough bread' is food, not an animal in the taxonomy"


def test_ranking_and_guard_end_to_end_with_the_fake_provider(client, add_image, make_worker, monkeypatch):
    # The bag-of-words fake embedder scores far below a real model, so use the test thresholds here.
    monkeypatch.setattr("app.api.routes.posts.current_thresholds", lambda settings: TEST_THRESHOLDS)
    fox_img, wolf_img = add_image(), add_image()
    client.post("/jobs", json={"type": "process_images"})
    client.post("/posts", json=_post())
    worker, fake = make_worker([vision_json(), vision_json(subject="gray wolf", caption="A gray wolf in a forest",
                                                           attributes=["gray fur", "forest"])])
    fake.topics = [topic_json()]
    drain(worker)

    body = client.get("/posts/foxes-at-dusk/images").json()
    assert body["status"] == "match", json.dumps(body, indent=1)[:3000]
    assert body["suggestion"]["image_id"] == str(fox_img)
    forced = client.post("/posts/foxes-at-dusk/candidates", json={"image_id": str(wolf_img)}).json()
    assert forced["verdict"] == "REJECTED"
    assert "Animal category mismatch: expected fox, detected wolf" in forced["reasons"]
    again = client.get("/posts/foxes-at-dusk/images").json()   # suggestions are upserted: same ids on repeat
    assert again["suggestion"]["suggestion_id"] == body["suggestion"]["suggestion_id"]


def test_matching_before_the_post_is_embedded_is_a_clean_409(client):
    client.post("/posts", json=_post())
    response = client.get("/posts/foxes-at-dusk/images")
    assert response.status_code == 409 and "process_posts" in response.json()["error"]["message"]


def test_post_and_matching_boundary_validation(client):
    assert client.post("/posts", json=_post()).status_code == 201
    assert client.post("/posts", json=_post()).status_code == 409                       # duplicate slug
    assert client.post("/posts", json={**_post(slug="Bad Slug!")}).status_code == 422
    assert client.post("/posts", json={**_post(slug="ok-slug"), "extra": 1}).status_code == 422
    assert client.post("/posts", json={"slug": "short-body", "title": "Hi there", "body": "too short"}).status_code == 422
    assert client.get("/posts/no-such-post").status_code == 404
    assert client.get("/posts/bad slug!/images").status_code == 422
    assert client.get("/posts/foxes-at-dusk/images", params={"limit": 0}).status_code == 422
    assert client.get("/posts/foxes-at-dusk/images", params={"limit": 51}).status_code == 422
    assert client.post("/posts/foxes-at-dusk/candidates", json={"image_id": "not-a-uuid"}).status_code == 422
    assert client.post("/posts/foxes-at-dusk/candidates", json={}).status_code == 422
    assert client.post("/jobs", json={"type": "process_everything"}).status_code == 422


def test_review_endpoints_validate_input(client):
    missing = str(uuid.uuid4())
    assert client.get(f"/suggestions/{missing}/explain").status_code == 404
    assert client.post(f"/suggestions/{missing}/approve").status_code == 404
    assert client.post("/suggestions/not-a-uuid/approve").status_code == 422
    assert client.post(f"/suggestions/{missing}/reject", json={"note": "x" * 1001}).status_code == 422
    assert client.post(f"/suggestions/{missing}/reject", json={"verdict": "nope"}).status_code == 422
    assert client.get("/suggestions", params={"verdict": "maybe"}).status_code == 422
    assert client.get("/suggestions", params={"review": "someday"}).status_code == 422


def test_posts_are_tenant_isolated(client):
    client.post("/posts", json=_post())
    other = {"X-Tenant-Id": "other"}
    assert client.get("/posts/foxes-at-dusk", headers=other).status_code == 404
    assert client.get("/posts", headers=other).json()["total"] == 0
    assert client.post("/posts", json=_post(), headers=other).status_code == 201       # same slug, other tenant
