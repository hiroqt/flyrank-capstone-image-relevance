from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy.exc import IntegrityError

from app.api.deps import SessionDep, TenantDep
from app.api.schemas import (
    CandidateDecisionOut,
    CandidateOut,
    CandidateRequest,
    GuardCheckOut,
    Page,
    PostCreate,
    PostCreated,
    PostDetail,
    PostImagesOut,
    PostOut,
    PostStatus,
    SubjectRankOut,
)
from app.config import get_settings
from app.db.models import Post
from app.domain.errors import Conflict, NotFound
from app.repositories import posts as posts_repo
from app.services import matching
from app.services.jobs import embed_model_name, enqueue_process_posts
from app.services.thresholds import current_thresholds

router = APIRouter(prefix="/posts", tags=["posts & matching"])

PostRef = Annotated[str, Path(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9-]+$",
                              description="Post UUID or slug, e.g. the-behavior-of-red-foxes")]


def _post_or_404(session, tenant_id: str, ref: str) -> Post:
    post = posts_repo.get_by_ref(session, tenant_id, ref)
    if post is None:
        raise NotFound(f"Post '{ref}' not found")
    return post


def candidate_out(c: matching.Candidate) -> CandidateOut:
    return CandidateOut(
        suggestion_id=c.suggestion_id, image_id=c.image.id, file_path=c.image.file_path, rank=c.rank,
        similarity=round(c.similarity, 4), subject=c.image.subject, subject_key=c.image.subject_key,
        image_status=c.image.status, confidence=c.image.confidence, verdict=c.decision.verdict,
        reasons=c.decision.reasons,
    )


@router.get("", response_model=Page[PostOut], summary="Posts and what each one is about (subject_key)")
def list_posts(session: SessionDep, tenant_id: TenantDep, status: PostStatus | None = None,
               subject_key: Annotated[str | None, Query(max_length=40)] = None,
               limit: Annotated[int, Query(ge=1, le=200)] = 50,
               offset: Annotated[int, Query(ge=0)] = 0) -> Page[PostOut]:
    rows, total = posts_repo.list_(session, tenant_id, status=status, subject_key=subject_key,
                                   limit=limit, offset=offset)
    return Page[PostOut](items=[PostOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.post("", status_code=201, response_model=PostCreated,
             summary="Create a post; its embedding is computed by a background job (returned as job_id)")
def create_post(body: PostCreate, session: SessionDep, tenant_id: TenantDep) -> PostCreated:
    try:
        with session.begin_nested():
            post = posts_repo.create(session, tenant_id, slug=body.slug, title=body.title, body=body.body)
    except IntegrityError as exc:
        raise Conflict(f"A post with slug '{body.slug}' already exists") from exc
    job = enqueue_process_posts(session, tenant_id, force=False, settings=get_settings(), post_ids=[post.id])
    session.commit()
    return PostCreated(**PostDetail.model_validate(post).model_dump(), job_id=job.id)


@router.get("/{post_ref}", response_model=PostDetail, summary="One post")
def get_post(post_ref: PostRef, session: SessionDep, tenant_id: TenantDep) -> PostDetail:
    return PostDetail.model_validate(_post_or_404(session, tenant_id, post_ref))


@router.get("/{post_ref}/images", response_model=PostImagesOut,
            summary="Ranked, guarded image suggestions for a post (or 'no_confident_match' with reasons)")
def post_images(post_ref: PostRef, session: SessionDep, tenant_id: TenantDep,
                limit: Annotated[int, Query(ge=1, le=50, description="How many ranked candidates to show")] = 5
                ) -> PostImagesOut:
    settings = get_settings()
    post = _post_or_404(session, tenant_id, post_ref)
    result = matching.match_post(session, tenant_id, post, limit=limit, thresholds=current_thresholds(settings),
                                 model=embed_model_name(settings))
    session.commit()  # suggestions are upserted so they can be reviewed (idempotent: same ids on repeat)
    return PostImagesOut(
        post_id=post.id, slug=post.slug, title=post.title, post_subject=post.subject_key, status=result.status,
        suggestion=candidate_out(result.suggestion) if result.suggestion else None, reasons=result.reasons,
        candidates=[candidate_out(c) for c in result.candidates],
        by_subject=[SubjectRankOut(**vars(s)) for s in result.by_subject],
        thresholds=result.thresholds.public(),
    )


@router.post("/{post_ref}/candidates", response_model=CandidateDecisionOut,
             summary="Force one image through the guard for this post and explain the decision")
def force_candidate(post_ref: PostRef, body: CandidateRequest, session: SessionDep,
                    tenant_id: TenantDep) -> CandidateDecisionOut:
    settings = get_settings()
    post = _post_or_404(session, tenant_id, post_ref)
    thresholds = current_thresholds(settings)
    c = matching.check_candidate(session, tenant_id, post, body.image_id, thresholds=thresholds,
                                 model=embed_model_name(settings))
    session.commit()
    return CandidateDecisionOut(
        **candidate_out(c).model_dump(), post_id=post.id, slug=post.slug, post_subject=post.subject_key,
        checks=[GuardCheckOut(**d) for d in c.decision.checks_as_dicts()], thresholds=thresholds.public(),
    )
