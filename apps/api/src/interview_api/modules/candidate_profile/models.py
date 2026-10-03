from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from interview_api.infrastructure.db.base import Base


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), unique=True)
    current_version_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT", use_alter=True, name="fk_profile_current_version"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CandidateProfileVersion(Base):
    __tablename__ = "candidate_profile_versions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    candidate_profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    base_version_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT"))
    source_resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id", ondelete="RESTRICT"), index=True)
    generation_key: Mapped[str] = mapped_column(String(64), unique=True)
    summary: Mapped[str | None] = mapped_column(Text)
    unknowns: Mapped[list] = mapped_column(JSON)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    producer: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("experience_agent_runs.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("candidate_profile_id", "version_number", name="uq_candidate_profile_version"),)


class ResumeEvidence(Base):
    __tablename__ = "resume_evidence"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id", ondelete="RESTRICT"), index=True)
    evidence_key: Mapped[str] = mapped_column(String(80), unique=True)
    section: Mapped[str] = mapped_column(String(100))
    page: Mapped[int | None] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    excerpt: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserConfirmedEvidence(Base):
    __tablename__ = "user_confirmed_evidence"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    candidate_profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="RESTRICT"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    evidence_key: Mapped[str] = mapped_column(String(80), unique=True)
    statement: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CandidateFact(Base):
    """Common provenance anchor, including summary and strengths."""
    __tablename__ = "candidate_facts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT"), index=True)
    field_path: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String(32))
    __table_args__ = (UniqueConstraint("version_id", "field_path", name="uq_candidate_fact_path"),)


class CandidateFactEvidence(Base):
    __tablename__ = "candidate_fact_evidence"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("candidate_facts.id", ondelete="RESTRICT"), index=True)
    resume_evidence_id: Mapped[int | None] = mapped_column(ForeignKey("resume_evidence.id", ondelete="RESTRICT"))
    user_evidence_id: Mapped[int | None] = mapped_column(ForeignKey("user_confirmed_evidence.id", ondelete="RESTRICT"))
    __table_args__ = (CheckConstraint("(resume_evidence_id IS NULL) <> (user_evidence_id IS NULL)", name="ck_candidate_evidence_one_source"),)


class ProjectExperience(Base):
    __tablename__ = "candidate_project_experiences"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT"), index=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("candidate_facts.id", ondelete="RESTRICT"), unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)


class WorkExperience(Base):
    __tablename__ = "candidate_work_experiences"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT"), index=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("candidate_facts.id", ondelete="RESTRICT"), unique=True)
    employer: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    experience_type: Mapped[str] = mapped_column(String(20))


class EducationExperience(Base):
    __tablename__ = "candidate_education_experiences"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT"), index=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("candidate_facts.id", ondelete="RESTRICT"), unique=True)
    institution: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)


class UserSkill(Base):
    """Stable resume/self-reported facts, never dynamic interview scores."""
    __tablename__ = "candidate_user_skills"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT"), index=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("candidate_facts.id", ondelete="RESTRICT"), unique=True)
    name: Mapped[str] = mapped_column(Text)
    evidence_state: Mapped[str] = mapped_column(String(32))
    __table_args__ = (CheckConstraint("evidence_state IN ('MENTIONED', 'DEMONSTRATED', 'STRONG_EVIDENCE')", name="ck_candidate_skill_state"),)


class ProfileCorrection(Base):
    __tablename__ = "candidate_profile_corrections"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    base_version_id: Mapped[int] = mapped_column(ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT"))
    new_version_id: Mapped[int] = mapped_column(ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT"), unique=True)
    field_path: Mapped[str] = mapped_column(String(200))
    old_value: Mapped[object] = mapped_column(JSON)
    new_value: Mapped[object] = mapped_column(JSON)
    changed_by: Mapped[str] = mapped_column(String(20), default="user")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProfileGeneration(Base):
    __tablename__ = "candidate_profile_generations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id", ondelete="RESTRICT"), index=True)
    generation_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT"))
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("experience_agent_runs.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
