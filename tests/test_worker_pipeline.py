"""Section 6 AI-1..AI-4 and shared requirements #3, #5, #7: validated vision output, flagging,
batch job with retries + failure alerts, per-call cost rows, idempotency, budget guard."""
from decimal import Decimal

from sqlalchemy import select, text, update

from app.db.models import AICall, Alert, Image, ImageTag, Job, JobItem
from app.db.session import get_engine, get_sessionmaker
from app.providers.base import (
    PermanentProviderError,
    ProviderNotConfigured,
    RateLimitedError,
    TransientProviderError,
)
from app.providers.pricing import PriceBook
from app.providers.tracked import CostLedger
from app.repositories import jobs as jobs_repo
from tests.conftest import drain, vision_json

INVALID = '{"subject": "red fox"}'


def _get(model, id_):
    with get_sessionmaker()() as s:
        return s.get(model, id_)


def _calls(operation: str | None = "vision"):
    """Cost rows in order. Defaults to vision calls; pass operation=None for every row."""
    with get_sessionmaker()() as s:
        query = select(AICall).order_by(AICall.id)
        if operation:
            query = query.where(AICall.operation == operation)
        return s.scalars(query).all()


def _alerts():
    with get_sessionmaker()() as s:
        return s.scalars(select(Alert).order_by(Alert.created_at)).all()


def _items(job_id):
    with get_sessionmaker()() as s:
        return s.scalars(select(JobItem).where(JobItem.job_id == job_id)).all()


def test_valid_output_tags_image_and_records_one_cost_row(add_image, enqueue, make_worker):
    image_id = add_image()
    job = enqueue()
    worker, fake = make_worker([vision_json()])
    assert drain(worker) == 1

    image = _get(Image, image_id)
    assert (image.status, image.subject, image.category, image.confidence) == ("tagged", "red fox", "animal", 0.94)
    assert image.caption == "A red fox standing in a forest"
    assert image.last_job_id == job.id
    with get_sessionmaker()() as s:
        tags = {(t.kind, t.tag) for t in s.scalars(select(ImageTag).where(ImageTag.image_id == image_id))}
    assert {("subject", "red fox"), ("category", "animal"), ("attribute", "orange fur")} <= tags

    finished = _get(Job, job.id)
    assert (finished.status, finished.processed, finished.succeeded, finished.failed) == ("succeeded", 1, 1, 0)
    calls = _calls(operation=None)
    # One vision call, one embedding call for the image (text + subject in a single request),
    # and one for the taxonomy labels the first time they are needed. Every call has a cost row.
    assert [(c.operation, c.entity_type, c.status, c.job_id) for c in calls] == [
        ("embed", "label", "ok", job.id), ("vision", "image", "ok", job.id), ("embed", "image", "ok", job.id)]
    assert [c.entity_id for c in calls[1:]] == [str(image_id)] * 2
    assert len(_calls("vision")) == len(fake.calls)
    assert (image.subject_key, image.subject_key_detail["best"]) == ("fox", "fox")


def test_low_confidence_is_flagged_not_accepted(add_image, enqueue, make_worker):
    image_id = add_image()
    job = enqueue()
    worker, _ = make_worker([vision_json(subject="canid", confidence=0.42)])
    drain(worker)

    image = _get(Image, image_id)
    assert image.status == "flagged"
    assert image.flag_reasons[0] == "Low confidence 0.42 < 0.70: identification is uncertain, needs human review"
    # "canid" is not one of the taxonomy classes either: a second, independent reason.
    assert image.flag_reasons[1].startswith("Subject 'canid' does not resolve to a known category")
    assert _get(Job, job.id).flagged == 1


def test_confident_answer_about_an_unknown_subject_is_still_flagged(add_image, enqueue, make_worker):
    """Self-reported confidence is not trusted on its own: the subject must resolve to a known class."""
    image_id = add_image()
    enqueue()
    worker, _ = make_worker([vision_json(subject="cat", confidence=0.97)])
    drain(worker)

    image = _get(Image, image_id)
    assert (image.status, image.subject_key) == ("flagged", "unknown")
    assert image.flag_reasons == ["Subject 'cat' does not resolve to a known category "
                                  "(closest category is 'cat' (1.00), which is outside the taxonomy)"]


def test_invalid_output_is_repaired_and_every_attempt_is_logged(add_image, enqueue, make_worker):
    image_id = add_image()
    enqueue()
    worker, fake = make_worker([INVALID, vision_json()])
    drain(worker)

    assert _get(Image, image_id).status == "tagged"
    assert [(c.status, c.attempt) for c in _calls()] == [("invalid_output", 1), ("ok", 2)]
    assert "rejected by schema validation" in fake.calls[1]


def test_output_still_invalid_after_repairs_is_never_trusted(add_image, enqueue, make_worker):
    image_id = add_image()
    job = enqueue()
    worker, fake = make_worker([INVALID, INVALID, INVALID])
    drain(worker)

    image = _get(Image, image_id)
    assert image.status == "failed"
    assert image.subject is None and image.caption is None  # nothing from the bad output was stored
    assert "failed schema validation 3 times" in image.flag_reasons[0]
    assert [c.status for c in _calls()] == ["invalid_output"] * 3
    assert len(fake.calls) == 3  # permanent: no job-level retry on top of the repairs
    assert _get(Job, job.id).status == "failed"
    assert any(str(image_id) in a.message for a in _alerts())


