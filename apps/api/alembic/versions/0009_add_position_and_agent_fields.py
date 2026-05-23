"""add target_position, interview_mode, question_count, interview_plan, planner_trace
to interview_sessions; add parent_question_id, is_dynamic, planned_order,
answer_summary, missing_points, evaluation to interview_session_questions

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Extend interview_sessions ──
    op.add_column(
        "interview_sessions",
        sa.Column("target_position", sa.String(100), nullable=True),
    )
    op.add_column(
        "interview_sessions",
        sa.Column(
            "target_position_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "interview_sessions",
        sa.Column(
            "interview_mode",
            sa.String(50),
            nullable=False,
            server_default="comprehensive",
        ),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("interview_plan_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("planner_trace_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "interview_sessions",
        sa.Column(
            "question_count",
            sa.Integer(),
            nullable=False,
            server_default="20",
        ),
    )

    # ── Extend interview_session_questions ──
    op.add_column(
        "interview_session_questions",
        sa.Column("parent_question_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "interview_session_questions",
        sa.Column(
            "is_dynamic",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "interview_session_questions",
        sa.Column("planned_order", sa.Integer(), nullable=True),
    )
    op.add_column(
        "interview_session_questions",
        sa.Column("answer_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "interview_session_questions",
        sa.Column("missing_points_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "interview_session_questions",
        sa.Column("evaluation_json", sa.JSON(), nullable=True),
    )

    # Index for dynamic questions lookup
    op.create_index(
        "idx_questions_parent_id",
        "interview_session_questions",
        ["parent_question_id"],
    )
    op.create_index(
        "idx_questions_is_dynamic",
        "interview_session_questions",
        ["session_id", "is_dynamic"],
    )


def downgrade() -> None:
    op.drop_index("idx_questions_is_dynamic", table_name="interview_session_questions")
    op.drop_index("idx_questions_parent_id", table_name="interview_session_questions")

    op.drop_column("interview_session_questions", "evaluation_json")
    op.drop_column("interview_session_questions", "missing_points_json")
    op.drop_column("interview_session_questions", "answer_summary")
    op.drop_column("interview_session_questions", "planned_order")
    op.drop_column("interview_session_questions", "is_dynamic")
    op.drop_column("interview_session_questions", "parent_question_id")

    op.drop_column("interview_sessions", "question_count")
    op.drop_column("interview_sessions", "planner_trace_json")
    op.drop_column("interview_sessions", "interview_plan_json")
    op.drop_column("interview_sessions", "interview_mode")
    op.drop_column("interview_sessions", "target_position_confirmed")
    op.drop_column("interview_sessions", "target_position")
