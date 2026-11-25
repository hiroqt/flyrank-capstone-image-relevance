from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import IdempotencyKey


def get(session: Session, tenant_id: str, key: str) -> IdempotencyKey | None:
    return session.scalar(select(IdempotencyKey).where(IdempotencyKey.tenant_id == tenant_id,
                                                       IdempotencyKey.key == key))


def save(session: Session, tenant_id: str, key: str, *, request_hash: str, status_code: int, response: dict) -> None:
    session.add(IdempotencyKey(tenant_id=tenant_id, key=key, request_hash=request_hash,
                               status_code=status_code, response=response))
    session.flush()
