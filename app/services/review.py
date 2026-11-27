"""Human review of suggestions (brief section 4 part 5): approve / reject / inspect why.

One decision per suggestion. Repeating the same decision is a no-op (the stored review is
returned); a contradicting decision is a 409, because two different answers can't both be true.
A reviewer may approve a pairing the guard rejected (human override); the response says so.
"""
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Review, Suggestion
from app.domain.errors import Conflict, NotFound
from app.repositories import suggestions as suggestions_repo


@dataclass(frozen=True)
class ReviewOutcome:
    suggestion: Suggestion
    review: Review
    created: bool

    @property
    def overrides_guard(self) -> bool:
        return (self.review.decision == "approved") != (self.suggestion.verdict == "accepted")


def decide(session: Session, tenant_id: str, suggestion_id: uuid.UUID, decision: str, *,
           note: str | None, reviewer: str | None) -> ReviewOutcome:
    suggestion = suggestions_repo.get(session, tenant_id, suggestion_id)
    if suggestion is None:
        raise NotFound(f"Suggestion {suggestion_id} not found")
    existing = suggestions_repo.review_for(session, tenant_id, suggestion_id)
    if existing is None:
        try:
            with session.begin_nested():
                review = suggestions_repo.add_review(session, tenant_id, suggestion_id, decision=decision,
                                                     note=note, reviewer=reviewer)
            return ReviewOutcome(suggestion, review, created=True)
        except IntegrityError:  # a concurrent reviewer decided first: fall through to compare
            existing = suggestions_repo.review_for(session, tenant_id, suggestion_id)
            if existing is None:
                raise
    if existing.decision != decision:
        raise Conflict(f"Suggestion {suggestion_id} was already {existing.decision}; "
                       f"it cannot also be {decision}")
    return ReviewOutcome(suggestion, existing, created=False)
