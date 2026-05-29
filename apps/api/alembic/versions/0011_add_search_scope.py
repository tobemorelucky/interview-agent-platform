"""add search_scope to experience_collection_tasks

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experience_collection_tasks",
        sa.Column(
            "search_scope",
            sa.String(32),
            nullable=False,
            server_default="JOB",
        ),
    )


def downgrade() -> None:
    op.drop_column("experience_collection_tasks", "search_scope")
