import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep, TenantDep
from app.api.schemas import AlertOut
from app.repositories import alerts as alerts_repo

router = APIRouter(prefix="/alerts", tags=["ops"])


@router.get("", response_model=list[AlertOut], summary="Background-job failure alerts")
def list_alerts(session: SessionDep, tenant_id: TenantDep, job_id: uuid.UUID | None = None,
                limit: Annotated[int, Query(ge=1, le=200)] = 50,
                offset: Annotated[int, Query(ge=0)] = 0) -> list[AlertOut]:
    return [AlertOut.model_validate(a) for a in alerts_repo.list_(session, tenant_id, job_id=job_id,
                                                                  limit=limit, offset=offset)]
