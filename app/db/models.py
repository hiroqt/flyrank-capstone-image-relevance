"""Database models (brief section 6, Backend box 1; PLAN.md section 3).

Every table carries tenant_id and its indexes lead with it (shared requirement #4: isolated tenants).
Status-like columns are VARCHAR + CHECK constraints: easy to read, easy to migrate.
The schema itself is created by Alembic migrations, never by create_all().
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


IMAGE_STATUSES = ("pending", "processing", "tagged", "flagged", "failed")
TAG_KINDS = ("subject", "category", "attribute")
POST_STATUSES = ("pending", "embedded", "failed")
OWNER_TYPES = ("image", "post", "label")
VERDICTS = ("accepted", "rejected")
DECISIONS = ("approved", "rejected")
JOB_TYPES = ("process_images", "process_posts")
JOB_STATUSES = ("queued", "running", "succeeded", "partial", "failed")
ITEM_TYPES = ("image", "post")
ITEM_STATUSES = ("queued", "running", "succeeded", "flagged", "failed")
AI_OPERATIONS = ("vision", "embed")
AI_CALL_STATUSES = ("ok", "invalid_output", "error", "rate_limited", "budget_blocked")
AI_TIERS = ("free", "paid", "local")
ALERT_LEVELS = ("warning", "error")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _tenant_fk() -> Mapped[str]:
    return mapped_column(String(40), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint(r"id ~ '^[a-z0-9][a-z0-9-]{1,39}$'", name="id_format"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Image(Base):
    __tablename__ = "images"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sha256"),
        Index(None, "tenant_id", "status"),
        Index(None, "tenant_id", "subject_key"),
        CheckConstraint(_in("status", IMAGE_STATUSES), name="status"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = _tenant_fk()
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(Text)
    photographer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    subject: Mapped[str | None] = mapped_column(Text)
    subject_key: Mapped[str | None] = mapped_column(String(40))
    subject_key_score: Mapped[float | None] = mapped_column(Float)
    category: Mapped[str | None] = mapped_column(String(20))
    caption: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    flag_reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    vision_model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The job that last wrote this image's metadata: makes a crash-retried job item idempotent.
    last_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class ImageTag(Base):
    __tablename__ = "image_tags"
    __table_args__ = (
        Index(None, "tenant_id", "tag"),
        CheckConstraint(_in("kind", TAG_KINDS), name="kind"),
    )

    image_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    tag: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = _tenant_fk()


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug"),
        CheckConstraint(_in("status", POST_STATUSES), name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = _tenant_fk()
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    subject_key: Mapped[str | None] = mapped_column(String(40))
    subject_key_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Embedding(Base):
    """Polymorphic: owner_type + owner_id. Images/posts use their UUID, taxonomy labels use their key."""

    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_type", "owner_id", "model"),
        Index(None, "tenant_id", "owner_type", "model"),
        CheckConstraint(_in("owner_type", OWNER_TYPES), name="owner_type"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = _tenant_fk()
    owner_type: Mapped[str] = mapped_column(String(10), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # No fixed dimension: provider/model can change. Exact search is fine at ~50 rows (no ANN index).
    vector: Mapped[Any] = mapped_column(Vector(), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Suggestion(Base):
    __tablename__ = "suggestions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "post_id", "image_id", "guard_version"),
        Index(None, "tenant_id", "post_id", "rank"),
        CheckConstraint(_in("verdict", VERDICTS), name="verdict"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = _tenant_fk()
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    image_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    verdict: Mapped[str] = mapped_column(String(10), nullable=False)
    reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    checks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    guard_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Review(Base):
    """Human approval/rejection of a suggestion. One decision per suggestion."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("suggestion_id"),
        Index(None, "tenant_id", "decision"),
        CheckConstraint(_in("decision", DECISIONS), name="decision"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = _tenant_fk()
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suggestions.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = _created_at()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index(None, "tenant_id", "status"),
        CheckConstraint(_in("type", JOB_TYPES), name="type"),
        CheckConstraint(_in("status", JOB_STATUSES), name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = _tenant_fk()
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    force: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    processed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    succeeded: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    flagged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobItem(Base):
    __tablename__ = "job_items"
    __table_args__ = (
        UniqueConstraint("job_id", "item_id"),
        Index(None, "status", "next_run_at"),  # the worker's claim query
        CheckConstraint(_in("item_type", ITEM_TYPES), name="item_type"),
        CheckConstraint(_in("status", ITEM_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = _tenant_fk()
    item_type: Mapped[str] = mapped_column(String(10), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class AICall(Base):
    """The cost log: one row per provider attempt, whatever the outcome (brief section 6, AI box 4; probe P6)."""

    __tablename__ = "ai_calls"
    __table_args__ = (
        Index(None, "tenant_id", "created_at"),
        Index(None, "job_id"),
        Index(None, "entity_type", "entity_id"),
        CheckConstraint(_in("operation", AI_OPERATIONS), name="operation"),
        CheckConstraint(_in("status", AI_CALL_STATUSES), name="status"),
        CheckConstraint(_in("tier", AI_TIERS), name="tier"),
        CheckConstraint("cost_usd >= 0", name="cost_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = _tenant_fk()
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    job_item_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("job_items.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(10), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False, server_default="0")
    tier: Mapped[str] = mapped_column(String(10), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index(None, "tenant_id", "created_at"),
        CheckConstraint(_in("level", ALERT_LEVELS), name="level"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = _tenant_fk()
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class IdempotencyKey(Base):
    """Stored response for a POST carrying an Idempotency-Key, so a retried request happens once."""

    __tablename__ = "idempotency_keys"

    tenant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()
