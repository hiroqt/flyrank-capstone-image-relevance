"""Recorded AI outputs, so results reproduce without a key and without re-paying (probe P5).

`export` writes what the AI produced for a tenant: each image's validated vision metadata, each
post's validated topic, and every embedding (vectors as base64 float32, exactly what pgvector stores).
`load` replays that into a tenant with NO AI calls, then classifies with the *current* rules
(taxonomy + thresholds + VISION_MIN_CONFIDENCE), using the same functions as the worker.

Why: Gemini is not deterministic, so re-processing the corpus can shift a caption or two. The eval
in the README is computed on this recording, so anyone can reproduce the exact number.
"""
import base64
import json
import struct
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Image, Post, Tenant
from app.domain.schemas import ImageMetadata
from app.repositories import embeddings as embeddings_repo
from app.repositories import images as images_repo
from app.repositories import posts as posts_repo
from app.repositories import tenants as tenants_repo
from app.services.embedding import text_hash
from app.services.pipeline import classify_image, classify_post
from app.services.taxonomy import SubjectResolver, Taxonomy, resolver_version
from app.services.thresholds import Thresholds

FORMAT = 1


class SnapshotError(Exception):
    pass


def _pack(vector) -> str:
    return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode()


def _unpack(data: str) -> list[float]:
    raw = base64.b64decode(data)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def _vec(row) -> dict:
    return {"text_hash": row.text_hash, "vector": _pack(row.vector)}


def export(session: Session, tenant_id: str, path: Path, *, embed_model: str, taxonomy: Taxonomy) -> dict:
    def vectors(owner_type: str) -> dict:
        return embeddings_repo.by_owner(session, tenant_id, owner_type, embed_model)

    image_vecs, subject_vecs, post_vecs, label_vecs = (vectors(t) for t in ("image", "subject", "post", "label"))
    images = []
    for image in session.scalars(select(Image).where(Image.tenant_id == tenant_id).order_by(Image.file_path)):
        key = str(image.id)
        if image.status not in ("tagged", "flagged") or key not in image_vecs or key not in subject_vecs:
            continue  # only fully processed images are recorded
        attributes = sorted(t.tag for t in images_repo.tags_for(session, image.id) if t.kind == "attribute")
        images.append({
            "file": image.file_path.rsplit("/", 1)[-1], "file_path": image.file_path, "sha256": image.sha256,
            "source_url": image.source_url, "license": image.license, "photographer": image.photographer,
            "vision": {"subject": image.subject, "category": image.category, "attributes": attributes,
                       "caption": image.caption, "confidence": image.confidence},
            "vision_model": image.vision_model, "prompt_version": image.prompt_version,
            "vectors": {"image": _vec(image_vecs[key]), "subject": _vec(subject_vecs[key])},
        })
    posts = []
    for post in session.scalars(select(Post).where(Post.tenant_id == tenant_id).order_by(Post.slug)):
        key = str(post.id)
        if post.status != "embedded" or not post.topic or key not in post_vecs or key not in subject_vecs:
            continue
        posts.append({"slug": post.slug, "title": post.title, "body": post.body, "topic": post.topic,
                      "vectors": {"post": _vec(post_vecs[key]), "subject": _vec(subject_vecs[key])}})
    labels = {key: {"text": text, **_vec(label_vecs[key])}
              for key, text in taxonomy.all_labels().items() if key in label_vecs}
    data = {
        "format": FORMAT, "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_tenant": tenant_id, "embed_model": embed_model, "taxonomy_version": taxonomy.version,
        "counts": {"images": len(images), "posts": len(posts), "labels": len(labels)},
        "labels": labels, "images": images, "posts": posts,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return data["counts"]


def read(path: Path) -> dict:
    if not path.exists():
        raise SnapshotError(f"{path} not found. Create it with: python -m app.cli snapshot")
    data = json.loads(path.read_text())
    if data.get("format") != FORMAT:
        raise SnapshotError(f"{path}: unsupported snapshot format {data.get('format')!r}")
    return data


def load(session: Session, tenant_id: str, data: dict, *, taxonomy: Taxonomy, thresholds: Thresholds,
         settings: Settings, fresh: bool = True) -> dict:
    """Replay a snapshot into `tenant_id` (no AI calls). fresh=True wipes the tenant first (deterministic)."""
    model = data["embed_model"]
    labels = data["labels"]
    stale = [k for k, text in taxonomy.all_labels().items()
             if k not in labels or labels[k]["text_hash"] != text_hash(text)]
    if stale:
        raise SnapshotError(f"config/taxonomy.yaml changed since the snapshot was recorded (labels {stale}); "
                            "re-process and re-export: python -m app.cli process --wait && python -m app.cli snapshot")

    if fresh and session.get(Tenant, tenant_id) is not None:
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))  # cascades to every tenant-owned row
        session.flush()
    tenants_repo.upsert(session, tenant_id, f"Snapshot replay of '{data['source_tenant']}' ({data['created_at']})")

    def put(owner_type: str, owner_id: str, rec: dict) -> list[float]:
        vector = _unpack(rec["vector"])
        embeddings_repo.upsert(session, tenant_id, owner_type=owner_type, owner_id=owner_id, model=model,
                               vector=vector, text_hash=rec["text_hash"])
        return vector

    for key in taxonomy.all_labels():
        put("label", key, labels[key])
    resolver = SubjectResolver({k: _unpack(labels[k]["vector"]) for k in taxonomy.all_labels()},
                               frozenset(taxonomy.labels), thresholds.label_min, thresholds.label_margin,
                               version=resolver_version(taxonomy, thresholds.label_min, thresholds.label_margin))

    for rec in data["images"]:
        images_repo.register(session, tenant_id, sha256=rec["sha256"], file_path=rec["file_path"],
                             source_url=rec["source_url"], license=rec["license"], photographer=rec["photographer"])
        image = session.scalar(select(Image).where(Image.tenant_id == tenant_id, Image.sha256 == rec["sha256"]))
        meta = ImageMetadata.model_validate(rec["vision"])  # recorded output is re-validated, never trusted blindly
        images_repo.save_metadata(session, image, meta, json.dumps(rec["vision"]), status="tagged", flag_reasons=[],
                                  model=rec["vision_model"], prompt_version=rec["prompt_version"], job_id=None)
        session.flush()
        put("image", str(image.id), rec["vectors"]["image"])
        classify_image(image, put("subject", str(image.id), rec["vectors"]["subject"]), resolver, settings)

    for rec in data["posts"]:
        posts_repo.upsert(session, tenant_id, slug=rec["slug"], title=rec["title"], body=rec["body"])
        post = posts_repo.get_by_ref(session, tenant_id, rec["slug"])
        post.topic = rec["topic"]
        put("post", str(post.id), rec["vectors"]["post"])
        classify_post(post, put("subject", str(post.id), rec["vectors"]["subject"]), resolver, settings)
    session.flush()
    return {"images": len(data["images"]), "posts": len(data["posts"]), "embed_model": model}
