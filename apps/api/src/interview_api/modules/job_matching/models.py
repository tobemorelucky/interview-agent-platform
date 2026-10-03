from datetime import datetime
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from interview_api.infrastructure.db.base import Base


class JobMatchRun(Base):
    __tablename__ = "job_match_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    candidate_profile_version_id: Mapped[int] = mapped_column(ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT"))
    request_key: Mapped[str] = mapped_column(String(100))
    policy_version: Mapped[str] = mapped_column(String(50))
    prompt_version: Mapped[str] = mapped_column(String(50))
    input_snapshot_json: Mapped[dict] = mapped_column(JSON)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    coverage_json: Mapped[dict] = mapped_column(JSON)
    producer: Mapped[str] = mapped_column(String(50))
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(200))
    attempt_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("user_id", "request_key", name="uq_job_match_request"),
        CheckConstraint("status IN ('READY','RUNNING','COMPLETED','FAILED')", name="ck_job_match_run_status"))


class JobMatchCandidate(Base):
    __tablename__ = "job_match_candidates"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("job_match_runs.id", ondelete="RESTRICT"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"))
    job_description_version_id: Mapped[int] = mapped_column(ForeignKey("job_description_versions.id", ondelete="RESTRICT"))
    input_json: Mapped[dict] = mapped_column(JSON)
    eligibility_status: Mapped[str] = mapped_column(String(20))
    eligibility_reasons: Mapped[list] = mapped_column(JSON)
    unknown_constraints: Mapped[list] = mapped_column(JSON)
    evidence_matrix_json: Mapped[list] = mapped_column(JSON)
    ranking_components_json: Mapped[dict] = mapped_column(JSON)
    pre_rank: Mapped[int | None] = mapped_column(Integer)
    agent_rank: Mapped[int | None] = mapped_column(Integer)
    final_rank: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("run_id", "job_id", name="uq_job_match_candidate"),
        UniqueConstraint("run_id", "final_rank", name="uq_job_match_final_rank"),
        CheckConstraint("eligibility_status IN ('SATISFIED','NOT_SATISFIED','UNKNOWN')", name="ck_job_match_eligibility"),
        CheckConstraint("eligibility_status != 'NOT_SATISFIED' OR (pre_rank IS NULL AND agent_rank IS NULL AND final_rank IS NULL)", name="ck_job_match_excluded_rank"))


class JobMatchRecommendation(Base):
    __tablename__ = "job_match_recommendations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("job_match_candidates.id", ondelete="RESTRICT"), unique=True)
    fit_band: Mapped[str] = mapped_column(String(32))
    analysis_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("fit_band IN ('STRONG_MATCH','GOOD_MATCH','EXPLORATORY','UNCERTAIN')", name="ck_job_match_fit_band"),)


class JobMatchAttempt(Base):
    __tablename__ = "job_match_attempts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("job_match_runs.id", ondelete="RESTRICT"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("experience_agent_runs.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("run_id", "attempt_number", name="uq_job_match_attempt"),)
