"""Tenant lookups and seeding."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Tenant


def get(session: Session, tenant_id: str) -> Tenant | None:
    return session.scalar(select(Tenant).where(Tenant.id == tenant_id))


def upsert(session: Session, tenant_id: str, name: str) -> None:
    stmt = insert(Tenant).values(id=tenant_id, name=name)
    session.execute(stmt.on_conflict_do_update(index_elements=[Tenant.id], set_={"name": name}))
