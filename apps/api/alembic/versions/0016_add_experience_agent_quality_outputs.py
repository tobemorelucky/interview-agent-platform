"""add experience agent routing reliability quality outputs

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("interview_experiences", sa.Column("routing_json", sa.JSON(), nullable=True))
    op.add_column("interview_experiences", sa.Column("reliability_json", sa.JSON(), nullable=True))
    op.add_column("interview_experiences", sa.Column("quality_gate_json", sa.JSON(), nullable=True))

    op.add_column("interview_questions", sa.Column("technical_categories_json", sa.JSON(), nullable=True))
    op.add_column(
        "interview_questions",
        sa.Column("should_index", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("interview_questions", "should_index")
    op.drop_column("interview_questions", "technical_categories_json")

    op.drop_column("interview_experiences", "quality_gate_json")
    op.drop_column("interview_experiences", "reliability_json")
    op.drop_column("interview_experiences", "routing_json")
