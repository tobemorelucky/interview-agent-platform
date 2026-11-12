"""Private user-supplied JD targets; context snapshots accept a private source.

Two changes, both minimal and additive:

1. A new owner-scoped table. It deliberately does not reuse companies / jobs /
   job_description_versions: those names are globally unique and publicly
   reviewed, so a private paste must not occupy them.
2. The interview context snapshot kept three NOT NULL public foreign keys, so a
   private target could only be recorded by inventing a public company/job/JD
   row. They become nullable and the snapshot gains one nullable
   job_target_id, guarded by a CHECK that exactly one source is present.
   Existing rows keep all three values, so nothing is rewritten.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("user_job_targets",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("job_title", sa.String(255), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="USER_SUPPLIED"),
        sa.Column("source_reference", sa.String(500)),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("structured_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("requirements_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("parse_status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("parse_mode", sa.String(20)),
        sa.Column("parse_error", sa.String(500)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "content_fingerprint", name="uq_user_job_targets_owner_content"),
        sa.CheckConstraint("parse_status IN ('DRAFT','PARSED','CONFIRMED','FAILED')",
                           name="ck_user_job_targets_status"),
        sa.CheckConstraint("source_type = 'USER_SUPPLIED'", name="ck_user_job_targets_source"))
    op.create_index("idx_user_job_targets_owner_created", "user_job_targets", ["user_id", "created_at"])

    op.alter_column("interview_context_snapshots", "company_id", existing_type=sa.BigInteger(), nullable=True)
    op.alter_column("interview_context_snapshots", "job_id", existing_type=sa.BigInteger(), nullable=True)
    op.alter_column("interview_context_snapshots", "job_description_version_id",
                    existing_type=sa.BigInteger(), nullable=True)
    op.add_column("interview_context_snapshots",
                  sa.Column("job_target_id", sa.BigInteger(), sa.ForeignKey("user_job_targets.id", ondelete="RESTRICT")))
    op.create_check_constraint("ck_interview_snapshot_source", "interview_context_snapshots",
        "(company_id IS NOT NULL AND job_id IS NOT NULL AND job_description_version_id IS NOT NULL "
        "AND job_target_id IS NULL) OR (company_id IS NULL AND job_id IS NULL "
        "AND job_description_version_id IS NULL AND job_target_id IS NOT NULL)")
    op.create_index("ix_interview_context_snapshots_job_target_id",
                    "interview_context_snapshots", ["job_target_id"])


def downgrade():
    # A private-source snapshot cannot satisfy the restored NOT NULL columns.
    # Refuse instead of silently deleting interview history.
    private = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM interview_context_snapshots WHERE company_id IS NULL")).scalar()
    if private:
        raise RuntimeError(
            f"{private} interview snapshots were created from a private JD target; "
            "export or intentionally delete them before downgrading to 0026")
    op.drop_index("ix_interview_context_snapshots_job_target_id", table_name="interview_context_snapshots")
    op.drop_constraint("ck_interview_snapshot_source", "interview_context_snapshots", type_="check")
    op.drop_column("interview_context_snapshots", "job_target_id")
    op.alter_column("interview_context_snapshots", "job_description_version_id",
                    existing_type=sa.BigInteger(), nullable=False)
    op.alter_column("interview_context_snapshots", "company_id", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column("interview_context_snapshots", "job_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_index("idx_user_job_targets_owner_created", table_name="user_job_targets")
    op.drop_table("user_job_targets")
