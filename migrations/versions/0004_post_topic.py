"""post topic: posts.topic (structured text extraction) + 'text' AI operation in the cost log

Posts are resolved to a subject_key from a short subject phrase, like images: a structured
text call reads the post and returns {subject, category, confidence}. That call is cost-tracked
as operation='text'.

Revision ID: 0004
Revises: 0003
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("topic", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.drop_constraint(op.f("ck_ai_calls_operation"), "ai_calls", type_="check")
    op.create_check_constraint(op.f("ck_ai_calls_operation"), "ai_calls", "operation IN ('vision', 'text', 'embed')")


def downgrade() -> None:
    op.execute("DELETE FROM ai_calls WHERE operation = 'text'")
    op.drop_constraint(op.f("ck_ai_calls_operation"), "ai_calls", type_="check")
    op.create_check_constraint(op.f("ck_ai_calls_operation"), "ai_calls", "operation IN ('vision', 'embed')")
    op.drop_column("posts", "topic")
