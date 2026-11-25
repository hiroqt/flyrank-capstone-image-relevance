"""Image data access. Every function is tenant-scoped."""
import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import AICall, Image, ImageTag
from app.domain.schemas import ImageMetadata

DONE_STATUSES = ("tagged", "flagged")


def get(session: Session, tenant_id: str, image_id: uuid.UUID) -> Image | None:
    return session.scalar(select(Image).where(Image.tenant_id == tenant_id, Image.id == image_id))


def list_(session: Session, tenant_id: str, *, status: str | None = None, subject: str | None = None,
          limit: int = 50, offset: int = 0) -> tuple[Sequence[Image], int]:
    query = select(Image).where(Image.tenant_id == tenant_id)
    if status:
        query = query.where(Image.status == status)
    if subject:
        query = query.where(Image.subject.ilike(f"%{subject}%"))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(Image.created_at, Image.id).limit(limit).offset(offset)).all()
    return rows, total


def register(session: Session, tenant_id: str, *, sha256: str, file_path: str, source_url: str | None = None,
             license: str | None = None, photographer: str | None = None) -> bool:
    """Insert an image; returns False if this tenant already has the same bytes (sha256 dedupe)."""
    stmt = (insert(Image)
            .values(tenant_id=tenant_id, sha256=sha256, file_path=file_path, source_url=source_url,
                    license=license, photographer=photographer)
            .on_conflict_do_nothing(index_elements=[Image.tenant_id, Image.sha256])
            .returning(Image.id))
    return session.scalar(stmt) is not None


def ids_needing_vision(session: Session, tenant_id: str, *, model: str, prompt_version: str,
                       force: bool) -> list[uuid.UUID]:
    """Images a vision job should process. Without force, images already done by this model+prompt are skipped."""
    query = select(Image.id).where(Image.tenant_id == tenant_id)
    if not force:
        already_done = (Image.status.in_(DONE_STATUSES) & (Image.vision_model == model)
                        & (Image.prompt_version == prompt_version))
        query = query.where(~already_done)
    return list(session.scalars(query.order_by(Image.created_at, Image.id)))


def save_metadata(session: Session, image: Image, meta: ImageMetadata, raw_text: str, *, status: str,
                  flag_reasons: list[str], model: str, prompt_version: str, job_id: uuid.UUID | None) -> None:
    image.subject = meta.subject
    image.category = meta.category.value
    image.caption = meta.caption
    image.confidence = meta.confidence
    image.status = status
    image.flag_reasons = flag_reasons
    image.vision_model = model
    image.prompt_version = prompt_version
    image.raw_response = {"text": raw_text}
    image.last_job_id = job_id
    replace_tags(session, image, meta)


def replace_tags(session: Session, image: Image, meta: ImageMetadata) -> None:
    session.execute(delete(ImageTag).where(ImageTag.image_id == image.id))
    tags = {("subject", meta.subject.lower()), ("category", meta.category.value)}
    tags |= {("attribute", a.strip().lower()) for a in meta.attributes if a.strip()}
    session.add_all(
        ImageTag(image_id=image.id, tenant_id=image.tenant_id, kind=kind, tag=tag[:80]) for kind, tag in sorted(tags)
    )


def mark_failed(session: Session, tenant_id: str, image_id: uuid.UUID, reason: str,
                job_id: uuid.UUID | None) -> None:
    image = get(session, tenant_id, image_id)
    if image is None:
        return
    image.status = "failed"
    image.flag_reasons = [reason]
    image.last_job_id = job_id


def tags_for(session: Session, image_id: uuid.UUID) -> Sequence[ImageTag]:
    return session.scalars(select(ImageTag).where(ImageTag.image_id == image_id)
                           .order_by(ImageTag.kind, ImageTag.tag)).all()


def ai_calls_for(session: Session, tenant_id: str, image_id: uuid.UUID) -> Sequence[AICall]:
    return session.scalars(select(AICall).where(AICall.tenant_id == tenant_id, AICall.entity_type == "image",
                                                AICall.entity_id == str(image_id)).order_by(AICall.id)).all()
