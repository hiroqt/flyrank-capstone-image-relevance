"""Post data access. Every function is tenant-scoped."""
import uuid
from collections.abc import Sequence

from sqlalchemy import String, and_, case, cast, func, literal, literal_column, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Embedding, Post


def get(session: Session, tenant_id: str, post_id: uuid.UUID) -> Post | None:
    return session.scalar(select(Post).where(Post.tenant_id == tenant_id, Post.id == post_id))


def get_by_ref(session: Session, tenant_id: str, ref: str) -> Post | None:
    """A post by UUID or by slug (the probes use slugs, e.g. the-behavior-of-red-foxes)."""
    try:
        return get(session, tenant_id, uuid.UUID(ref))
    except ValueError:
        return session.scalar(select(Post).where(Post.tenant_id == tenant_id, Post.slug == ref))


def list_(session: Session, tenant_id: str, *, status: str | None = None, subject_key: str | None = None,
          limit: int = 50, offset: int = 0) -> tuple[Sequence[Post], int]:
    query = select(Post).where(Post.tenant_id == tenant_id)
    if status:
        query = query.where(Post.status == status)
    if subject_key:
        query = query.where(Post.subject_key == subject_key)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(Post.slug).limit(limit).offset(offset)).all()
    return rows, total


def upsert(session: Session, tenant_id: str, *, slug: str, title: str, body: str) -> bool:
    """Seed helper. Returns True if the post is new. Changed text sends the post back to `pending`."""
    stmt = insert(Post).values(tenant_id=tenant_id, slug=slug, title=title, body=body)
    changed = (Post.title != stmt.excluded.title) | (Post.body != stmt.excluded.body)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Post.tenant_id, Post.slug],
        set_={"title": stmt.excluded.title, "body": stmt.excluded.body,
              "status": case((changed, "pending"), else_=Post.status),
              "updated_at": case((changed, func.now()), else_=Post.updated_at)},
    ).returning(literal_column("(xmax = 0)"))  # Postgres: xmax is 0 only for a freshly inserted row
    return bool(session.scalar(stmt))


def create(session: Session, tenant_id: str, *, slug: str, title: str, body: str) -> Post:
    post = Post(tenant_id=tenant_id, slug=slug, title=title, body=body)
    session.add(post)
    session.flush()
    session.refresh(post)
    return post


def ids_needing_embedding(session: Session, tenant_id: str, model: str, *, force: bool, resolver: str,
                          post_ids: Sequence[uuid.UUID] | None = None, dims: int | None = None) -> list[uuid.UUID]:
    """Posts that are new, changed, embedded by another model/size, or resolved under different rules."""
    query = select(Post.id).where(Post.tenant_id == tenant_id)
    if post_ids is not None:
        query = query.where(Post.id.in_(post_ids))
    if not force:
        has_vector = (select(Embedding.id)
                      .where(and_(Embedding.tenant_id == Post.tenant_id, Embedding.owner_type == "post",
                                  Embedding.owner_id == cast(Post.id, String), Embedding.model == model,
                                  true() if dims is None else Embedding.dim == dims))
                      .exists())
        stale = Post.subject_key_detail["resolver"].astext.is_distinct_from(resolver)
        # The topic was read from different text (e.g. the text changed while a job was processing it).
        # Same hash as app.services.embedding.text_hash(post_text(post)): sha256 of "title\n\nbody".
        current_text = func.encode(func.sha256(func.convert_to(Post.title + literal("\n\n") + Post.body,
                                                               literal("UTF8"))), literal("hex"))
        topic_stale = Post.topic["text_hash"].astext.is_distinct_from(current_text)
        query = query.where((Post.status != "embedded") | ~has_vector | stale | topic_stale)
    return list(session.scalars(query.order_by(Post.slug)))


def mark_failed(session: Session, tenant_id: str, post_id: uuid.UUID) -> None:
    post = get(session, tenant_id, post_id)
    if post is not None:
        post.status = "failed"
