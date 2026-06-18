"""add audit logs

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_role", sa.String(length=50), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=True),
        sa.Column("resource_id", sa.String(length=100), nullable=True),
        sa.Column("ip_address", sa.String(length=100), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="SUCCESS"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("idx_audit_logs_action", "audit_logs", ["action"])
    op.create_index("idx_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"])
    op.create_index("idx_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("idx_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("idx_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("idx_audit_logs_resource", table_name="audit_logs")
    op.drop_index("idx_audit_logs_action", table_name="audit_logs")
    op.drop_index("idx_audit_logs_actor_user_id", table_name="audit_logs")
    op.drop_table("audit_logs")
