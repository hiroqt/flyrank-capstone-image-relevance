"""images.last_job_id: which job last wrote an image's metadata (idempotent retries)

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("images", sa.Column("last_job_id", sa.UUID(), nullable=True))
    op.create_foreign_key(op.f("fk_images_last_job_id_jobs"), "images", "jobs",
                          ["last_job_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint(op.f("fk_images_last_job_id_jobs"), "images", type_="foreignkey")
    op.drop_column("images", "last_job_id")
