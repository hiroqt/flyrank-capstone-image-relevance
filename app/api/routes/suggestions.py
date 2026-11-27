import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from app.api.deps import SessionDep, TenantDep
from app.api.schemas import (
    GuardCheckOut,
    Page,
    ReviewDecisionOut,
    ReviewOut,
    ReviewRequest,
    ReviewState,
    SuggestionExplain,
    SuggestionOut,
)
from app.db.models import Image, Post, Review, Suggestion
from app.domain.errors import NotFound
from app.repositories import suggestions as suggestions_repo
from app.services import idempotency, review

router = APIRouter(prefix="/suggestions", tags=["review"])

IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$"),
]


def _suggestion_out(s: Suggestion, r: Review | None, post: Post, image: Image) -> SuggestionOut:
    return SuggestionOut(
        id=s.id, post_id=s.post_id, post_slug=post.slug, image_id=s.image_id, file_path=image.file_path,
        rank=s.rank, similarity=round(s.similarity, 4), verdict=s.verdict.upper(), reasons=s.reasons,
        guard_version=s.guard_version, review=ReviewOut.model_validate(r) if r else None, updated_at=s.updated_at,
    )


@router.get("", response_model=Page[SuggestionOut],
            summary="The review queue: guard decisions on post-image pairs, filterable")
def list_suggestions(session: SessionDep, tenant_id: TenantDep,
                     post_id: uuid.UUID | None = None,
                     verdict: Annotated[str | None, Query(pattern="^(accepted|rejected)$")] = None,
                     review_state: Annotated[ReviewState | None, Query(alias="review")] = None,
                     limit: Annotated[int, Query(ge=1, le=200)] = 50,
                     offset: Annotated[int, Query(ge=0)] = 0) -> Page[SuggestionOut]:
    rows, total = suggestions_repo.list_(session, tenant_id, post_id=post_id, verdict=verdict,
                                         review=review_state, limit=limit, offset=offset)
    items = [_suggestion_out(s, r, session.get(Post, s.post_id), session.get(Image, s.image_id)) for s, r in rows]
    return Page[SuggestionOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/{suggestion_id}/explain", response_model=SuggestionExplain,
            summary="Inspect why: every guard check with the observed value and its threshold")
def explain(suggestion_id: uuid.UUID, session: SessionDep, tenant_id: TenantDep) -> SuggestionExplain:
    s = suggestions_repo.get(session, tenant_id, suggestion_id)
    if s is None:
        raise NotFound(f"Suggestion {suggestion_id} not found")
    post, image = session.get(Post, s.post_id), session.get(Image, s.image_id)
    r = suggestions_repo.review_for(session, tenant_id, s.id)
    if s.verdict == "accepted":
        summary = (f"Selected: '{image.subject}' matches the post's subject '{post.subject_key}' with similarity "
                   f"{s.similarity:.3f}, and every guard check passed.")
    else:
        summary = "Refused: " + "; ".join(s.reasons)
    return SuggestionExplain(
        **_suggestion_out(s, r, post, image).model_dump(),
        post_subject=post.subject_key, image_subject=image.subject, image_subject_key=image.subject_key,
        image_confidence=image.confidence, image_status=image.status,
        checks=[GuardCheckOut(**c) for c in s.checks], thresholds=s.thresholds, summary=summary,
    )


def _decide(decision: str, suggestion_id: uuid.UUID, body: ReviewRequest | None, session, tenant_id: str,
            idempotency_key: str | None) -> JSONResponse:
    body = body or ReviewRequest()

    def action() -> tuple[int, dict]:
        outcome = review.decide(session, tenant_id, suggestion_id, decision, note=body.note, reviewer=body.reviewer)
        out = ReviewDecisionOut(suggestion_id=suggestion_id, verdict=outcome.suggestion.verdict.upper(),
                                review=ReviewOut.model_validate(outcome.review),
                                overrides_guard=outcome.overrides_guard)
        return 200, out.model_dump(mode="json")

    payload = {"suggestion_id": str(suggestion_id), "decision": decision, **body.model_dump()}
    status, content, replayed = idempotency.run_once(session, tenant_id, idempotency_key, payload, action)
    return JSONResponse(status_code=status, content=content,
                        headers={"Idempotency-Replayed": "true"} if replayed else None)


@router.post("/{suggestion_id}/approve", response_model=ReviewDecisionOut,
             summary="Approve a suggested pairing (409 if it was already rejected)")
def approve(suggestion_id: uuid.UUID, session: SessionDep, tenant_id: TenantDep, body: ReviewRequest | None = None,
            idempotency_key: IdempotencyKey = None) -> JSONResponse:
    return _decide("approved", suggestion_id, body, session, tenant_id, idempotency_key)


@router.post("/{suggestion_id}/reject", response_model=ReviewDecisionOut,
             summary="Reject a suggested pairing (409 if it was already approved)")
def reject(suggestion_id: uuid.UUID, session: SessionDep, tenant_id: TenantDep, body: ReviewRequest | None = None,
           idempotency_key: IdempotencyKey = None) -> JSONResponse:
    return _decide("rejected", suggestion_id, body, session, tenant_id, idempotency_key)
