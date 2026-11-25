import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Alert


def add(session: Session, tenant_id: str, *, level: str, message: str, job_id: uuid.UUID | None) -> Alert:
    alert = Alert(tenant_id=tenant_id, level=level, message=message[:2000], job_id=job_id)
    session.add(alert)
    session.flush()
    return alert


def list_(session: Session, tenant_id: str, *, job_id: uuid.UUID | None, limit: int, offset: int) -> Sequence[Alert]:
    query = select(Alert).where(Alert.tenant_id == tenant_id)
    if job_id:
        query = query.where(Alert.job_id == job_id)
    return session.scalars(query.order_by(Alert.created_at.desc()).limit(limit).offset(offset)).all()
