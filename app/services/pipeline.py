"""Work done by one background job item (brief section 4, parts 1, 2 and 4).

Image item:  vision -> schema validation -> embeddings -> subject_key -> tagged | flagged | failed
Post item:   topic (structured text call, validated) -> embeddings -> subject_key -> embedded

Invalid vision output is retried with a repair prompt and is never stored as if it were valid.
An image is flagged (not accepted) when its confidence is low OR its subject does not resolve to
a known category: two independent signals, because self-reported confidence is not calibrated.
"""
import io
import logging
import uuid
from dataclasses import dataclass

from PIL import Image as PILImage
from PIL import UnidentifiedImageError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Image, Post
from app.domain.schemas import POST_TOPIC_JSON_SCHEMA, VISION_JSON_SCHEMA, ImageMetadata, PostTopic
from app.providers.tracked import CallContext, InvalidOutput, TrackedEmbedder, TrackedVision
from app.repositories import images as images_repo
from app.repositories import jobs as jobs_repo
from app.repositories import posts as posts_repo
from app.services.embedding import ensure_image_vectors, ensure_post_vectors, post_text, text_hash
from app.services.taxonomy import Resolution, SubjectResolver

log = logging.getLogger(__name__)

# v2: v1's descriptive rubric was ignored (every image scored >= 0.80, even a motion-blurred
# silhouette), so v2 caps confidence by conditions that are visible in the image.
PROMPT_VERSION = "v2"

VISION_PROMPT = """You label photos for a wildlife and nature image library.
Look only at what is visible in the image and return JSON with these fields:

- subject: the most specific common English name of the main subject, e.g. "red fox", "gray wolf",
  "golden retriever", "brown bear", "white-tailed deer". Use a common name, not a scientific name.
- category: one of animal, plant, landscape, person, food, object, other.
- attributes: 3 to 8 short visual descriptors (colour, setting, action, season, lighting, framing).
- caption: one factual sentence describing the image.
- confidence: how sure you are that `subject` is correct at the species/breed level, from 0 to 1.

Confidence rules. A low score sends the image to a human, which is cheap; a wrong confident
score puts the wrong photo in front of readers, which is expensive. Check these caps in order and
use the lowest one that applies:
- At most 0.60 if ANY of these is true: the subject is a silhouette or backlit outline; it is
  motion-blurred or out of focus; it fills less than about a tenth of the frame; it is mostly in
  darkness; or the crop shows only part of it (for example just an eye or an ear).
- At most 0.85 if a look-alike is plausible and the features that tell them apart are not all
  clearly visible: fox vs coyote vs jackal vs dog; wolf vs coyote vs husky or wolfdog;
  one deer species vs another; black bear vs brown bear.
- 0.90 or more only when the whole animal (or its full face) is sharp, well lit, and shows the
  features that rule out every look-alike.
- Below 0.40 means you are guessing.
"""


TOPIC_PROMPT_VERSION = "t1"

TOPIC_PROMPT = """You label blog posts for a wildlife and nature image library.
Read the post below and return JSON with these fields:

- subject: the most specific common English name of the one thing the post is mainly about,
  e.g. "red fox", "gray wolf", "golden retriever", "sourdough bread". If the post only uses a
  scientific name, give the common English name instead.
- category: one of animal, plant, landscape, person, food, object, other.
- confidence: how sure you are that `subject` is what the post is mainly about, from 0 to 1.

"""


def repair_prompt(error: str, prompt: str = VISION_PROMPT) -> str:
    return (f"{prompt}\nYour previous answer was rejected by schema validation: {error}\n"
            "Answer again with JSON only, exactly matching the required fields and constraints.")


class PermanentItemError(Exception):
    """The item cannot succeed by retrying (missing file, output invalid after all repairs)."""


class ItemSuperseded(Exception):
    """A stale-lock recovery handed this item to another worker: drop our work, don't touch the item."""


@dataclass(frozen=True)
class ItemContext:
    tenant_id: str
    job_id: uuid.UUID | None
    job_item_id: int | None
    force: bool
    claim_attempt: int | None = None   # the claim token (job_items.attempts when we claimed it)


def _commit_paid_result(session: Session, ctx: ItemContext) -> None:
    """Commit a paid, validated result mid-item, but only while we still own the claim (row-locked check)."""
    if ctx.job_item_id is not None and ctx.claim_attempt is not None and \
            not jobs_repo.still_claimed(session, ctx.job_item_id, ctx.claim_attempt):
        session.rollback()
        raise ItemSuperseded(f"job item {ctx.job_item_id} was re-claimed by another worker")
    session.commit()


