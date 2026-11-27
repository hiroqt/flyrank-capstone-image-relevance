"""Request/response models for the HTTP layer. Requests forbid unknown fields."""
import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field

T = TypeVar("T")

ImageStatus = Literal["pending", "processing", "tagged", "flagged", "failed"]
JobStatus = Literal["queued", "running", "succeeded", "partial", "failed"]
Operation = Literal["vision", "text", "embed"]


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["process_images", "process_posts"] = Field(
        description="process_images: vision + embeddings for images; process_posts: embeddings for posts")
    force: bool = Field(default=False, description="Re-run the AI step even if the item is already done")


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    status: JobStatus
    force: bool
    total: int
    processed: int
    succeeded: int
    flagged: int
    failed: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @computed_field
    @property
    def progress(self) -> float:
        return round(self.processed / self.total, 4) if self.total else 1.0


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    tag: str


class AICallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: uuid.UUID | None
    job_item_id: int | None
    provider: str
    model: str
    operation: str
    entity_type: str
    entity_id: str
    attempt: int
    status: str
    input_tokens: int
    output_tokens: int
    tokens_estimated: bool
    cost_usd: float
    tier: str
    latency_ms: int
    error: str | None
    created_at: datetime


class ImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_path: str
    status: ImageStatus
    subject: str | None
    subject_key: str | None = Field(description="The taxonomy class the subject resolved to, or 'unknown'")
    category: str | None
    caption: str | None
    confidence: float | None
    flag_reasons: list[str]
    vision_model: str | None
    prompt_version: str | None
    updated_at: datetime


class ImageDetail(ImageOut):
    source_url: str | None
    license: str | None
    photographer: str | None
    last_job_id: uuid.UUID | None
    tags: list[TagOut]
    ai_calls: list[AICallOut]


class CostTotal(BaseModel):
    provider: str
    model: str
    operation: str
    status: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class CostReport(BaseModel):
    note: str = ("cost_usd is the list-price equivalent from config/pricing.yaml. Calls with tier=free "
                 "are billed $0 (Gemini free tier).")
    budget_usd: float
    spent_usd: float
    remaining_usd: float
    totals: list[CostTotal]
    calls: Page[AICallOut]


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID | None
    level: str
    message: str
    created_at: datetime


# ---- posts + matching (Phase 3) -----------------------------------------------------------------
PostStatus = Literal["pending", "embedded", "failed"]
Verdict = Literal["ACCEPTED", "REJECTED"]


class PostCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=20, max_length=20_000)


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    status: PostStatus
    subject_key: str | None
    subject_key_score: float | None
    subject_key_detail: dict[str, Any] | None
    topic: dict[str, Any] | None = Field(description="What the post is about, read by a structured text call")
    updated_at: datetime


class PostDetail(PostOut):
    body: str


class PostCreated(PostDetail):
    job_id: uuid.UUID = Field(description="The process_posts job that embeds this post")


class CandidateOut(BaseModel):
    suggestion_id: uuid.UUID | None
    image_id: uuid.UUID
    file_path: str
    rank: int
    similarity: float
    subject: str | None
    subject_key: str | None
    image_status: str
    confidence: float | None
    verdict: Verdict
    reasons: list[str]


class SubjectRankOut(BaseModel):
    subject_key: str
    best_rank: int
    best_similarity: float
    images: int


class PostImagesOut(BaseModel):
    post_id: uuid.UUID
    slug: str
    title: str
    post_subject: str | None
    status: Literal["match", "no_confident_match"]
    suggestion: CandidateOut | None
    reasons: list[str] = Field(description="Why there is no confident match (empty when status=match)")
    candidates: list[CandidateOut]
    by_subject: list[SubjectRankOut] = Field(description="Best rank and similarity per detected subject")
    thresholds: dict[str, Any]


class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_id: uuid.UUID


class GuardCheckOut(BaseModel):
    rule: str
    passed: bool
    observed: Any
    threshold: Any
    reason: str | None


class CandidateDecisionOut(CandidateOut):
    post_id: uuid.UUID
    slug: str
    post_subject: str | None
    checks: list[GuardCheckOut]
    thresholds: dict[str, Any]


# ---- review (Phase 4) ---------------------------------------------------------------------------
ReviewState = Literal["pending", "approved", "rejected"]


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)
    reviewer: str | None = Field(default=None, max_length=120)


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    decision: Literal["approved", "rejected"]
    note: str | None
    reviewer: str | None
    created_at: datetime


class SuggestionOut(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    post_slug: str
    image_id: uuid.UUID
    file_path: str
    rank: int
    similarity: float
    verdict: Verdict
    reasons: list[str]
    guard_version: str
    review: ReviewOut | None
    updated_at: datetime


class SuggestionExplain(SuggestionOut):
    post_subject: str | None
    image_subject: str | None
    image_subject_key: str | None
    image_confidence: float | None
    image_status: str
    checks: list[GuardCheckOut]
    thresholds: dict[str, Any]
    summary: str = Field(description="One-sentence answer to 'why was this image selected or refused?'")


class ReviewDecisionOut(BaseModel):
    suggestion_id: uuid.UUID
    verdict: Verdict = Field(description="What the guard decided")
    review: ReviewOut
    overrides_guard: bool = Field(description="True when the human decision differs from the guard's verdict")