def test_transient_error_is_retried_with_backoff_then_succeeds(add_image, enqueue, make_worker):
    image_id = add_image()
    job = enqueue()
    worker, _ = make_worker([TransientProviderError("Gemini API 503: overloaded"), vision_json()])
    assert drain(worker) == 2

    assert _get(Image, image_id).status == "tagged"
    (item,) = _items(job.id)
    assert (item.status, item.attempts) == ("succeeded", 2)
    assert [c.status for c in _calls()] == ["error", "ok"]
    assert _get(Job, job.id).status == "succeeded"


def test_rate_limit_is_logged_and_retried(add_image, enqueue, make_worker):
    add_image()
    enqueue()
    worker, _ = make_worker([RateLimitedError("Gemini API 429"), vision_json()])
    drain(worker)
    assert [c.status for c in _calls()] == ["rate_limited", "ok"]


def test_gives_up_after_max_attempts_and_raises_failure_alert(add_image, enqueue, make_worker):
    image_id = add_image()
    job = enqueue()
    worker, _ = make_worker([TransientProviderError("timeout")] * 3)
    assert drain(worker) == 3

    (item,) = _items(job.id)
    assert (item.status, item.attempts) == ("failed", 3)
    assert "Gave up after 3 attempts" in item.last_error
    assert _get(Image, image_id).status == "failed"
    assert _get(Job, job.id).status == "failed"
    assert [c.status for c in _calls()] == ["error"] * 3
    assert len(_alerts()) >= 1


def test_permanent_provider_error_is_not_retried(add_image, enqueue, make_worker):
    add_image()
    job = enqueue()
    worker, fake = make_worker([PermanentProviderError("Gemini API 400: bad request")])
    assert drain(worker) == 1
    assert len(fake.calls) == 1
    assert _get(Job, job.id).status == "failed"


def test_missing_api_key_fails_items_with_a_clear_alert(add_image, enqueue, make_worker):
    add_image()
    job = enqueue()

    def no_key():
        raise ProviderNotConfigured("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")

    worker, _ = make_worker(vision_factory=no_key)
    drain(worker)
    assert _get(Job, job.id).status == "failed"
    assert any("GEMINI_API_KEY is not set" in a.message for a in _alerts())


def test_mixed_batch_finishes_partial(add_image, enqueue, make_worker):
    add_image(), add_image()
    job = enqueue()
    worker, _ = make_worker([vision_json(), INVALID, INVALID, INVALID])
    drain(worker)
    finished = _get(Job, job.id)
    assert (finished.status, finished.succeeded, finished.failed, finished.processed) == ("partial", 1, 1, 2)


def test_budget_guard_stops_the_job_before_any_paid_call(add_image, enqueue, make_worker, test_settings):
    add_image(), add_image()
    job = enqueue()
    pricey = PriceBook({"providers": {"fake": {"tier": "paid", "models": {},
                                               "unknown_model": {"input": 1000, "output": 1000}}}})
    ledger = CostLedger(get_sessionmaker(), pricey, budget_usd=0.01)
    worker, fake = make_worker([vision_json(), vision_json()], ledger=ledger)
    drain(worker)

    assert fake.calls == []  # the provider was never called
    finished = _get(Job, job.id)
    assert (finished.status, finished.failed) == ("failed", 2)
    assert "AI budget exceeded" in finished.error
    assert [c.status for c in _calls(operation=None)] == ["budget_blocked"]
    assert _calls(operation=None)[0].cost_usd == Decimal("0")


def test_rerun_without_force_skips_done_images_and_force_reprocesses(add_image, enqueue, make_worker):
    add_image()
    enqueue()
    drain(make_worker([vision_json()])[0])

    again = enqueue()
    assert (again.total, again.status) == (0, "succeeded")
    assert enqueue(force=True).total == 1


def test_item_handed_to_two_workers_is_paid_and_counted_once(add_image, enqueue, make_worker):
    """A slow worker A outlives the stale-lock timeout; worker B re-claims and finishes the item."""
    add_image()
    job = enqueue()
    with get_sessionmaker()() as s, s.begin():
        claimed_by_a = jobs_repo.claim_next(s)
    with get_engine().begin() as conn:  # A's lock goes stale
        conn.execute(text("UPDATE job_items SET locked_at = now() - interval '1 hour'"))

    worker_b, fake = make_worker([vision_json()])
    drain(worker_b)  # recovers the stale item, processes it: 1 paid call
    worker_b._process(claimed_by_a)  # A finally gets to it: same job already wrote the image

    assert len(fake.calls) == 1
    finished = _get(Job, job.id)
    assert (finished.processed, finished.succeeded) == (1, 1)


def test_stale_running_items_are_requeued(add_image, enqueue):
    add_image()
    job = enqueue()
    with get_sessionmaker()() as s, s.begin():
        jobs_repo.claim_next(s)
        s.execute(update(JobItem).values(locked_at=text("now() - interval '1 hour'")))
    with get_sessionmaker()() as s, s.begin():
        assert jobs_repo.recover_stale(s, stale_after_s=300) == 1
    (item,) = _items(job.id)
    assert item.status == "queued"
