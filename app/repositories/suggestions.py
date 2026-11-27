"""Suggestions (guard decisions on post-image pairs) and their human reviews. Tenant-scoped."""
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Review, Suggestion


def upsert(session: Session, tenant_id: str, *, post_id: uuid.UUID, image_id: uuid.UUID, rank: int,
           similarity: float, verdict: str, reasons: list[str], checks: list[dict[str, Any]],
           thresholds: dict[str, Any], guard_version: str) -> uuid.UUID:
    """Idempotent by (tenant, post, image, guard_version): re-asking updates the same row (same id)."""
    values = {"tenant_id": tenant_id, "post_id": post_id, "image_id": image_id, "rank": rank,
              "similarity": similarity, "verdict": verdict, "reasons": reasons, "checks": checks,
              "thresholds": thresholds, "guard_version": guard_version}
    stmt = insert(Suggestion).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Suggestion.tenant_id, Suggestion.post_id, Suggestion.image_id, Suggestion.guard_version],
        set_={k: stmt.excluded[k] for k in ("rank", "similarity", "verdict", "reasons", "checks", "thresholds")}
        | {"updated_at": func.now()},
    ).returning(Suggestion.id)
    return session.scalar(stmt)


def get(session: Session, tenant_id: str, suggestion_id: uuid.UUID) -> Suggestion | None:
    return session.scalar(select(Suggestion).where(Suggestion.tenant_id == tenant_id,
                                                   Suggestion.id == suggestion_id))


def list_(session: Session, tenant_id: str, *, post_id: uuid.UUID | None = None, verdict: str | None = None,
          review: str | None = None, limit: int = 50, offset: int = 0
          ) -> tuple[Sequence[tuple[Suggestion, Review | None]], int]:
    """review: 'pending' (no human decision yet), 'approved' or 'rejected'."""
    query = (select(Suggestion, Review)
             .outerjoin(Review, Review.suggestion_id == Suggestion.id)
             .where(Suggestion.tenant_id == tenant_id))
    if post_id:
        query = query.where(Suggestion.post_id == post_id)
    if verdict:
        query = query.where(Suggestion.verdict == verdict)
    if review == "pending":
        query = query.where(Review.id.is_(None))
    elif review:
        query = query.where(Review.decision == review)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.execute(query.order_by(Suggestion.post_id, Suggestion.rank, Suggestion.id)
                           .limit(limit).offset(offset)).all()
    return [(s, r) for s, r in rows], total


def review_for(session: Session, tenant_id: str, suggestion_id: uuid.UUID) -> Review | None:
    return session.scalar(select(Review).where(Review.tenant_id == tenant_id,
                                               Review.suggestion_id == suggestion_id))


def add_review(session: Session, tenant_id: str, suggestion_id: uuid.UUID, *, decision: str,
               note: str | None, reviewer: str | None) -> Review:
    review = Review(tenant_id=tenant_id, suggestion_id=suggestion_id, decision=decision, note=note,
                    reviewer=reviewer)
    session.add(review)
    session.flush()
    session.refresh(review)
    return review
