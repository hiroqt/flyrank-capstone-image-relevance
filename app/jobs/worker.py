"""Background worker (brief section 4 part 4; shared requirement #3).

Run with:  python -m app.jobs.worker   (the `worker` service in docker-compose.yml)

Loop: recover stale items -> claim one queued item (SKIP LOCKED) -> process it -> record the outcome.
  - success                    -> item succeeded/flagged, job counters updated
  - transient provider error   -> retry later with exponential backoff + jitter, up to max_attempts
  - permanent error            -> item failed, image marked failed, alert raised
  - budget exceeded            -> whole job stopped, remaining items failed, alert raised
"""
import logging
import random
import signal
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import Job
from app.db.session import get_sessionmaker
from app.jobs.retry import backoff_seconds
from app.logging_config import configure_logging
from app.providers.base import PermanentProviderError, TransientProviderError
from app.providers.factory import build_ledger, build_vision
from app.providers.tracked import BudgetExceeded, TrackedVision
from app.repositories import images as images_repo
from app.repositories import jobs as jobs_repo
from app.repositories.jobs import ClaimedItem
from app.services import pipeline
from app.services.alerts import raise_alert

log = logging.getLogger("app.worker")


class Worker:
    def __init__(self, settings: Settings, session_factory: Callable[[], Session],
                 vision_factory: Callable[[], TrackedVision], *, rand: Callable[[], float] = random.random,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.session_factory = session_factory
        self._vision_factory = vision_factory
        self._vision: TrackedVision | None = None
        self._rand = rand
        self._sleep = sleep
        self._stopping = False

    # ---- lifecycle -------------------------------------------------------------------------
    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        log.info("Worker started (provider=%s)", self.settings.ai_provider)
        while not self._stopping:
            if not self.run_once():
                self._sleep(self.settings.worker_poll_interval_s)
        log.info("Worker stopped")

    def _stop(self, *_: object) -> None:
        log.info("Shutdown requested; finishing the current item")
        self._stopping = True

    def vision(self) -> TrackedVision:
        if self._vision is None:
            self._vision = self._vision_factory()  # raises ProviderNotConfigured if e.g. the key is missing
        return self._vision

    # ---- one iteration ---------------------------------------------------------------------
    def run_once(self) -> bool:
        """Process at most one item. Returns True if an item was claimed."""
        self._housekeeping()
        with self.session_factory() as session, session.begin():
            item = jobs_repo.claim_next(session)
        if item is None:
            return False
        self._process(item)
        return True

    def _housekeeping(self) -> None:
        with self.session_factory() as session, session.begin():
            recovered = jobs_repo.recover_stale(session, self.settings.worker_stale_after_s)
            if recovered:
                log.warning("Re-queued %d stale item(s) left running by a crashed worker", recovered)
            for job_id in jobs_repo.open_job_ids_without_open_items(session):
                self._close_job(session, job_id)

    def _process(self, item: ClaimedItem) -> None:
        with self.session_factory() as session:
            try:
                job = session.get(Job, item.job_id)
                ctx = pipeline.ItemContext(tenant_id=item.tenant_id, job_id=item.job_id, job_item_id=item.id,
                                           force=bool(job and job.force))
                status = pipeline.process_image(session, item.item_id, ctx, self.vision(), self.settings)
                if not jobs_repo.complete_item(session, item, "flagged" if status == "flagged" else "succeeded"):
                    session.rollback()
                    log.warning("Item %s was already finished by another worker; result discarded", item.id)
                    return
                self._close_job(session, item.job_id)
                session.commit()
                log.info("Item %s (image %s) -> %s", item.id, item.item_id, status)
                return
            except BudgetExceeded as exc:
                session.rollback()
                self._abort_job(item, str(exc))
            except (pipeline.PermanentItemError, PermanentProviderError) as exc:
                session.rollback()
                self._fail(item, str(exc))
            except TransientProviderError as exc:
                session.rollback()
                self._retry_or_fail(item, str(exc), exc.retry_after_s)
            except Exception as exc:  # noqa: BLE001: an unexpected bug still gets bounded retries + an alert
                session.rollback()
                log.exception("Unexpected error processing item %s", item.id)
                self._retry_or_fail(item, f"{type(exc).__name__}: {exc}", None)

    # ---- outcomes --------------------------------------------------------------------------
    def _retry_or_fail(self, item: ClaimedItem, error: str, retry_after: float | None) -> None:
        if item.attempts >= item.max_attempts:
            self._fail(item, f"Gave up after {item.attempts} attempts: {error}")
            return
        delay = backoff_seconds(item.attempts, self.settings.job_backoff_base_s, self.settings.job_backoff_max_s,
                                retry_after=retry_after, rand=self._rand)
        with self.session_factory() as session, session.begin():
            if not jobs_repo.retry_item_later(session, item, delay, error):
                return
        log.warning("Item %s attempt %d/%d failed (%s); retrying in %.1fs",
                    item.id, item.attempts, item.max_attempts, error, delay)

    def _fail(self, item: ClaimedItem, error: str) -> None:
        with self.session_factory() as session, session.begin():
            if not jobs_repo.fail_item(session, item, error):
                return
            images_repo.mark_failed(session, item.tenant_id, item.item_id, error, item.job_id)
            raise_alert(session, item.tenant_id, f"Image {item.item_id} failed: {error}", job_id=item.job_id)
            self._close_job(session, item.job_id)

    def _abort_job(self, item: ClaimedItem, error: str) -> None:
        with self.session_factory() as session, session.begin():
            jobs_repo.fail_item(session, item, error)
            stopped = jobs_repo.fail_remaining(session, item.job_id, error)
            raise_alert(session, item.tenant_id, f"Job stopped, {stopped + 1} item(s) not processed: {error}",
                        job_id=item.job_id)
            self._close_job(session, item.job_id, error=error)

    def _close_job(self, session: Session, job_id, error: str | None = None) -> None:
        job = jobs_repo.finish_if_done(session, job_id, error)
        if job is None:
            return
        log.info("Job %s finished: %s (%d tagged, %d flagged, %d failed of %d)",
                 job.id, job.status, job.succeeded, job.flagged, job.failed, job.total)
        if job.failed and error is None:
            raise_alert(session, job.tenant_id, f"Job finished '{job.status}' with {job.failed} failed "
                        f"of {job.total} item(s)", job_id=job.id, level="warning")


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    session_factory = get_sessionmaker()
    ledger = build_ledger(settings, session_factory)
    Worker(settings, session_factory, lambda: build_vision(settings, ledger)).run_forever()


if __name__ == "__main__":
    main()
