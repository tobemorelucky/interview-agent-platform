"""ORM models for shared public company, job, source, and evidence data."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from interview_api.infrastructure.db.base import Base, TimestampMixin
from interview_api.modules.public_career.fingerprints import (
    compute_content_fingerprint,
    normalize_text,
)


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    official_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", server_default="ACTIVE"
    )

    aliases: Mapped[list[CompanyAlias]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[Job]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("normalized_name", name="uq_companies_normalized_name"),
        Index("idx_companies_status", "status"),
    )

    @validates("canonical_name")
    def _normalize_canonical_name(self, _key: str, value: str) -> str:
        self.normalized_name = normalize_text(value)
        return value.strip()


class CompanyAlias(Base):
    __tablename__ = "company_aliases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)

    company: Mapped[Company] = relationship(back_populates="aliases")

    __table_args__ = (
        UniqueConstraint(
            "company_id", "normalized_alias", name="uq_company_aliases_company_alias"
        ),
        Index("idx_company_aliases_normalized_alias", "normalized_alias"),
    )

    @validates("alias")
    def _normalize_alias(self, _key: str, value: str) -> str:
        self.normalized_alias = normalize_text(value)
        return value.strip()


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    canonical_title: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(255), nullable=False)
    job_family: Mapped[str | None] = mapped_column(String(128), nullable=True)
    campus_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    level: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", server_default="ACTIVE"
    )

    company: Mapped[Company] = relationship(back_populates="jobs")
    description_versions: Mapped[list[JobDescriptionVersion]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    tags: Mapped[list[JobTag]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id", "normalized_title", name="uq_jobs_company_title"
        ),
        Index("idx_jobs_company_id", "company_id"),
        Index("idx_jobs_status", "status"),
    )

    @validates("canonical_title")
    def _normalize_canonical_title(self, _key: str, value: str) -> str:
        self.normalized_title = normalize_text(value)
        return value.strip()


class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    snapshots: Mapped[list[SourceSnapshot]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_sources_url_hash"),
        Index("idx_sources_domain", "domain"),
        Index("idx_sources_source_type", "source_type"),
    )


class SourceSnapshot(TimestampMixin, Base):
    __tablename__ = "source_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    created_by_tool_invocation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("tool_invocations.id", ondelete="SET NULL"),
        nullable=True,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    source: Mapped[Source] = relationship(back_populates="snapshots")
    claim_evidence: Mapped[list[ClaimEvidence]] = relationship(
        back_populates="source_snapshot"
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id", "content_hash", name="uq_source_snapshots_source_content"
        ),
        Index("idx_source_snapshots_content_hash", "content_hash"),
        Index("idx_source_snapshots_source_fetched", "source_id", "fetched_at"),
        Index(
            "idx_source_snapshots_tool_invocation",
            "created_by_tool_invocation_id",
        ),
    )


class Claim(TimestampMixin, Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_key: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", server_default="ACTIVE"
    )

    evidence: Mapped[list[ClaimEvidence]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )
    requirements: Mapped[list[JobRequirement]] = relationship(back_populates="claim")

    __table_args__ = (
        UniqueConstraint("claim_key", name="uq_claims_claim_key"),
        Index("idx_claims_category", "category"),
    )


class ClaimEvidence(TimestampMixin, Base):
    __tablename__ = "claim_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    source_snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_key: Mapped[str] = mapped_column(String(128), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    source_location_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    support_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="DIRECT", server_default="DIRECT"
    )

    claim: Mapped[Claim] = relationship(back_populates="evidence")
    source_snapshot: Mapped[SourceSnapshot] = relationship(
        back_populates="claim_evidence"
    )

    __table_args__ = (
        UniqueConstraint(
            "claim_id", "evidence_key", name="uq_claim_evidence_claim_key"
        ),
        Index("idx_claim_evidence_snapshot", "source_snapshot_id"),
    )


class JobDescriptionVersion(TimestampMixin, Base):
    __tablename__ = "job_description_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("source_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    publication_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="WAITING_REVIEW", server_default="WAITING_REVIEW"
    )

    job: Mapped[Job] = relationship(back_populates="description_versions")
    source_snapshot: Mapped[SourceSnapshot | None] = relationship()
    requirements: Mapped[list[JobRequirement]] = relationship(
        back_populates="job_description_version", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "job_id", "version_number", name="uq_job_description_versions_job_version"
        ),
        UniqueConstraint(
            "job_id",
            "content_fingerprint",
            name="uq_job_description_versions_job_fingerprint",
        ),
        Index("idx_job_description_versions_job_id", "job_id"),
        Index(
            "idx_job_description_versions_source_snapshot", "source_snapshot_id"
        ),
    )

    @validates("raw_text")
    def _fingerprint_raw_text(self, _key: str, value: str) -> str:
        self.content_fingerprint = compute_content_fingerprint(value)
        return value


class JobRequirement(Base):
    __tablename__ = "job_requirements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_description_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("job_description_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    requirement_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[str | None] = mapped_column(String(32), nullable=True)
    claim_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )

    job_description_version: Mapped[JobDescriptionVersion] = relationship(
        back_populates="requirements"
    )
    claim: Mapped[Claim | None] = relationship(back_populates="requirements")

    __table_args__ = (
        Index(
            "idx_job_requirements_description_version",
            "job_description_version_id",
        ),
        Index("idx_job_requirements_claim_id", "claim_id"),
    )


class JobTag(Base):
    __tablename__ = "job_tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    tag: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_tag: Mapped[str] = mapped_column(String(128), nullable=False)

    job: Mapped[Job] = relationship(back_populates="tags")

    __table_args__ = (
        UniqueConstraint("job_id", "normalized_tag", name="uq_job_tags_job_tag"),
        Index("idx_job_tags_normalized_tag", "normalized_tag"),
    )

    @validates("tag")
    def _normalize_tag(self, _key: str, value: str) -> str:
        self.normalized_tag = normalize_text(value)
        return value.strip()


class PublicDataRefreshTask(TimestampMixin, Base):
    __tablename__ = "public_data_refresh_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default="PENDING"
    )
    freshness_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_public_data_refresh_tasks_idempotency_key"
        ),
        Index("idx_public_data_refresh_tasks_status", "status"),
        Index(
            "idx_public_data_refresh_tasks_target", "target_type", "target_ref"
        ),
    )
