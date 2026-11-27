"""Similarity ranking + the mismatch guard for one post (brief section 4 parts 2-3, section 5).

Reads stored vectors only: no AI call ever happens on this request path (brief section 4 part 4).

    rank every embedded image by cosine similarity to the post
    -> run the guard on each (ranking stays pure similarity; the guard decides)
    -> suggestion = the highest-ranked ACCEPTED image, else "no_confident_match" + reasons
"""
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.db.models import Image, Post
from app.domain.errors import Conflict, NotFound
from app.repositories import embeddings as embeddings_repo
from app.repositories import images as images_repo
from app.repositories import suggestions as suggestions_repo
from app.services import guard
from app.services.guard import GuardDecision, ImageFacts, PostFacts
from app.services.thresholds import Thresholds

MatchStatus = Literal["match", "no_confident_match"]


@dataclass(frozen=True)
class Candidate:
    image: Image
    rank: int
    similarity: float
    decision: GuardDecision
    suggestion_id: uuid.UUID | None = None


@dataclass(frozen=True)
class SubjectRank:
    subject_key: str
    best_rank: int
    best_similarity: float
    images: int


@dataclass(frozen=True)
class MatchResult:
    post: Post
    status: MatchStatus
    suggestion: Candidate | None
    candidates: list[Candidate]
    reasons: list[str]
    by_subject: list[SubjectRank]
    thresholds: Thresholds


def post_facts(post: Post) -> PostFacts:
    detail = post.subject_key_detail or {}
    return PostFacts(subject_key=post.subject_key, subject_reason=detail.get("reason"))


def image_facts(image: Image) -> ImageFacts:
    return ImageFacts(status=image.status, confidence=image.confidence, subject=image.subject,
                      subject_key=image.subject_key, flag_reasons=tuple(image.flag_reasons or ()))


def _ranked(session: Session, tenant_id: str, post: Post, model: str) -> list[tuple[Image, float]]:
    """Rank with `model` (the configured embedding model) or, if the post was embedded by another
    model only (e.g. replayed from a recording), with that one: vectors are only compared within a model."""
    models = embeddings_repo.models_for(session, tenant_id, "post", str(post.id)) if post.status == "embedded" else []
    if not models:
        raise Conflict(f"Post '{post.slug}' has not been embedded yet. "
                       'Run POST /jobs {"type": "process_posts"} and wait for it to finish.')
    return embeddings_repo.rank_images(session, tenant_id, model if model in models else models[0], post.id)


def _persist(session: Session, tenant_id: str, post: Post, c: Candidate, t: Thresholds) -> Candidate:
    suggestion_id = suggestions_repo.upsert(
        session, tenant_id, post_id=post.id, image_id=c.image.id, rank=c.rank, similarity=c.similarity,
        verdict=c.decision.verdict.lower(), reasons=c.decision.reasons, checks=c.decision.checks_as_dicts(),
        thresholds=t.public(), guard_version=t.guard_version)
    return Candidate(c.image, c.rank, c.similarity, c.decision, suggestion_id)


def _by_subject(evaluated: list[Candidate]) -> list[SubjectRank]:
    groups: OrderedDict[str, list[Candidate]] = OrderedDict()
    for c in evaluated:  # already in rank order, so the first one seen is each subject's best
        groups.setdefault(c.image.subject_key or "unknown", []).append(c)
    return [SubjectRank(key, items[0].rank, round(items[0].similarity, 4), len(items)) for key, items in groups.items()]


def ranked_images(session: Session, tenant_id: str, post: Post, model: str) -> list[tuple[Image, float]]:
    """Every embedded image, most similar first. Raises Conflict if the post isn't embedded yet."""
    return _ranked(session, tenant_id, post, model)


def evaluate_ranked(post: Post, ranked: list[tuple[Image, float]], thresholds: Thresholds) -> list[Candidate]:
    """Run the guard over an already-ranked list (pure: used by the API and by the eval sweep)."""
    pf = post_facts(post)
    return [Candidate(image, rank, similarity, guard.evaluate(pf, image_facts(image), similarity, thresholds))
            for rank, (image, similarity) in enumerate(ranked, start=1)]


def first_accepted(evaluated: list[Candidate]) -> Candidate | None:
    return next((c for c in evaluated if c.decision.verdict == "ACCEPTED"), None)


def match_post(session: Session, tenant_id: str, post: Post, *, limit: int, thresholds: Thresholds, model: str,
               persist: bool = True) -> MatchResult:
    pf = post_facts(post)
    evaluated = evaluate_ranked(post, _ranked(session, tenant_id, post, model), thresholds)
    accepted = first_accepted(evaluated)

    shown = evaluated[:limit]
    if accepted is not None and accepted.rank > limit:
        shown.append(accepted)  # the suggestion is always shown, even if it sits below the top `limit`
    if persist:
        shown = [_persist(session, tenant_id, post, c, thresholds) for c in shown]
    suggestion = next((c for c in shown if accepted is not None and c.image.id == accepted.image.id), None)

    reasons: list[str] = []
    if suggestion is None:
        reasons = guard.no_match_reasons(
            pf, [(image_facts(c.image), c.similarity, c.decision) for c in evaluated[:limit]], thresholds)
    return MatchResult(post=post, status="match" if suggestion else "no_confident_match", suggestion=suggestion,
                       candidates=shown, reasons=reasons, by_subject=_by_subject(evaluated), thresholds=thresholds)


def check_candidate(session: Session, tenant_id: str, post: Post, image_id: uuid.UUID, *, thresholds: Thresholds,
                    model: str, persist: bool = True) -> Candidate:
    """Force one image through the guard for a post (probe P3: the wolf on the fox post)."""
    ranked = _ranked(session, tenant_id, post, model)
    for rank, (image, similarity) in enumerate(ranked, start=1):
        if image.id == image_id:
            candidate = Candidate(image, rank, similarity,
                                  guard.evaluate(post_facts(post), image_facts(image), similarity, thresholds))
            return _persist(session, tenant_id, post, candidate, thresholds) if persist else candidate
    if images_repo.get(session, tenant_id, image_id) is None:
        raise NotFound(f"Image {image_id} not found")
    raise Conflict(f"Image {image_id} has not been processed yet (no embedding). "
                   'Run POST /jobs {"type": "process_images"} first.')
