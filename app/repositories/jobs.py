"""Job + job-item data access, including the worker's claim query."""
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Job, JobItem


@dataclass(frozen=True)
class ClaimedItem:
    id: int
    job_id: uuid.UUID
    tenant_id: str
    item_type: str
    item_id: uuid.UUID
    attempts: int
    max_attempts: int


def create(session: Session, tenant_id: str, *, job_type: str, force: bool, item_type: str,
           item_ids: list[uuid.UUID], max_attempts: int) -> Job:
    job = Job(tenant_id=tenant_id, type=job_type, force=force, total=len(item_ids))
    if not item_ids:
        job.status = "succeeded"
        job.finished_at = func.now()
    session.add(job)
    session.flush()
    if item_ids:
        session.execute(insert(JobItem), [
            {"job_id": job.id, "tenant_id": tenant_id, "item_type": item_type, "item_id": item_id,
             "max_attempts": max_attempts}
            for item_id in item_ids
        ])
    session.refresh(job)
    return job


def get(session: Session, tenant_id: str, job_id: uuid.UUID) -> Job | None:
    return session.scalar(select(Job).where(Job.tenant_id == tenant_id, Job.id == job_id))


def list_(session: Session, tenant_id: str, *, status: str | None, limit: int, offset: int) -> Sequence[Job]:
    query = select(Job).where(Job.tenant_id == tenant_id)
    if status:
        query = query.where(Job.status == status)
    return session.scalars(query.order_by(Job.created_at.desc()).limit(limit).offset(offset)).all()


# FOR UPDATE SKIP LOCKED: several workers can poll the same table; each row is handed to exactly one.
_CLAIM_SQL = text("""
    UPDATE job_items
       SET status = 'running', locked_at = now(), attempts = attempts + 1, updated_at = now()
     WHERE id = (
            SELECT id FROM job_items
             WHERE status = 'queued' AND next_run_at <= now()
             ORDER BY next_run_at, id
             FOR UPDATE SKIP LOCKED
             LIMIT 1)
 RETURNING id, job_id, tenant_id, item_type, item_id, attempts, max_attempts
""")


def claim_next(session: Session) -> ClaimedItem | None:
    row = session.execute(_CLAIM_SQL).mappings().first()
    if row is None:
        return None
    session.execute(update(Job).where(Job.id == row["job_id"], Job.status == "queued")
                    .values(status="running", started_at=func.now()))
    return ClaimedItem(**row)


def recover_stale(session: Session, stale_after_s: int) -> int:
    """Items left 'running' by a crashed worker go back to the queue."""
    result = session.execute(text("""
        UPDATE job_items SET status = 'queued', locked_at = NULL, updated_at = now()
         WHERE status = 'running' AND locked_at < now() - make_interval(secs => :secs)
    """), {"secs": stale_after_s})
    return result.rowcount or 0


def _still_ours(item: ClaimedItem):
    """The claim token: status 'running' AND the attempt number we claimed. A stale-lock recovery that
    hands the item to another worker bumps `attempts`, so the superseded worker can no longer finish,
    fail or re-queue it."""
    return (JobItem.id == item.id) & (JobItem.status == "running") & (JobItem.attempts == item.attempts)


def still_claimed(session: Session, job_item_id: int, attempt: int) -> bool:
    """Lock our item row (until this transaction ends) and confirm we still own the claim. Used right
    before committing a paid result mid-item, so a superseded worker never overwrites the new owner."""
    row = session.execute(select(JobItem.status, JobItem.attempts).where(JobItem.id == job_item_id)
                          .with_for_update()).first()
    return row is not None and row.status == "running" and row.attempts == attempt


def complete_item(session: Session, item: ClaimedItem, outcome: str) -> bool:
    """outcome: 'succeeded' | 'flagged'. Only counts if the claim is still ours: if a stale-lock recovery
    handed the item to another worker, nothing is double-counted or overwritten."""
    result = session.execute(update(JobItem).where(_still_ours(item))
                             .values(status=outcome, locked_at=None, last_error=None))
    if not result.rowcount:
        return False
    counter = Job.flagged if outcome == "flagged" else Job.succeeded
    session.execute(update(Job).where(Job.id == item.job_id)
                    .values({Job.processed: Job.processed + 1, counter: counter + 1}))
    return True


def retry_item_later(session: Session, item: ClaimedItem, delay_s: float, error: str) -> bool:
    result = session.execute(update(JobItem).where(_still_ours(item)).values(
        status="queued", locked_at=None, last_error=error[:1000],
        next_run_at=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, delay_s),
    ))
    return bool(result.rowcount)


def fail_item(session: Session, item: ClaimedItem, error: str) -> bool:
    result = session.execute(update(JobItem).where(_still_ours(item))
                             .values(status="failed", locked_at=None, last_error=error[:1000]))
    if not result.rowcount:
        return False
    session.execute(update(Job).where(Job.id == item.job_id)
                    .values({Job.processed: Job.processed + 1, Job.failed: Job.failed + 1}))
    return True


def fail_remaining(session: Session, job_id: uuid.UUID, error: str) -> int:
    """Abort: every queued item of the job fails with the same reason (e.g. budget exceeded)."""
    result = session.execute(update(JobItem).where(JobItem.job_id == job_id, JobItem.status == "queued")
                             .values(status="failed", last_error=error[:1000]))
    count = result.rowcount or 0
    if count:
        session.execute(update(Job).where(Job.id == job_id)
                        .values({Job.processed: Job.processed + count, Job.failed: Job.failed + count}))
    return count


def finish_if_done(session: Session, job_id: uuid.UUID, error: str | None = None) -> Job | None:
    """Close the job once no item is queued or running. Returns the job if it just finished."""
    open_items = session.scalar(select(func.count()).select_from(JobItem)
                                .where(JobItem.job_id == job_id, JobItem.status.in_(("queued", "running"))))
    if open_items:
        return None
    job = session.get(Job, job_id, with_for_update=True)
    if job is None or job.finished_at is not None:
        return None
    if job.failed == 0:
        job.status = "succeeded"
    elif job.succeeded + job.flagged == 0:
        job.status = "failed"
    else:
        job.status = "partial"
    if error:
        job.error = error[:1000]
    job.finished_at = func.now()
    session.flush()
    session.refresh(job)
    return job


def open_job_ids_without_open_items(session: Session) -> list[uuid.UUID]:
    """Jobs still marked queued/running whose items are all terminal (e.g. two workers finished the
    last two items at the same moment and neither saw the other's commit)."""
    return list(session.scalars(text("""
        SELECT j.id FROM jobs j
         WHERE j.status IN ('queued', 'running')
           AND NOT EXISTS (SELECT 1 FROM job_items i
                            WHERE i.job_id = j.id AND i.status IN ('queued', 'running'))
    """)))
