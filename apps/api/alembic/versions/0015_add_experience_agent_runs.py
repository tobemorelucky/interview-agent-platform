"""add experience agent run traces

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experience_agent_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_item_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "graph_name",
            sa.String(length=100),
            nullable=False,
            server_default="experience_extraction_graph",
        ),
        sa.Column("graph_version", sa.String(length=50), nullable=False, server_default="v1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="RUNNING"),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["source_item_id"], ["experience_source_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["experience_collection_tasks.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_experience_agent_runs_source_item_id", "experience_agent_runs", ["source_item_id"])
    op.create_index("idx_experience_agent_runs_task_id", "experience_agent_runs", ["task_id"])
    op.create_index("idx_experience_agent_runs_status", "experience_agent_runs", ["status"])
    op.create_index("idx_experience_agent_runs_created_at", "experience_agent_runs", ["created_at"])

    op.create_table(
        "experience_agent_step_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("agent_run_id", sa.BigInteger(), nullable=False),
        sa.Column("step_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("token_usage_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_run_id"], ["experience_agent_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_experience_agent_step_runs_agent_run_id", "experience_agent_step_runs", ["agent_run_id"])
    op.create_index("idx_experience_agent_step_runs_step_name", "experience_agent_step_runs", ["step_name"])
    op.create_index("idx_experience_agent_step_runs_status", "experience_agent_step_runs", ["status"])

    op.add_column("interview_experiences", sa.Column("task_id", sa.BigInteger(), nullable=True))
    op.add_column("interview_experiences", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("interview_experiences", sa.Column("platform", sa.String(length=64), nullable=True))
    op.add_column("interview_experiences", sa.Column("extraction_confidence", sa.Float(), nullable=True))
    op.add_column("interview_experiences", sa.Column("extraction_output_json", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "fk_interview_experiences_task_id",
        "interview_experiences",
        "experience_collection_tasks",
        ["task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_interview_experiences_task_id", "interview_experiences", ["task_id"])

    op.add_column("interview_questions", sa.Column("original_answer", sa.Text(), nullable=True))
    op.add_column("interview_questions", sa.Column("evidence", sa.Text(), nullable=True))
    op.add_column("interview_questions", sa.Column("question_type", sa.String(length=32), nullable=True))
    op.add_column("interview_questions", sa.Column("confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_questions", "confidence")
    op.drop_column("interview_questions", "question_type")
    op.drop_column("interview_questions", "evidence")
    op.drop_column("interview_questions", "original_answer")

    op.drop_index("idx_interview_experiences_task_id", table_name="interview_experiences")
    op.drop_constraint("fk_interview_experiences_task_id", "interview_experiences", type_="foreignkey")
    op.drop_column("interview_experiences", "extraction_output_json")
    op.drop_column("interview_experiences", "extraction_confidence")
    op.drop_column("interview_experiences", "platform")
    op.drop_column("interview_experiences", "source_url")
    op.drop_column("interview_experiences", "task_id")

    op.drop_index("idx_experience_agent_step_runs_status", table_name="experience_agent_step_runs")
    op.drop_index("idx_experience_agent_step_runs_step_name", table_name="experience_agent_step_runs")
    op.drop_index("idx_experience_agent_step_runs_agent_run_id", table_name="experience_agent_step_runs")
    op.drop_table("experience_agent_step_runs")

    op.drop_index("idx_experience_agent_runs_created_at", table_name="experience_agent_runs")
    op.drop_index("idx_experience_agent_runs_status", table_name="experience_agent_runs")
    op.drop_index("idx_experience_agent_runs_task_id", table_name="experience_agent_runs")
    op.drop_index("idx_experience_agent_runs_source_item_id", table_name="experience_agent_runs")
    op.drop_table("experience_agent_runs")
