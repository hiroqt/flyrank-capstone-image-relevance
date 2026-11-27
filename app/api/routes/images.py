import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep, TenantDep
from app.api.schemas import AICallOut, ImageDetail, ImageOut, ImageStatus, Page, TagOut
from app.domain.errors import NotFound
from app.repositories import images as images_repo

router = APIRouter(prefix="/images", tags=["images"])


@router.get("", response_model=Page[ImageOut], summary="Image table (filter status=flagged for review)")
def list_images(session: SessionDep, tenant_id: TenantDep, status: ImageStatus | None = None,
                subject: Annotated[str | None, Query(max_length=60, description="substring of the subject")] = None,
                subject_key: Annotated[str | None, Query(max_length=40, description="e.g. fox, wolf, unknown")] = None,
                limit: Annotated[int, Query(ge=1, le=200)] = 50,
                offset: Annotated[int, Query(ge=0)] = 0) -> Page[ImageOut]:
    rows, total = images_repo.list_(session, tenant_id, status=status, subject=subject, subject_key=subject_key,
                                    limit=limit, offset=offset)
    return Page[ImageOut](items=[ImageOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{image_id}", response_model=ImageDetail,
            summary="One image: validated metadata, tags, flag reasons and every AI call made for it")
def get_image(image_id: uuid.UUID, session: SessionDep, tenant_id: TenantDep) -> ImageDetail:
    image = images_repo.get(session, tenant_id, image_id)
    if image is None:
        raise NotFound(f"Image {image_id} not found")
    base = ImageOut.model_validate(image).model_dump()
    return ImageDetail(
        **base,
        source_url=image.source_url, license=image.license, photographer=image.photographer,
        last_job_id=image.last_job_id,
        tags=[TagOut.model_validate(t) for t in images_repo.tags_for(session, image.id)],
        ai_calls=[AICallOut.model_validate(c) for c in images_repo.ai_calls_for(session, tenant_id, image.id)],
    )
