import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from app.api.deps import SessionDep, TenantDep
from app.api.schemas import JobCreate, JobOut, JobStatus
from app.config import get_settings
from app.domain.errors import NotFound
from app.repositories import jobs as jobs_repo
from app.services import idempotency
from app.services.jobs import enqueue

router = APIRouter(prefix="/jobs", tags=["jobs"])

IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$"),
]


@router.post("", status_code=202, response_model=JobOut,
             summary="Enqueue a batch job (returns immediately; the worker does the AI calls)")
def create_job(body: JobCreate, session: SessionDep, tenant_id: TenantDep,
               idempotency_key: IdempotencyKey = None) -> JSONResponse:
    settings = get_settings()

    def action() -> tuple[int, dict]:
        job = enqueue(session, tenant_id, body.type, force=body.force, settings=settings)
        return 202, JobOut.model_validate(job).model_dump(mode="json")

    status, payload, replayed = idempotency.run_once(session, tenant_id, idempotency_key,
                                                     body.model_dump(mode="json"), action)
    headers = {"Idempotency-Replayed": "true"} if replayed else None
    return JSONResponse(status_code=status, content=payload, headers=headers)


@router.get("/{job_id}", response_model=JobOut, summary="Job progress and counts")
def get_job(job_id: uuid.UUID, session: SessionDep, tenant_id: TenantDep) -> JobOut:
    job = jobs_repo.get(session, tenant_id, job_id)
    if job is None:
        raise NotFound(f"Job {job_id} not found")
    return JobOut.model_validate(job)


@router.get("", response_model=list[JobOut], summary="Recent jobs")
def list_jobs(session: SessionDep, tenant_id: TenantDep, status: JobStatus | None = None,
              limit: Annotated[int, Query(ge=1, le=200)] = 20,
              offset: Annotated[int, Query(ge=0)] = 0) -> list[JobOut]:
    return [JobOut.model_validate(j) for j in jobs_repo.list_(session, tenant_id, status=status,
                                                              limit=limit, offset=offset)]
