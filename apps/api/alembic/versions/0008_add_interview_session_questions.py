"""add interview_session_questions table and extend interview_sessions

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-23

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Extend interview_sessions ──
    op.add_column(
        "interview_sessions",
        sa.Column(
            "current_question_index",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "interview_sessions",
        sa.Column(
            "question_generation_status",
            sa.String(20),
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.add_column(
        "interview_sessions",
        sa.Column(
            "question_generation_error",
            sa.Text(),
            nullable=True,
        ),
    )

    # ── Create interview_session_questions ──
    op.create_table(
        "interview_session_questions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "question_index",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("standard_answer", sa.Text(), nullable=True),
        sa.Column("dimension", sa.String(50), nullable=True),
        sa.Column("difficulty", sa.String(20), nullable=True),
        sa.Column(
            "source",
            sa.String(30),
            nullable=False,
            server_default="LLM_GENERATED",
        ),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column(
            "follow_up_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_questions_session_id",
        "interview_session_questions",
        ["session_id"],
    )
    op.create_index(
        "idx_questions_session_index",
        "interview_session_questions",
        ["session_id", "question_index"],
        unique=True,
    )
    op.create_index(
        "idx_questions_status",
        "interview_session_questions",
        ["session_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_questions_status", table_name="interview_session_questions")
    op.drop_index(
        "idx_questions_session_index", table_name="interview_session_questions"
    )
    op.drop_index(
        "idx_questions_session_id", table_name="interview_session_questions"
    )
    op.drop_table("interview_session_questions")
    op.drop_column("interview_sessions", "question_generation_error")
    op.drop_column("interview_sessions", "question_generation_status")
    op.drop_column("interview_sessions", "current_question_index")
