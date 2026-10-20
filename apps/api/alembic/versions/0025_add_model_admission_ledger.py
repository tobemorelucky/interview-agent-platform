"""Add durable model admission ledger; no historical business data changes."""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("model_budget_owners",
        sa.Column("owner_key", sa.String(80), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), unique=True))
    op.create_table("model_admissions",
        sa.Column("call_key", sa.String(64), primary_key=True),
        sa.Column("owner_key", sa.String(80), sa.ForeignKey("model_budget_owners.owner_key", ondelete="RESTRICT"), nullable=False),
        sa.Column("route", sa.String(100), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("charged_tokens", sa.Integer(), nullable=False),
        sa.Column("actual_tokens", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('RESERVED','SETTLED','UNKNOWN')", name="ck_model_admission_status"),
        sa.CheckConstraint("reserved_tokens > 0 AND charged_tokens >= 0", name="ck_model_admission_tokens"))
    op.create_index("ix_model_admissions_owner_key", "model_admissions", ["owner_key"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM model_admissions)")).scalar():
        raise RuntimeError("Refusing to discard model admission audit; archive it before a controlled downgrade")
    op.drop_table("model_admissions")
    op.drop_table("model_budget_owners")
