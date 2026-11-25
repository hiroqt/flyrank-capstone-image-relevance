"""Enqueueing background jobs. Requests only create rows; the worker does the slow AI work."""
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Job
from app.repositories import images as images_repo
from app.repositories import jobs as jobs_repo
from app.services.pipeline import PROMPT_VERSION


def enqueue_process_images(session: Session, tenant_id: str, *, force: bool, vision_model: str,
                           settings: Settings) -> Job:
    image_ids = images_repo.ids_needing_vision(session, tenant_id, model=vision_model,
                                               prompt_version=PROMPT_VERSION, force=force)
    return jobs_repo.create(session, tenant_id, job_type="process_images", force=force, item_type="image",
                            item_ids=image_ids, max_attempts=settings.job_max_attempts)


def vision_model_name(settings: Settings) -> str:
    return {
        "gemini": settings.gemini_vision_model,
        "ollama": settings.ollama_vision_model,
        "fake": "fake-vision-1",
    }[settings.ai_provider]
