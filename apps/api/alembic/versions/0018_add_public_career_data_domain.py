"""add public career data domain

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("official_domain", sa.String(253), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "normalized_name", name="uq_companies_normalized_name"
        ),
    )
    op.create_index("idx_companies_status", "companies", ["status"])

    op.create_table(
        "company_aliases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("normalized_alias", sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "normalized_alias",
            name="uq_company_aliases_company_alias",
        ),
    )
    op.create_index(
        "idx_company_aliases_normalized_alias",
        "company_aliases",
        ["normalized_alias"],
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("canonical_title", sa.String(255), nullable=False),
        sa.Column("normalized_title", sa.String(255), nullable=False),
        sa.Column("job_family", sa.String(128), nullable=True),
        sa.Column("campus_type", sa.String(64), nullable=True),
        sa.Column("level", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "normalized_title", name="uq_jobs_company_title"
        ),
    )
    op.create_index("idx_jobs_company_id", "jobs", ["company_id"])
    op.create_index("idx_jobs_status", "jobs", ["status"])

    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url_hash", name="uq_sources_url_hash"),
    )
    op.create_index("idx_sources_domain", "sources", ["domain"])
    op.create_index("idx_sources_source_type", "sources", ["source_type"])

    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("normalized_content", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "content_hash",
            name="uq_source_snapshots_source_content",
        ),
    )
    op.create_index(
        "idx_source_snapshots_content_hash", "source_snapshots", ["content_hash"]
    )
    op.create_index(
        "idx_source_snapshots_source_fetched",
        "source_snapshots",
        ["source_id", "fetched_at"],
    )

    op.create_table(
        "claims",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("claim_key", sa.String(128), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_key", name="uq_claims_claim_key"),
    )
    op.create_index("idx_claims_category", "claims", ["category"])

    op.create_table(
        "claim_evidence",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("claim_id", sa.BigInteger(), nullable=False),
        sa.Column("source_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("evidence_key", sa.String(128), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column(
            "source_location_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "support_type", sa.String(32), nullable=False, server_default="DIRECT"
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["source_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "claim_id", "evidence_key", name="uq_claim_evidence_claim_key"
        ),
    )
    op.create_index(
        "idx_claim_evidence_snapshot", "claim_evidence", ["source_snapshot_id"]
    )

    op.create_table(
        "job_description_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("structured_data_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["source_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "version_number",
            name="uq_job_description_versions_job_version",
        ),
        sa.UniqueConstraint(
            "job_id",
            "content_fingerprint",
            name="uq_job_description_versions_job_fingerprint",
        ),
    )
    op.create_index(
        "idx_job_description_versions_job_id", "job_description_versions", ["job_id"]
    )
    op.create_index(
        "idx_job_description_versions_source_snapshot",
        "job_description_versions",
        ["source_snapshot_id"],
    )

    op.create_table(
        "job_requirements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_description_version_id", sa.BigInteger(), nullable=False),
        sa.Column("requirement_type", sa.String(64), nullable=True),
        sa.Column("normalized_value", sa.String(255), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("importance", sa.String(32), nullable=True),
        sa.Column("claim_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_description_version_id"],
            ["job_description_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_job_requirements_description_version",
        "job_requirements",
        ["job_description_version_id"],
    )
    op.create_index(
        "idx_job_requirements_claim_id", "job_requirements", ["claim_id"]
    )

    op.create_table(
        "job_tags",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("tag", sa.String(128), nullable=False),
        sa.Column("normalized_tag", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id", "normalized_tag", name="uq_job_tags_job_tag"
        ),
    )
    op.create_index(
        "idx_job_tags_normalized_tag", "job_tags", ["normalized_tag"]
    )

    op.create_table(
        "public_data_refresh_tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_ref", sa.String(255), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("freshness_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_public_data_refresh_tasks_idempotency_key"
        ),
    )
    op.create_index(
        "idx_public_data_refresh_tasks_status",
        "public_data_refresh_tasks",
        ["status"],
    )
    op.create_index(
        "idx_public_data_refresh_tasks_target",
        "public_data_refresh_tasks",
        ["target_type", "target_ref"],
    )

    op.add_column(
        "interview_experiences", sa.Column("company_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "interview_experiences", sa.Column("job_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "interview_experiences",
        sa.Column("content_fingerprint", sa.String(64), nullable=True),
    )
    op.create_foreign_key(
        "fk_interview_experiences_company_id",
        "interview_experiences",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_interview_experiences_job_id",
        "interview_experiences",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_interview_experiences_company_id",
        "interview_experiences",
        ["company_id"],
    )
    op.create_index(
        "idx_interview_experiences_job_id", "interview_experiences", ["job_id"]
    )
    op.create_index(
        "idx_interview_experiences_content_fingerprint",
        "interview_experiences",
        ["content_fingerprint"],
    )

    op.add_column(
        "interview_questions", sa.Column("company_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "interview_questions", sa.Column("job_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_interview_questions_company_id",
        "interview_questions",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_interview_questions_job_id",
        "interview_questions",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_interview_questions_company_id", "interview_questions", ["company_id"]
    )
    op.create_index(
        "idx_interview_questions_job_id", "interview_questions", ["job_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_interview_questions_job_id", table_name="interview_questions")
    op.drop_index(
        "idx_interview_questions_company_id", table_name="interview_questions"
    )
    op.drop_constraint(
        "fk_interview_questions_job_id", "interview_questions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_interview_questions_company_id",
        "interview_questions",
        type_="foreignkey",
    )
    op.drop_column("interview_questions", "job_id")
    op.drop_column("interview_questions", "company_id")

    op.drop_index(
        "idx_interview_experiences_content_fingerprint",
        table_name="interview_experiences",
    )
    op.drop_index(
        "idx_interview_experiences_job_id", table_name="interview_experiences"
    )
    op.drop_index(
        "idx_interview_experiences_company_id", table_name="interview_experiences"
    )
    op.drop_constraint(
        "fk_interview_experiences_job_id",
        "interview_experiences",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_interview_experiences_company_id",
        "interview_experiences",
        type_="foreignkey",
    )
    op.drop_column("interview_experiences", "content_fingerprint")
    op.drop_column("interview_experiences", "job_id")
    op.drop_column("interview_experiences", "company_id")

    op.drop_table("public_data_refresh_tasks")
    op.drop_table("job_tags")
    op.drop_table("job_requirements")
    op.drop_table("job_description_versions")
    op.drop_table("claim_evidence")
    op.drop_table("claims")
    op.drop_table("source_snapshots")
    op.drop_table("sources")
    op.drop_table("jobs")
    op.drop_table("company_aliases")
    op.drop_table("companies")

