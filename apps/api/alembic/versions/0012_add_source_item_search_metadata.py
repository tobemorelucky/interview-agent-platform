"""add search metadata to experience_source_items

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("experience_source_items", sa.Column("query_text", sa.Text(), nullable=True))
    op.add_column("experience_source_items", sa.Column("snippet", sa.Text(), nullable=True))
    op.add_column("experience_source_items", sa.Column("engine", sa.String(128), nullable=True))
    op.add_column("experience_source_items", sa.Column("matched_reason", sa.Text(), nullable=True))
    op.add_column("experience_source_items", sa.Column("filtered_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("experience_source_items", "filtered_reason")
    op.drop_column("experience_source_items", "matched_reason")
    op.drop_column("experience_source_items", "engine")
    op.drop_column("experience_source_items", "snippet")
    op.drop_column("experience_source_items", "query_text")
