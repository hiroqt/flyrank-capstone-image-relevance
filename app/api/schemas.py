"""Request/response models for the HTTP layer. Requests forbid unknown fields."""
import uuid
from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field

T = TypeVar("T")

ImageStatus = Literal["pending", "processing", "tagged", "flagged", "failed"]
JobStatus = Literal["queued", "running", "succeeded", "partial", "failed"]
Operation = Literal["vision", "embed"]


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["process_images"] = Field(description="process_posts arrives with the matching engine (Phase 3)")
    force: bool = Field(default=False, description="Re-process images already tagged by the current model + prompt")


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
