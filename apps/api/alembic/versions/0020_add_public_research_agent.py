"""Minimal generalization of existing experience audit and public provenance.

Revision ID: 0020
Revises: 0019
"""

from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("experience_agent_runs", "source_item_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("experience_agent_runs", sa.Column("refresh_task_id", sa.BigInteger(), nullable=True))
    op.add_column("experience_agent_runs", sa.Column("agent_role", sa.String(100), nullable=True))
    op.create_foreign_key("fk_experience_agent_runs_refresh", "experience_agent_runs", "public_data_refresh_tasks", ["refresh_task_id"], ["id"], ondelete="RESTRICT")
    op.create_index("idx_experience_agent_runs_refresh_task", "experience_agent_runs", ["refresh_task_id"])
    for name, size in (("provider_name", 100), ("prompt_name", 100), ("prompt_version", 50)):
        op.add_column("experience_agent_step_runs", sa.Column(name, sa.String(size), nullable=True))
    op.add_column("experience_agent_step_runs", sa.Column("structured_retry", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("job_description_versions", sa.Column("publication_status", sa.String(32), nullable=False, server_default="WAITING_REVIEW"))
    for table, column, target in (
        ("interview_experiences", "source_snapshot_id", "source_snapshots"),
        ("interview_experiences", "claim_id", "claims"),
        ("interview_questions", "claim_id", "claims"),
    ):
        op.add_column(table, sa.Column(column, sa.BigInteger(), nullable=True))
        op.create_foreign_key(f"fk_{table}_{column}", table, target, [column], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    # Restoring NOT NULL cannot represent public-only audits. Refuse rather
    # than silently deleting traces; operators must export/archive them first.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM experience_agent_runs WHERE source_item_id IS NULL")):
        raise RuntimeError("0020 downgrade requires archiving public-only agent audits first")
    for table, column in (
        ("interview_questions", "claim_id"),
        ("interview_experiences", "claim_id"),
        ("interview_experiences", "source_snapshot_id"),
    ):
        op.drop_constraint(f"fk_{table}_{column}", table, type_="foreignkey")
        op.drop_column(table, column)
    op.drop_column("job_description_versions", "publication_status")
    for name in ("structured_retry", "prompt_version", "prompt_name", "provider_name"):
        op.drop_column("experience_agent_step_runs", name)
    op.drop_index("idx_experience_agent_runs_refresh_task", table_name="experience_agent_runs")
    op.drop_constraint("fk_experience_agent_runs_refresh", "experience_agent_runs", type_="foreignkey")
    op.drop_column("experience_agent_runs", "agent_role")
    op.drop_column("experience_agent_runs", "refresh_task_id")
    op.alter_column("experience_agent_runs", "source_item_id", existing_type=sa.BigInteger(), nullable=False)
