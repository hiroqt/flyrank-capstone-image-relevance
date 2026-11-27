"""Enqueueing background jobs. Requests only create rows; the worker does the slow AI work."""
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Job
from app.repositories import embeddings as embeddings_repo
from app.repositories import images as images_repo
from app.repositories import jobs as jobs_repo
from app.repositories import posts as posts_repo
from app.services.pipeline import PROMPT_VERSION
from app.services.taxonomy import current_taxonomy, resolver_version
from app.services.thresholds import current_thresholds

JOB_TYPES = ("process_images", "process_posts")


def current_resolver_version(settings: Settings) -> str:
    t = current_thresholds(settings)
    return resolver_version(current_taxonomy(settings), t.label_min, t.label_margin)


def enqueue_process_images(session: Session, tenant_id: str, *, force: bool, vision_model: str,
                           settings: Settings, resolver: str | None = None) -> Job:
    """Images that need vision (new, failed, older model/prompt, or force), an embedding, or a re-resolved
    subject. Only the missing steps run: a re-resolve after a taxonomy change makes no AI call."""
    needs_vision = images_repo.ids_needing_vision(session, tenant_id, model=vision_model,
                                                  prompt_version=PROMPT_VERSION, force=force)
    needs_vector = embeddings_repo.image_ids_needing_subject(session, tenant_id, embed_model_name(settings),
                                                             resolver or current_resolver_version(settings),
                                                             dims=embed_dimensions(settings))
    image_ids = list(dict.fromkeys([*needs_vision, *needs_vector]))
    return jobs_repo.create(session, tenant_id, job_type="process_images", force=force, item_type="image",
                            item_ids=image_ids, max_attempts=settings.job_max_attempts)


def enqueue_process_posts(session: Session, tenant_id: str, *, force: bool, settings: Settings,
                          post_ids: Sequence[uuid.UUID] | None = None, resolver: str | None = None) -> Job:
    ids = posts_repo.ids_needing_embedding(session, tenant_id, embed_model_name(settings), force=force,
                                           resolver=resolver or current_resolver_version(settings),
                                           post_ids=post_ids, dims=embed_dimensions(settings))
    return jobs_repo.create(session, tenant_id, job_type="process_posts", force=force, item_type="post",
                            item_ids=ids, max_attempts=settings.job_max_attempts)


def enqueue(session: Session, tenant_id: str, job_type: str, *, force: bool, settings: Settings) -> Job:
    if job_type == "process_posts":
        return enqueue_process_posts(session, tenant_id, force=force, settings=settings)
    return enqueue_process_images(session, tenant_id, force=force, vision_model=vision_model_name(settings),
                                  settings=settings)


def vision_model_name(settings: Settings) -> str:
    return {
        "gemini": settings.gemini_vision_model,
        "ollama": settings.ollama_vision_model,
        "fake": "fake-vision-1",
    }[settings.ai_provider]


def embed_model_name(settings: Settings) -> str:
    return {
        "gemini": settings.gemini_embed_model,
        "ollama": settings.ollama_embed_model,
        "fake": "fake-embed-1",
    }[settings.ai_provider]


def embed_dimensions(settings: Settings) -> int | None:
    """The configured vector size, when the provider takes one (Gemini). Ollama/fake sizes are fixed by the model."""
    return settings.embed_dimensions if settings.ai_provider == "gemini" else None
