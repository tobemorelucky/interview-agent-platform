"""add interview_sessions and interview_messages tables

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-22

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("resume_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("memory_summary", sa.Text(), nullable=True),
        sa.Column(
            "turn_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "last_compressed_turn",
            sa.Integer(),
            nullable=False,
            server_default="0",
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_interview_sessions_user_id", "interview_sessions", ["user_id"]
    )
    op.create_index(
        "idx_interview_sessions_status", "interview_sessions", ["status"]
    )

    op.create_table(
        "interview_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "turn_index", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
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
        "idx_interview_messages_session_id",
        "interview_messages",
        ["session_id"],
    )
    op.create_index(
        "idx_interview_messages_turn",
        "interview_messages",
        ["session_id", "turn_index"],
    )


def downgrade() -> None:
    op.drop_index("idx_interview_messages_turn", table_name="interview_messages")
    op.drop_index(
        "idx_interview_messages_session_id", table_name="interview_messages"
    )
    op.drop_table("interview_messages")
    op.drop_index(
        "idx_interview_sessions_status", table_name="interview_sessions"
    )
    op.drop_index(
        "idx_interview_sessions_user_id", table_name="interview_sessions"
    )
    op.drop_table("interview_sessions")
