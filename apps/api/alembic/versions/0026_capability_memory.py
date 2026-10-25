"""Versioned inferred capability memory, no stable profile or historical edits."""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("capability_memories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("exclusions", sa.JSON(), nullable=False),
        sa.Column("correction_note", sa.String(500)),
        sa.Column("projection", sa.JSON(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint("user_id", "topic", name="uq_capability_owner_topic"))
    op.create_index("ix_capability_memories_user_id", "capability_memories", ["user_id"])
    op.create_table("capability_memory_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("memory_id", sa.BigInteger(), sa.ForeignKey("capability_memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(20), nullable=False),
        sa.Column("previous_state", sa.String(30), nullable=False),
        sa.Column("projection", sa.JSON(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("memory_id", "revision", name="uq_capability_version"))
    op.create_index("ix_capability_memory_versions_memory_id", "capability_memory_versions", ["memory_id"])


def downgrade():
    op.drop_table("capability_memory_versions")
    op.drop_table("capability_memories")
