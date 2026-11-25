"""HTTP-layer dependencies: DB session and the tenant scope for every request."""
import re
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_session
from app.domain.errors import BadRequest, NotFound
from app.repositories import tenants

TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")

SessionDep = Annotated[Session, Depends(get_session)]


def get_tenant_id(
    session: SessionDep,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> str:
    """Resolve the tenant from X-Tenant-Id (falls back to DEFAULT_TENANT). Every repository call is scoped by it."""
    tenant_id = (x_tenant_id or get_settings().default_tenant).strip().lower()
    if not TENANT_ID_PATTERN.match(tenant_id):
        raise BadRequest("X-Tenant-Id must be 2-40 chars: lowercase letters, digits, hyphens")
    if tenants.get(session, tenant_id) is None:
        raise NotFound(f"Tenant '{tenant_id}' not found")
    return tenant_id


TenantDep = Annotated[str, Depends(get_tenant_id)]