def _clear_subject(row) -> None:
    """New vision output / a new topic invalidates the old subject_key until it is re-resolved. A NULL
    resolver version makes the next job pick the item up again (re-resolving is free); until then the
    guard treats the subject as unknown, so a half-processed row can never be matched on stale data."""
    row.subject_key = None
    row.subject_key_score = None
    row.subject_key_detail = None


def confidence_reasons(confidence: float | None, min_confidence: float) -> list[str]:
    """Why a *valid* result must still go to a human instead of being accepted."""
    if confidence is not None and confidence < min_confidence:
        return [f"Low confidence {confidence:.2f} < {min_confidence:.2f}: identification "
                "is uncertain, needs human review"]
    return []


def subject_reasons(subject: str | None, resolution: Resolution) -> list[str]:
    if resolution.reason is None:
        return []
    return [f"Subject '{subject}' does not resolve to a known category ({resolution.reason})"]


def load_image(path, max_side: int) -> tuple[bytes, str]:
    """Read, sanity-check and downscale an image (fewer vision tokens). Returns (jpeg bytes, mime)."""
    try:
        with PILImage.open(path) as img:
            img.load()
            if img.format not in ("JPEG", "PNG", "WEBP"):
                raise PermanentItemError(f"Unsupported image format {img.format!r}")
            img = img.convert("RGB")
            img.thumbnail((max_side, max_side))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            return buffer.getvalue(), "image/jpeg"
    except FileNotFoundError as exc:
        raise PermanentItemError(f"Image file not found: {path}") from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise PermanentItemError(f"Unreadable image file {path}: {exc}") from exc


DONE_STATUSES = ("tagged", "flagged")


def _needs_vision(image: Image, ctx: ItemContext, model: str) -> bool:
    if image.status not in DONE_STATUSES:
        return True
    if ctx.job_id is not None and image.last_job_id == ctx.job_id:
        return False  # this job already paid for it (a crash-retried item)
    if ctx.force:
        return True
    return not (image.vision_model == model and image.prompt_version == PROMPT_VERSION)


def _run_vision(session: Session, image: Image, ctx: ItemContext, vision: TrackedVision, settings: Settings) -> None:
    data, mime = load_image(settings.resolve_path(image.file_path), settings.vision_max_side_px)
    call_ctx = CallContext(tenant_id=ctx.tenant_id, entity_type="image", entity_id=str(image.id),
                           job_id=ctx.job_id, job_item_id=ctx.job_item_id)
    prompt = VISION_PROMPT
    last_error: InvalidOutput | None = None
    for _ in range(settings.vision_max_repairs + 1):
        try:
            meta, raw_text = vision.describe_validated(call_ctx, data, mime, prompt, ImageMetadata,
                                                       VISION_JSON_SCHEMA)
            break
        except InvalidOutput as exc:
            last_error = exc
            prompt = repair_prompt(exc.error)
    else:
        raise PermanentItemError(
            f"Vision output failed schema validation {settings.vision_max_repairs + 1} times; "
            f"last error: {last_error.error if last_error else 'unknown'}"
        )
    reasons = confidence_reasons(meta.confidence, settings.vision_min_confidence)
    images_repo.save_metadata(session, image, meta, raw_text, status="flagged" if reasons else "tagged",
                              flag_reasons=reasons, model=vision.model, prompt_version=PROMPT_VERSION,
                              job_id=ctx.job_id)
    _clear_subject(image)
    # Keep the paid, validated result even if the embedding step below fails and the item is retried.
    _commit_paid_result(session, ctx)


def process_image(session: Session, image_id: uuid.UUID, ctx: ItemContext, vision: TrackedVision,
                  settings: Settings, *, embedder: TrackedEmbedder, resolver: SubjectResolver) -> str:
    """Returns the image's resulting status: 'tagged' or 'flagged'.

    Raises PermanentItemError (do not retry), or lets provider errors / BudgetExceeded propagate
    so the worker can decide (retry with backoff, or stop the job).
    """
    image = images_repo.get(session, ctx.tenant_id, image_id)
    if image is None:
        raise PermanentItemError(f"Image {image_id} not found")

    if _needs_vision(image, ctx, vision.model):
        _run_vision(session, image, ctx, vision, settings)

    call_ctx = CallContext(tenant_id=ctx.tenant_id, entity_type="image", entity_id=str(image.id),
                           job_id=ctx.job_id, job_item_id=ctx.job_item_id)
    _, subject_vector = ensure_image_vectors(session, image, embedder, call_ctx)
    return classify_image(image, subject_vector, resolver, settings)


