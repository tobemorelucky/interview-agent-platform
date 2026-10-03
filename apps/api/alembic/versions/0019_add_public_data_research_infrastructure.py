"""add public data research infrastructure

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("refresh_task_id", sa.BigInteger(), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("tool_version", sa.String(50), nullable=False),
        sa.Column("provider_name", sa.String(100), nullable=False),
        sa.Column("provider_mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["refresh_task_id"],
            ["public_data_refresh_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_tool_invocations_refresh_task",
        "tool_invocations",
        ["refresh_task_id", "created_at"],
    )
    op.create_index(
        "idx_tool_invocations_tool",
        "tool_invocations",
        ["tool_name", "created_at"],
    )
    op.create_index(
        "idx_tool_invocations_status", "tool_invocations", ["status"]
    )

    op.add_column(
        "sources",
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.add_column(
        "source_snapshots",
        sa.Column(
            "created_by_tool_invocation_id", sa.BigInteger(), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_source_snapshots_tool_invocation",
        "source_snapshots",
        "tool_invocations",
        ["created_by_tool_invocation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_source_snapshots_tool_invocation",
        "source_snapshots",
        ["created_by_tool_invocation_id"],
    )

    op.add_column(
        "experience_collection_tasks",
        sa.Column("public_refresh_task_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_experience_collection_tasks_public_refresh",
        "experience_collection_tasks",
        "public_data_refresh_tasks",
        ["public_refresh_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_experience_collection_tasks_public_refresh",
        "experience_collection_tasks",
        ["public_refresh_task_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_experience_collection_tasks_public_refresh",
        "experience_collection_tasks",
        type_="unique",
    )
    op.drop_constraint(
        "fk_experience_collection_tasks_public_refresh",
        "experience_collection_tasks",
        type_="foreignkey",
    )
    op.drop_column("experience_collection_tasks", "public_refresh_task_id")

    op.drop_index(
        "idx_source_snapshots_tool_invocation", table_name="source_snapshots"
    )
    op.drop_constraint(
        "fk_source_snapshots_tool_invocation",
        "source_snapshots",
        type_="foreignkey",
    )
    op.drop_column("source_snapshots", "created_by_tool_invocation_id")
    op.drop_column("sources", "metadata_json")

    op.drop_index("idx_tool_invocations_status", table_name="tool_invocations")
    op.drop_index("idx_tool_invocations_tool", table_name="tool_invocations")
    op.drop_index(
        "idx_tool_invocations_refresh_task", table_name="tool_invocations"
    )
    op.drop_table("tool_invocations")
