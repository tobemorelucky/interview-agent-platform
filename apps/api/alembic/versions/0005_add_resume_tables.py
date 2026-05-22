"""add resumes and resume_reports tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-22

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resumes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=True),
        sa.Column("file_type", sa.String(10), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="UPLOADED",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("task_id", sa.String(64), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column(
            "processing_started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "processing_finished_at", sa.DateTime(timezone=True), nullable=True
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_resumes_user_id", "resumes", ["user_id"])
    op.create_index("idx_resumes_status", "resumes", ["status"])

    op.create_table(
        "resume_reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("resume_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("retrieval_queries_json", sa.JSON(), nullable=True),
        sa.Column("retrieved_context_json", sa.JSON(), nullable=True),
        sa.Column("questions_json", sa.JSON(), nullable=True),
        sa.Column("suggestions_json", sa.JSON(), nullable=True),
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
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resume_id"),
    )
    op.create_index("idx_resume_reports_user_id", "resume_reports", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_resume_reports_user_id", table_name="resume_reports")
    op.drop_table("resume_reports")
    op.drop_index("idx_resumes_status", table_name="resumes")
    op.drop_index("idx_resumes_user_id", table_name="resumes")
    op.drop_table("resumes")
