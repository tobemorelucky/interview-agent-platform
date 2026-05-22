"""add task_id, processing timestamps to kb_documents

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-21

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("kb_documents", sa.Column("task_id", sa.String(100), nullable=True))
    op.add_column(
        "kb_documents",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "kb_documents",
        sa.Column("processing_finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kb_documents", "processing_finished_at")
    op.drop_column("kb_documents", "processing_started_at")
    op.drop_column("kb_documents", "task_id")
