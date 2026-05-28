"""add experience collection tables (Phase 4 DB foundation)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. experience_keyword_presets ──
    op.create_table(
        "experience_keyword_presets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("preset_type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("preset_type", "name", name="uq_experience_keyword_presets_type_name"),
    )
    op.create_index("idx_experience_keyword_presets_type", "experience_keyword_presets", ["preset_type"])
    op.create_index("idx_experience_keyword_presets_enabled", "experience_keyword_presets", ["enabled"])

    # ── 2. experience_collection_tasks ──
    op.create_table(
        "experience_collection_tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("time_window_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("job_keywords_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("company_keywords_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("platforms_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("max_results", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("review_mode", sa.String(32), nullable=False, server_default="MANUAL"),
        sa.Column("write_to_question_db", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("write_to_vector_index", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("update_public_summary", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("found_url_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extracted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_experience_collection_tasks_status", "experience_collection_tasks", ["status"])
    op.create_index("idx_experience_collection_tasks_created_at", "experience_collection_tasks", ["created_at"])
    op.create_index("idx_experience_collection_tasks_created_by", "experience_collection_tasks", ["created_by"])

    # ── 3. experience_source_items ──
    op.create_table(
        "experience_source_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("normalized_url_hash", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(64), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("author_name", sa.String(128), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at_confidence", sa.String(32), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("raw_storage_key", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("fetch_status", sa.String(32), nullable=False, server_default="DISCOVERED"),
        sa.Column("extract_status", sa.String(32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["task_id"], ["experience_collection_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "normalized_url_hash", name="uq_source_items_task_url"),
    )
    op.create_index("idx_experience_source_items_task_id", "experience_source_items", ["task_id"])
    op.create_index("idx_experience_source_items_fetch_status", "experience_source_items", ["fetch_status"])
    op.create_index("idx_experience_source_items_extract_status", "experience_source_items", ["extract_status"])
    op.create_index("idx_experience_source_items_platform", "experience_source_items", ["platform"])
    op.create_index("idx_experience_source_items_content_hash", "experience_source_items", ["content_hash"])

    # ── 4. interview_experiences ──
    op.create_table(
        "interview_experiences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_item_id", sa.BigInteger(), nullable=True),
        sa.Column("company", sa.String(128), nullable=True),
        sa.Column("position", sa.String(128), nullable=True),
        sa.Column("job_direction", sa.String(128), nullable=True),
        sa.Column("interview_round", sa.String(64), nullable=True),
        sa.Column("experience_date", sa.String(64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("reliability_score", sa.Float(), nullable=True),
        sa.Column("reliability_level", sa.String(32), nullable=True),
        sa.Column("quality_flags_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("review_status", sa.String(32), nullable=False, server_default="PENDING"),
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
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_item_id"], ["experience_source_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_interview_experiences_company", "interview_experiences", ["company"])
    op.create_index("idx_interview_experiences_position", "interview_experiences", ["position"])
    op.create_index("idx_interview_experiences_review_status", "interview_experiences", ["review_status"])
    op.create_index("idx_interview_experiences_reliability_score", "interview_experiences", ["reliability_score"])
    op.create_index("idx_interview_experiences_created_at", "interview_experiences", ["created_at"])

    # ── 5. interview_questions ──
    op.create_table(
        "interview_questions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("experience_id", sa.BigInteger(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("standard_answer", sa.Text(), nullable=True),
        sa.Column("answer_source", sa.String(32), nullable=False, server_default="LLM_GENERATED"),
        sa.Column("category", sa.String(128), nullable=True),
        sa.Column("difficulty", sa.String(32), nullable=True),
        sa.Column("company", sa.String(128), nullable=True),
        sa.Column("position", sa.String(128), nullable=True),
        sa.Column("interview_round", sa.String(64), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("reliability_score", sa.Float(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("routing_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("review_status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("index_status", sa.String(32), nullable=False, server_default="NOT_INDEXED"),
        sa.Column("question_fingerprint", sa.String(64), nullable=True),
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
        sa.ForeignKeyConstraint(["experience_id"], ["interview_experiences.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_interview_questions_experience_id", "interview_questions", ["experience_id"])
    op.create_index("idx_interview_questions_category", "interview_questions", ["category"])
    op.create_index("idx_interview_questions_company", "interview_questions", ["company"])
    op.create_index("idx_interview_questions_position", "interview_questions", ["position"])
    op.create_index("idx_interview_questions_review_status", "interview_questions", ["review_status"])
    op.create_index("idx_interview_questions_index_status", "interview_questions", ["index_status"])
    op.create_index("idx_interview_questions_fingerprint", "interview_questions", ["question_fingerprint"])
    op.create_index("idx_interview_questions_created_at", "interview_questions", ["created_at"])

    # ── 6. experience_recent_summaries ──
    op.create_table(
        "experience_recent_summaries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("time_window_hours", sa.Integer(), nullable=False),
        sa.Column("job_keywords_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("company_keywords_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("summary_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("generated_by_task_id", sa.BigInteger(), nullable=True),
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
        sa.ForeignKeyConstraint(["generated_by_task_id"], ["experience_collection_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_experience_recent_summaries_time_window", "experience_recent_summaries", ["time_window_hours"])
    op.create_index("idx_experience_recent_summaries_created_at", "experience_recent_summaries", ["created_at"])


def downgrade() -> None:
    op.drop_table("experience_recent_summaries")
    op.drop_table("interview_questions")
    op.drop_table("interview_experiences")
    op.drop_table("experience_source_items")
    op.drop_table("experience_collection_tasks")
    op.drop_table("experience_keyword_presets")
