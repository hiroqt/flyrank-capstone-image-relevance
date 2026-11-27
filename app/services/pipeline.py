"""Image understanding pipeline (brief section 4, part 1).

One image -> vision model -> schema validation -> tagged | flagged | failed.
Invalid output is retried with a repair prompt and is never stored as if it were valid.
Low-confidence results are flagged, not accepted.
"""
import io
import logging
import uuid
from dataclasses import dataclass

from PIL import Image as PILImage
from PIL import UnidentifiedImageError
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.schemas import VISION_JSON_SCHEMA, ImageMetadata
from app.providers.tracked import CallContext, InvalidOutput, TrackedVision
from app.repositories import images as images_repo

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


def repair_prompt(error: str) -> str:
    return (f"{VISION_PROMPT}\nYour previous answer was rejected by schema validation: {error}\n"
            "Answer again with JSON only, exactly matching the required fields and constraints.")


class PermanentItemError(Exception):
    """The item cannot succeed by retrying (missing file, output invalid after all repairs)."""


@dataclass(frozen=True)
class ItemContext:
    tenant_id: str
    job_id: uuid.UUID | None
    job_item_id: int | None
    force: bool


def flag_reasons(meta: ImageMetadata, min_confidence: float) -> list[str]:
    """Why a *valid* result must still go to a human instead of being accepted."""
    reasons = []
    if meta.confidence < min_confidence:
        reasons.append(f"Low confidence {meta.confidence:.2f} < {min_confidence:.2f}: identification "
                       "is uncertain, needs human review")
    return reasons


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


def process_image(session: Session, image_id: uuid.UUID, ctx: ItemContext, vision: TrackedVision,
                  settings: Settings) -> str:
    """Returns the image's resulting status: 'tagged' or 'flagged'.

    Raises PermanentItemError (do not retry), or lets provider errors / BudgetExceeded propagate
    so the worker can decide (retry with backoff, or stop the job).
    """
    image = images_repo.get(session, ctx.tenant_id, image_id)
    if image is None:
        raise PermanentItemError(f"Image {image_id} not found")

    # Idempotency: a crash-retried item that this job already completed is not paid for twice.
    if ctx.job_id is not None and image.last_job_id == ctx.job_id and image.status in ("tagged", "flagged"):
        log.info("image %s already processed by job %s; skipping", image_id, ctx.job_id)
        return image.status
    if (not ctx.force and image.status in ("tagged", "flagged") and image.vision_model == vision.model
            and image.prompt_version == PROMPT_VERSION):
        return image.status

    data, mime = load_image(settings.resolve_path(image.file_path), settings.vision_max_side_px)
    call_ctx = CallContext(tenant_id=ctx.tenant_id, entity_type="image", entity_id=str(image_id),
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

    reasons = flag_reasons(meta, settings.vision_min_confidence)
    status = "flagged" if reasons else "tagged"
    images_repo.save_metadata(session, image, meta, raw_text, status=status, flag_reasons=reasons,
                              model=vision.model, prompt_version=PROMPT_VERSION, job_id=ctx.job_id)
    return status
