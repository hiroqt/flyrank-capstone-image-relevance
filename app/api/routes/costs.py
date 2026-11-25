import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep, TenantDep
from app.api.schemas import AICallOut, CostReport, CostTotal, Operation, Page
from app.config import get_settings
from app.repositories import ai_calls as ai_calls_repo

router = APIRouter(prefix="/costs", tags=["costs"])


@router.get("", response_model=CostReport, summary="Per-call AI cost log with totals and budget")
def costs(session: SessionDep, tenant_id: TenantDep, job_id: uuid.UUID | None = None,
          operation: Operation | None = None,
          limit: Annotated[int, Query(ge=1, le=500)] = 100,
          offset: Annotated[int, Query(ge=0)] = 0) -> CostReport:
    totals = ai_calls_repo.totals(session, tenant_id, job_id=job_id, operation=operation)
    all_totals = totals if job_id is None and operation is None else \
        ai_calls_repo.totals(session, tenant_id, job_id=None, operation=None)
    spent = float(sum(t["cost_usd"] or 0 for t in all_totals))
    budget = get_settings().ai_budget_usd
    calls = ai_calls_repo.list_(session, tenant_id, job_id=job_id, operation=operation, limit=limit, offset=offset)
    total_calls = sum(t["calls"] for t in totals)
    return CostReport(
        budget_usd=budget, spent_usd=round(spent, 8), remaining_usd=round(budget - spent, 8),
        totals=[CostTotal(**{**t, "cost_usd": float(t["cost_usd"] or 0)}) for t in totals],
        calls=Page[AICallOut](items=[AICallOut.model_validate(c) for c in calls], total=total_calls,
                              limit=limit, offset=offset),
    )