def classify_image(image: Image, subject_vector: list[float], resolver: SubjectResolver, settings: Settings) -> str:
    """Final status of an image that has valid metadata: tagged, or flagged with every reason why."""
    resolution = resolver.resolve(subject_vector, category=image.category, subject=image.subject)
    reasons = (confidence_reasons(image.confidence, settings.vision_min_confidence)
               + subject_reasons(image.subject, resolution))
    image.subject_key = resolution.key
    image.subject_key_score = resolution.best_score
    image.subject_key_detail = {**resolution.detail(), "resolver": resolver.version}
    image.flag_reasons = reasons
    image.status = "flagged" if reasons else "tagged"
    return image.status


def _topic_is_current(post: Post, model: str) -> bool:
    t = post.topic or {}
    return (t.get("model") == model and t.get("prompt_version") == TOPIC_PROMPT_VERSION
            and t.get("text_hash") == text_hash(post_text(post)))


def _extract_topic(session: Session, post: Post, ctx: ItemContext, vision: TrackedVision,
                   settings: Settings) -> None:
    call_ctx = CallContext(tenant_id=ctx.tenant_id, entity_type="post", entity_id=str(post.id),
                           job_id=ctx.job_id, job_item_id=ctx.job_item_id)
    base_prompt = f"{TOPIC_PROMPT}Title: {post.title}\n\n{post.body}\n"
    prompt = base_prompt
    last_error: InvalidOutput | None = None
    for _ in range(settings.vision_max_repairs + 1):
        try:
            topic, _raw = vision.generate_validated(call_ctx, prompt, PostTopic, POST_TOPIC_JSON_SCHEMA)
            break
        except InvalidOutput as exc:
            last_error = exc
            prompt = repair_prompt(exc.error, base_prompt)
    else:
        raise PermanentItemError(
            f"Post topic output failed schema validation {settings.vision_max_repairs + 1} times; "
            f"last error: {last_error.error if last_error else 'unknown'}"
        )
    post.topic = {**topic.model_dump(mode="json"), "model": vision.model, "prompt_version": TOPIC_PROMPT_VERSION,
                  "text_hash": text_hash(post_text(post)), "job_id": str(ctx.job_id) if ctx.job_id else None}
    _clear_subject(post)
    _commit_paid_result(session, ctx)  # keep the paid, validated answer even if embedding fails and we retry


def process_post(session: Session, post_id: uuid.UUID, ctx: ItemContext, vision: TrackedVision,
                 settings: Settings, *, embedder: TrackedEmbedder, resolver: SubjectResolver) -> str:
    """Read what a post is about, embed it, resolve its subject_key. Returns 'succeeded'."""
    post = posts_repo.get(session, ctx.tenant_id, post_id)
    if post is None:
        raise PermanentItemError(f"Post {post_id} not found")
    done_by_this_job = ctx.job_id is not None and (post.topic or {}).get("job_id") == str(ctx.job_id)
    if not done_by_this_job and (ctx.force or not _topic_is_current(post, vision.model)):
        _extract_topic(session, post, ctx, vision, settings)

    topic = post.topic
    call_ctx = CallContext(tenant_id=ctx.tenant_id, entity_type="post", entity_id=str(post.id),
                           job_id=ctx.job_id, job_item_id=ctx.job_item_id)
    # No force here: the text hash already tells whether a vector is stale, so a retry never re-pays.
    _, subject_vector = ensure_post_vectors(session, post, topic["subject"], embedder, call_ctx)
    classify_post(post, subject_vector, resolver, settings)
    return "succeeded"


def classify_post(post: Post, subject_vector: list[float], resolver: SubjectResolver, settings: Settings) -> None:
    """Resolve what an embedded post is about (its topic's subject phrase -> subject_key)."""
    topic = post.topic
    resolution = resolver.resolve(subject_vector, category=topic["category"], subject=topic["subject"])
    if resolution.reason is None and topic["confidence"] < settings.vision_min_confidence:
        resolution = Resolution("unknown", resolution.best, resolution.best_score, resolution.runner_up,
                                resolution.runner_up_score,
                                f"unsure what the post is about (topic confidence {topic['confidence']:.2f} "
                                f"< {settings.vision_min_confidence:.2f})")
    post.subject_key = resolution.key
    post.subject_key_score = resolution.best_score
    post.subject_key_detail = {**resolution.detail(), "resolver": resolver.version}
    post.status = "embedded"
