"""Embedding generation, run only by the background worker (brief section 4 part 4).

Three kinds of vectors share one space (task type SEMANTIC_SIMILARITY):
  image   - caption + subject + attributes   -> ranking against posts
  subject - a short subject phrase           -> subject_key (the image's vision subject, or the
                                                post's extracted topic)
  post    - title + body                     -> ranking
  label   - each taxonomy description        -> subject_key resolution

Idempotent: a vector is only (re)computed when its text hash changed, so a retried job item
never pays twice for the same text.
"""
import hashlib
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models import Image, Post
from app.providers.tracked import CallContext, TrackedEmbedder
from app.repositories import embeddings as embeddings_repo
from app.repositories import images as images_repo
from app.services.taxonomy import Taxonomy


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def image_text(caption: str | None, subject: str | None, attributes: Sequence[str]) -> str:
    parts = [caption or "", f"Subject: {subject or 'unknown'}."]
    if attributes:
        parts.append(f"Attributes: {', '.join(attributes)}.")
    return " ".join(p for p in parts if p)


def post_text(post: Post) -> str:
    return f"{post.title}\n\n{post.body}"


def _ensure(session: Session, tenant_id: str, embedder: TrackedEmbedder, ctx: CallContext,
            wanted: dict[tuple[str, str], str], *, force: bool) -> dict[tuple[str, str], list[float]]:
    """wanted: {(owner_type, owner_id): text}. Embeds only stale/missing texts, in one call."""
    current = {key: embeddings_repo.get(session, tenant_id, key[0], key[1], embedder.model) for key in wanted}
    dims = embedder.dimensions
    stale = [key for key, text in wanted.items()
             if force or current[key] is None or current[key].text_hash != text_hash(text)
             or (dims is not None and current[key].dim != dims)]  # EMBED_DIMENSIONS changed: re-embed
    vectors = {key: list(row.vector) for key, row in current.items() if row is not None and key not in stale}
    if stale:
        fresh = embedder.embed(ctx, [wanted[key] for key in stale])
        for key, vector in zip(stale, fresh, strict=True):
            embeddings_repo.upsert(session, tenant_id, owner_type=key[0], owner_id=key[1], model=embedder.model,
                                   vector=vector, text_hash=text_hash(wanted[key]))
            vectors[key] = list(vector)
        session.flush()
    return vectors


def ensure_label_vectors(session: Session, tenant_id: str, taxonomy: Taxonomy, embedder: TrackedEmbedder,
                         *, job_id=None) -> dict[str, list[float]]:
    """Vectors for every taxonomy label, in scope and out of scope (embedded once per model)."""
    labels = taxonomy.all_labels()
    ctx = CallContext(tenant_id=tenant_id, entity_type="label", entity_id=f"taxonomy:{taxonomy.version}",
                      job_id=job_id)
    vectors = _ensure(session, tenant_id, embedder, ctx,
                      {("label", key): text for key, text in labels.items()}, force=False)
    return {key: vectors[("label", key)] for key in labels}


def ensure_image_vectors(session: Session, image: Image, embedder: TrackedEmbedder, ctx: CallContext,
                         *, force: bool = False) -> tuple[list[float], list[float]]:
    """Returns (image vector, subject vector)."""
    attributes = [t.tag for t in images_repo.tags_for(session, image.id) if t.kind == "attribute"]
    owner = str(image.id)
    wanted = {("image", owner): image_text(image.caption, image.subject, attributes),
              ("subject", owner): image.subject or "unknown"}
    vectors = _ensure(session, image.tenant_id, embedder, ctx, wanted, force=force)
    return vectors[("image", owner)], vectors[("subject", owner)]


def ensure_post_vectors(session: Session, post: Post, subject: str, embedder: TrackedEmbedder, ctx: CallContext,
                        *, force: bool = False) -> tuple[list[float], list[float]]:
    """Returns (post vector for ranking, vector of the post's topic subject for subject_key)."""
    owner = str(post.id)
    wanted = {("post", owner): post_text(post), ("subject", owner): subject}
    vectors = _ensure(session, post.tenant_id, embedder, ctx, wanted, force=force)
    return vectors[("post", owner)], vectors[("subject", owner)]
