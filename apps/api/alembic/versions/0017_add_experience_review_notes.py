"""add experience review notes

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("interview_experiences", sa.Column("review_note", sa.Text(), nullable=True))
    op.add_column("interview_experiences", sa.Column("reject_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_experiences", "reject_reason")
    op.drop_column("interview_experiences", "review_note")
