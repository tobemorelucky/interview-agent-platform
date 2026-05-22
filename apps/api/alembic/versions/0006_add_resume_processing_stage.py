"""add processing_stage and stage_message to resumes

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-22

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "resumes",
        sa.Column("processing_stage", sa.String(30), nullable=True),
    )
    op.add_column(
        "resumes",
        sa.Column("stage_message", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("resumes", "stage_message")
    op.drop_column("resumes", "processing_stage")
