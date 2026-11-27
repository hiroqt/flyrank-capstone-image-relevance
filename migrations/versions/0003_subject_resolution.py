"""subject resolution: 'subject' embeddings + subject_key_detail on images and posts

An image's subject_key is resolved from the embedding of its short `subject` string
(owner_type='subject'); posts from their full-text embedding. subject_key_detail keeps the
best and runner-up labels with their scores, so the guard can explain an `unknown`.

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_embeddings_owner_type"), "embeddings", type_="check")
    op.create_check_constraint(op.f("ck_embeddings_owner_type"), "embeddings",
                               "owner_type IN ('image', 'subject', 'post', 'label')")
    for table in ("images", "posts"):
        op.add_column(table, sa.Column("subject_key_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    for table in ("images", "posts"):
        op.drop_column(table, "subject_key_detail")
    op.execute("DELETE FROM embeddings WHERE owner_type = 'subject'")
    op.drop_constraint(op.f("ck_embeddings_owner_type"), "embeddings", type_="check")
    op.create_check_constraint(op.f("ck_embeddings_owner_type"), "embeddings",
                               "owner_type IN ('image', 'post', 'label')")
