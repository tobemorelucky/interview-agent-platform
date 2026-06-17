"""add user memory foundation tables

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_memory_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("memory_type", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False, server_default="INTERVIEW"),
        sa.Column("key", sa.String(200), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("source_type", sa.String(64), nullable=True),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="PRIVATE"),
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
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_user_memory_items_user_id", "user_memory_items", ["user_id"])
    op.create_index("idx_user_memory_items_user_type", "user_memory_items", ["user_id", "memory_type"])
    op.create_index("idx_user_memory_items_user_scope", "user_memory_items", ["user_id", "scope"])
    op.create_index("idx_user_memory_items_user_status", "user_memory_items", ["user_id", "status"])
    op.create_index("idx_user_memory_items_user_key", "user_memory_items", ["user_id", "key"])

    op.create_table(
        "user_skill_profiles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("skill_name", sa.String(100), nullable=False),
        sa.Column("skill_category", sa.String(100), nullable=True),
        sa.Column("level_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weakness_summary", sa.Text(), nullable=True),
        sa.Column("strength_summary", sa.Text(), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "skill_name", name="uq_user_skill_profiles_user_skill"),
    )
    op.create_index("idx_user_skill_profiles_user_id", "user_skill_profiles", ["user_id"])
    op.create_index("idx_user_skill_profiles_category", "user_skill_profiles", ["skill_category"])

    op.create_table(
        "user_memory_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("memory_item_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False, server_default="SYSTEM"),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["memory_item_id"], ["user_memory_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_user_memory_events_user_id", "user_memory_events", ["user_id"])
    op.create_index("idx_user_memory_events_memory_item_id", "user_memory_events", ["memory_item_id"])
    op.create_index("idx_user_memory_events_event_type", "user_memory_events", ["event_type"])


def downgrade() -> None:
    op.drop_table("user_memory_events")
    op.drop_table("user_skill_profiles")
    op.drop_table("user_memory_items")
