"""Cost-log queries (probe P6)."""
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AICall


def _filtered(tenant_id: str, job_id: uuid.UUID | None, operation: str | None):
    conditions = [AICall.tenant_id == tenant_id]
    if job_id:
        conditions.append(AICall.job_id == job_id)
    if operation:
        conditions.append(AICall.operation == operation)
    return conditions


def list_(session: Session, tenant_id: str, *, job_id: uuid.UUID | None, operation: str | None,
          limit: int, offset: int) -> Sequence[AICall]:
    return session.scalars(select(AICall).where(*_filtered(tenant_id, job_id, operation))
                           .order_by(AICall.id).limit(limit).offset(offset)).all()


def totals(session: Session, tenant_id: str, *, job_id: uuid.UUID | None, operation: str | None) -> list[dict]:
    rows = session.execute(
        select(AICall.provider, AICall.model, AICall.operation, AICall.status,
               func.count().label("calls"),
               func.sum(AICall.input_tokens).label("input_tokens"),
               func.sum(AICall.output_tokens).label("output_tokens"),
               func.sum(AICall.cost_usd).label("cost_usd"))
        .where(*_filtered(tenant_id, job_id, operation))
        .group_by(AICall.provider, AICall.model, AICall.operation, AICall.status)
        .order_by(AICall.operation, AICall.model, AICall.status)
    ).mappings().all()
    return [dict(r) for r in rows]
