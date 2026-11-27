"""Cost tracking + budget guard around every AI call (brief section 6 AI-4, shared requirement #7, probe P6).

Every provider attempt becomes exactly one ai_calls row, whatever the outcome:
ok | invalid_output | error | rate_limited | budget_blocked.
Rows are written in their own short transaction, so a cost entry survives even when the job
item that triggered it rolls back.
"""
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AICall
from app.jobs.retry import RateLimiter
from app.providers.base import (
    EmbeddingProvider,
    ProviderError,
    RateLimitedError,
    Usage,
    VisionProvider,
    VisionResponse,
)
from app.providers.pricing import PriceBook

log = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

# Conservative per-call estimates, used only for the pre-call budget check.
VISION_ESTIMATE = Usage(input_tokens=1_500, output_tokens=500, estimated=True)
TEXT_ESTIMATE = Usage(input_tokens=800, output_tokens=200, estimated=True)


@dataclass(frozen=True)
class CallContext:
    """Who a call is attributed to."""

    tenant_id: str
    entity_type: str  # image | post | label
    entity_id: str
    job_id: uuid.UUID | None = None
    job_item_id: int | None = None


class BudgetExceeded(Exception):
    pass


class InvalidOutput(Exception):
    """The model answered, but the answer failed schema validation. Never trusted."""

    def __init__(self, raw_text: str, error: str):
        super().__init__(error)
        self.raw_text = raw_text
        self.error = error


def summarize_validation_error(exc: ValidationError, limit: int = 5) -> str:
    parts = []
    for err in exc.errors()[:limit]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts)


class CostLedger:
    def __init__(self, session_factory: Callable[[], Session], prices: PriceBook, budget_usd: float):
        self._session_factory = session_factory
        self._prices = prices
        self.budget_usd = Decimal(str(budget_usd))

    def spent(self, tenant_id: str) -> Decimal:
        with self._session_factory() as session:
            total = session.scalar(select(func.coalesce(func.sum(AICall.cost_usd), 0))
                                   .where(AICall.tenant_id == tenant_id))
        return Decimal(total)

    def ensure_budget(self, ctx: CallContext, provider: str, model: str, operation: str, estimate: Usage) -> None:
        projected = self.spent(ctx.tenant_id) + self._prices.price(provider, model).cost(estimate)
        if projected > self.budget_usd:
            message = (f"AI budget exceeded for tenant '{ctx.tenant_id}': projected ${projected} "
                       f"> AI_BUDGET_USD ${self.budget_usd}")
            self.record(ctx, provider, model, operation, "budget_blocked", Usage(), 0, message, cost_override=Decimal(0))
            raise BudgetExceeded(message)

    def record(self, ctx: CallContext, provider: str, model: str, operation: str, status: str,
               usage: Usage, latency_ms: int, error: str | None = None,
               cost_override: Decimal | None = None) -> int:
        price = self._prices.price(provider, model)
        cost = price.cost(usage) if cost_override is None else cost_override
        with self._session_factory() as session, session.begin():
            attempt = 1
            if ctx.job_item_id is not None:
                attempt += session.scalar(
                    select(func.count()).select_from(AICall)
                    .where(AICall.job_item_id == ctx.job_item_id, AICall.operation == operation)
                ) or 0
            row = AICall(
                tenant_id=ctx.tenant_id, job_id=ctx.job_id, job_item_id=ctx.job_item_id,
                provider=provider, model=model, operation=operation,
                entity_type=ctx.entity_type, entity_id=ctx.entity_id, attempt=attempt, status=status,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                tokens_estimated=usage.estimated, cost_usd=cost, tier=price.tier,
                latency_ms=latency_ms, error=(error or None) and error[:1000],
            )
            session.add(row)
            session.flush()
            log.info("ai_call %s %s/%s %s entity=%s:%s status=%s tokens=%d/%d cost=$%.6f (%s) latency=%dms",
                     row.id, provider, model, operation, ctx.entity_type, ctx.entity_id, status,
                     usage.input_tokens, usage.output_tokens, cost, price.tier, latency_ms)
            return row.id


class TrackedVision:
    def __init__(self, inner: VisionProvider, ledger: CostLedger, limiter: RateLimiter | None = None):
        self.inner = inner
        self.ledger = ledger
        self.limiter = limiter or RateLimiter(0)

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    def describe_validated(self, ctx: CallContext, image: bytes, mime_type: str, prompt: str,
                           output_model: type[ModelT], json_schema: dict) -> tuple[ModelT, str]:
        """One vision call: budget check, rate limit, call, validate, and exactly one cost row."""
        return self._validated(ctx, "vision", VISION_ESTIMATE, output_model,
                               lambda: self.inner.describe(image, mime_type, prompt, json_schema))

    def generate_validated(self, ctx: CallContext, prompt: str, output_model: type[ModelT],
                           json_schema: dict) -> tuple[ModelT, str]:
        """One text-only structured call (what is this post about?), tracked exactly like vision."""
        return self._validated(ctx, "text", TEXT_ESTIMATE, output_model,
                               lambda: self.inner.generate(prompt, json_schema))

    def _validated(self, ctx: CallContext, operation: str, estimate: Usage, output_model: type[ModelT],
                   call: Callable[[], "VisionResponse"]) -> tuple[ModelT, str]:
        self.ledger.ensure_budget(ctx, self.provider, self.model, operation, estimate)
        self.limiter.acquire()
        started = time.monotonic()
        try:
            response = call()
        except ProviderError as exc:
            status = "rate_limited" if isinstance(exc, RateLimitedError) else "error"
            self.ledger.record(ctx, self.provider, self.model, operation, status, Usage(), _ms(started), str(exc))
            raise
        latency = _ms(started)

        try:
            parsed = output_model.model_validate_json(response.text)
        except ValidationError as exc:
            summary = summarize_validation_error(exc)
            self.ledger.record(ctx, self.provider, self.model, operation, "invalid_output",
                               response.usage, latency, summary)
            raise InvalidOutput(response.text, summary) from exc

        self.ledger.record(ctx, self.provider, self.model, operation, "ok", response.usage, latency)
        return parsed, response.text


class TrackedEmbedder:
    def __init__(self, inner: EmbeddingProvider, ledger: CostLedger, limiter: RateLimiter | None = None):
        self.inner = inner
        self.ledger = ledger
        self.limiter = limiter or RateLimiter(0)

    @property
    def dimensions(self) -> int | None:
        """The configured vector size, when the provider has one (Gemini: EMBED_DIMENSIONS)."""
        return getattr(self.inner, "dimensions", None)

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    def embed(self, ctx: CallContext, texts: list[str]) -> list[list[float]]:
        estimate = Usage(input_tokens=sum(max(1, len(t) // 4) for t in texts), estimated=True)
        self.ledger.ensure_budget(ctx, self.provider, self.model, "embed", estimate)
        self.limiter.acquire()
        started = time.monotonic()
        try:
            response = self.inner.embed(texts)
        except ProviderError as exc:
            status = "rate_limited" if isinstance(exc, RateLimitedError) else "error"
            self.ledger.record(ctx, self.provider, self.model, "embed", status, Usage(), _ms(started), str(exc))
            raise
        self.ledger.record(ctx, self.provider, self.model, "embed", "ok", response.usage, _ms(started))
        return response.vectors


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
