"""Regression tests for failure modes found in review: retried items never pay twice, a superseded worker
can't overwrite or re-queue the new owner's work, and half-processed rows are never matched on stale data.
Shared requirements #3 (background job) and #5 (a retried action happens once)."""
from sqlalchemy import func, select, text, update

from app.db.models import AICall, Image, Job, Post
from app.db.session import get_engine, get_sessionmaker
from app.providers.base import PermanentProviderError, TransientProviderError
from app.providers.fake import FakeEmbedder
from app.repositories import jobs as jobs_repo
from app.repositories import posts as posts_repo
from app.services.jobs import enqueue_process_posts
from app.services.taxonomy import Taxonomy, resolver_version
from tests.conftest import TEST_RESOLVER_VERSION, drain, vision_json


class FailingEmbedder:
    """Wraps the fake embedder and raises on chosen call numbers (1-based)."""

    provider, model = "fake", "fake-embed-1"

    def __init__(self, fail_on: dict[int, Exception]):
        self.inner, self.fail_on, self.n = FakeEmbedder(), fail_on, 0
        self.dimensions = self.inner.dimensions

    def embed(self, texts):
        self.n += 1
        if self.n in self.fail_on:
            raise self.fail_on[self.n]
        return self.inner.embed(texts)


def _get(model, id_):
    with get_sessionmaker()() as s:
        return s.get(model, id_)


def _count(operation: str) -> int:
    with get_sessionmaker()() as s:
        return s.scalar(select(func.count()).select_from(AICall).where(AICall.operation == operation))


def test_a_retried_force_post_job_pays_for_the_topic_once(make_worker, test_settings):
    with get_sessionmaker()() as s, s.begin():
        posts_repo.upsert(s, "demo", slug="red-foxes", title="Red foxes", body="The red fox hunts at dusk.")
    with get_sessionmaker()() as s:
        enqueue_process_posts(s, "demo", force=True, settings=test_settings, resolver=TEST_RESOLVER_VERSION)
        s.commit()
    # call 1 = taxonomy labels, call 2 = the post's vectors (fails once), call 3 = the retry
    worker, fake = make_worker(embedder=FailingEmbedder({2: TransientProviderError("503")}))
    fake.topics = ['{"subject": "red fox", "category": "animal", "confidence": 0.95}']
    drain(worker)

    post = _post("red-foxes")
    assert (post.status, post.subject_key) == ("embedded", "fox")
    assert len(fake.text_calls) == 1 and _count("text") == 1   # the retry did not re-read the topic


def _post(slug):
    with get_sessionmaker()() as s:
        return s.scalar(select(Post).where(Post.slug == slug))


def test_new_vision_output_never_leaves_a_stale_subject_and_a_later_job_only_redoes_the_missing_step(
        add_image, enqueue, make_worker):
    image_id = add_image()
    enqueue()
    drain(make_worker([vision_json(subject="gray wolf", caption="A gray wolf in a forest")])[0])
    assert _get(Image, image_id).subject_key == "wolf"

    enqueue(force=True)  # re-tag: the model now says red fox, then the embedding step fails for good
    worker, _ = make_worker([vision_json()], embedder=FailingEmbedder({1: PermanentProviderError("bad request")}))
    drain(worker)
    image = _get(Image, image_id)
    assert (image.subject, image.status, image.subject_key) == ("red fox", "flagged", None)  # never 'wolf'

    job = enqueue()  # picks up only the unfinished image, and does not pay for vision again
    assert job.total == 1
    worker, fake = make_worker()
    drain(worker)
    image = _get(Image, image_id)
    assert (image.status, image.subject_key, len(fake.calls)) == ("tagged", "fox", 0)


def test_a_superseded_worker_cannot_finish_fail_or_requeue_the_item(add_image, enqueue):
    add_image()
    job = enqueue()
    with get_sessionmaker()() as s, s.begin():
        claim_a = jobs_repo.claim_next(s)
    with get_engine().begin() as conn:  # A's lock goes stale; B re-claims
        conn.execute(text("UPDATE job_items SET locked_at = now() - interval '1 hour'"))
    with get_sessionmaker()() as s, s.begin():
        jobs_repo.recover_stale(s, stale_after_s=300)
        claim_b = jobs_repo.claim_next(s)
    assert claim_b.attempts == claim_a.attempts + 1

    with get_sessionmaker()() as s, s.begin():
        assert not jobs_repo.complete_item(s, claim_a, "succeeded")
        assert not jobs_repo.retry_item_later(s, claim_a, 0, "timeout")
        assert not jobs_repo.fail_item(s, claim_a, "boom")
        assert jobs_repo.complete_item(s, claim_b, "succeeded")
    assert (_get(Job, job.id).processed, _get(Job, job.id).succeeded) == (1, 1)


def test_a_superseded_worker_cannot_overwrite_the_new_owners_vision_result(add_image, enqueue, make_worker):
    image_id = add_image()
    enqueue()
    with get_sessionmaker()() as s, s.begin():
        claim_a = jobs_repo.claim_next(s)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE job_items SET locked_at = now() - interval '1 hour'"))
    with get_sessionmaker()() as s, s.begin():
        jobs_repo.recover_stale(s, stale_after_s=300)
        claim_b = jobs_repo.claim_next(s)

    worker_a, _ = make_worker([vision_json(subject="gray wolf", caption="A gray wolf in a forest")])
    worker_a._process(claim_a)      # A finally answers: its result is refused, nothing is written
    assert _get(Image, image_id).status == "pending"
    worker_b, _ = make_worker([vision_json()])
    worker_b._process(claim_b)
    image = _get(Image, image_id)
    assert (image.subject, image.subject_key, image.status) == ("red fox", "fox", "tagged")


def test_a_post_whose_text_changed_after_its_topic_was_read_is_picked_up_again(client, make_worker, test_settings):
    client.post("/posts", json={"slug": "fox-notes", "title": "Fox notes",
                                "body": "The red fox hunts voles in the long grass."})
    worker, fake = make_worker()
    fake.topics = ['{"subject": "red fox", "category": "animal", "confidence": 0.9}']
    drain(worker)
    with get_sessionmaker()() as s, s.begin():  # the text changes, but the row still says 'embedded'
        s.execute(update(Post).where(Post.slug == "fox-notes").values(body="All about gray wolves and their packs."))
    with get_sessionmaker()() as s:
        job = enqueue_process_posts(s, "demo", force=False, settings=test_settings, resolver=TEST_RESOLVER_VERSION)
        assert job.total == 1


def test_resolver_version_changes_when_a_label_text_changes_without_a_version_bump():
    a = Taxonomy("t1", {"fox": "a small wild canid", "wolf": "a pack hunter"}, {})
    b = Taxonomy("t1", {"fox": "a small wild canid with a bushy tail", "wolf": "a pack hunter"}, {})
    assert resolver_version(a, 0.6, 0.02) != resolver_version(b, 0.6, 0.02)
    assert resolver_version(a, 0.6, 0.02) == resolver_version(a, 0.6, 0.02)
