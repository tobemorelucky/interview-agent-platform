"""Round 23 (L3): persistent personal-data purge requests.

The request row IS the business fence: while it exists (in any status) the
resource is out of every new-business path. No existing table is altered, no
FK is changed, no CASCADE is added -- the smallest possible schema change.

The status machine is deliberately explicit about *partial* work: a request
that has erased database content but not yet removed the MinIO object stays in
CLEANING/FAILED and can never be reported as COMPLETED. Only the independent
verifier may write COMPLETED.
"""
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "purge_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("resource_type", sa.String(length=20), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="REQUESTED"),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("completed_steps", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        # Server-derived object locator ONLY. Never client-supplied, never
        # serialised to the client, never written into logs or diagnostics.
        sa.Column("object_key", sa.String(length=512), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_diagnostic", sa.String(length=300), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("residual_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("fenced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleaning_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verifying_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "resource_type IN ('RESUME','PRIVATE_JD')", name="ck_purge_resource_type"),
        sa.CheckConstraint(
            "status IN ('REQUESTED','FENCED','CLEANING','VERIFYING','COMPLETED','FAILED','BLOCKED')",
            name="ck_purge_status"),
        # One request per resource per owner: repeated purge requests are
        # idempotent. A second user with the same idempotency key gets their
        # own row and can never reach this one.
        sa.UniqueConstraint("user_id", "resource_type", "resource_id", name="uq_purge_user_resource"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_purge_user_idempotency"),
        sa.Index("idx_purge_status", "status"),
        sa.Index("idx_purge_user", "user_id"),
    )


def downgrade():
    op.drop_table("purge_requests")
