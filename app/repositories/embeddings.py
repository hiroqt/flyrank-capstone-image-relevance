"""Embedding storage and similarity search (pgvector). Every function is tenant-scoped."""
import uuid
from collections.abc import Sequence

from sqlalchemy import String, and_, cast, func, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Embedding, Image

IMAGE_DONE = ("tagged", "flagged")


def get(session: Session, tenant_id: str, owner_type: str, owner_id: str, model: str) -> Embedding | None:
    return session.scalar(select(Embedding).where(
        Embedding.tenant_id == tenant_id, Embedding.owner_type == owner_type,
        Embedding.owner_id == owner_id, Embedding.model == model))


def models_for(session: Session, tenant_id: str, owner_type: str, owner_id: str) -> list[str]:
    return list(session.scalars(select(Embedding.model).where(
        Embedding.tenant_id == tenant_id, Embedding.owner_type == owner_type, Embedding.owner_id == owner_id)
        .order_by(Embedding.created_at.desc())))


def by_owner(session: Session, tenant_id: str, owner_type: str, model: str) -> dict[str, Embedding]:
    rows = session.scalars(select(Embedding).where(
        Embedding.tenant_id == tenant_id, Embedding.owner_type == owner_type, Embedding.model == model))
    return {row.owner_id: row for row in rows}


def upsert(session: Session, tenant_id: str, *, owner_type: str, owner_id: str, model: str,
           vector: Sequence[float], text_hash: str) -> None:
    """Idempotent by (tenant, owner, model): re-embedding replaces the vector."""
    values = {"tenant_id": tenant_id, "owner_type": owner_type, "owner_id": owner_id, "model": model,
              "dim": len(vector), "vector": list(vector), "text_hash": text_hash}
    stmt = insert(Embedding).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Embedding.tenant_id, Embedding.owner_type, Embedding.owner_id, Embedding.model],
        set_={"vector": stmt.excluded.vector, "dim": stmt.excluded.dim, "text_hash": stmt.excluded.text_hash,
              "created_at": func.now()},
    )
    session.execute(stmt)


def rank_images(session: Session, tenant_id: str, model: str, post_id: uuid.UUID) -> list[tuple[Image, float]]:
    """Every embedded image of the tenant, most similar to the post first: [(image, cosine similarity)].

    Exact search (no ANN index): at ~50 images it is instant, and exact is easier to reason about.
    The post vector is read by a subquery, so both sides of `<=>` are typed vector columns.
    """
    post = (select(Embedding.vector, Embedding.dim)
            .where(Embedding.tenant_id == tenant_id, Embedding.owner_type == "post",
                   Embedding.owner_id == str(post_id), Embedding.model == model)
            .subquery())
    distance = Embedding.vector.cosine_distance(select(post.c.vector).scalar_subquery())
    rows = session.execute(
        select(Image, (1 - distance).label("similarity"))
        .join(Embedding, and_(Embedding.tenant_id == Image.tenant_id, Embedding.owner_type == "image",
                              Embedding.owner_id == cast(Image.id, String), Embedding.model == model,
                              # only vectors of the same size are comparable (EMBED_DIMENSIONS may change)
                              Embedding.dim == select(post.c.dim).scalar_subquery()))
        .where(Image.tenant_id == tenant_id)
        .order_by(distance, Image.id)
    ).all()
    return [(image, float(similarity)) for image, similarity in rows if similarity is not None]


def image_ids_needing_subject(session: Session, tenant_id: str, model: str, resolver: str,
                              dims: int | None = None) -> list[uuid.UUID]:
    """Classified images whose embedding is missing (tagged before Phase 3, or a model switch) or whose
    subject_key was resolved under different rules (taxonomy/thresholds changed: re-resolving is free)."""
    has_vector = (select(Embedding.id)
                  .where(Embedding.tenant_id == tenant_id, Embedding.owner_type == "image",
                         Embedding.owner_id == cast(Image.id, String), Embedding.model == model,
                         true() if dims is None else Embedding.dim == dims)
                  .exists())
    stale = Image.subject_key_detail["resolver"].astext.is_distinct_from(resolver)
    return list(session.scalars(select(Image.id)
                                .where(Image.tenant_id == tenant_id, Image.status.in_(IMAGE_DONE), ~has_vector | stale)
                                .order_by(Image.created_at, Image.id)))
