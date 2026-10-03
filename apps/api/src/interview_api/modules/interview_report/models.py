"""Versioned coaching reports; historical completed content is immutable."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from interview_api.infrastructure.db.base import Base


class FinalInterviewReport(Base):
    __tablename__ = "final_interview_reports"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id", ondelete="RESTRICT"), index=True)
    context_snapshot_id: Mapped[int] = mapped_column(ForeignKey("interview_context_snapshots.id", ondelete="RESTRICT"))
    generation_number: Mapped[int] = mapped_column(Integer)
    generation_key: Mapped[str] = mapped_column(String(64), unique=True)
    evaluation_fingerprint: Mapped[str] = mapped_column(String(64))
    base_current_report_id: Mapped[int | None] = mapped_column(ForeignKey("final_interview_reports.id", ondelete="RESTRICT"))
    generation_status: Mapped[str] = mapped_column(String(20))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    attempt_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    statistics_json: Mapped[dict] = mapped_column(JSON)
    coach_analysis_json: Mapped[dict | None] = mapped_column(JSON)
    producer: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(50))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("session_id", "generation_number", name="uq_final_report_generation"),
        CheckConstraint("NOT is_current OR generation_status = 'COMPLETED'", name="ck_final_report_current_completed"),
        Index("uq_final_report_current", "session_id", unique=True, postgresql_where=text("is_current")),
    )


class InterviewReportAttempt(Base):
    __tablename__ = "interview_report_attempts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("final_interview_reports.id", ondelete="RESTRICT"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("experience_agent_runs.id", ondelete="RESTRICT"))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("report_id", "attempt_number", name="uq_report_attempt_number"),)


class InterviewPerformanceObservation(Base):
    __tablename__ = "interview_performance_observations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id", ondelete="RESTRICT"))
    report_id: Mapped[int] = mapped_column(ForeignKey("final_interview_reports.id", ondelete="RESTRICT"), index=True)
    observation_type: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    supporting_evaluation_ids: Mapped[list] = mapped_column(JSON)
    occurrence_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def content(self):
        return self.text


class PerformanceObservationEvaluation(Base):
    __tablename__ = "performance_observation_evaluations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("interview_performance_observations.id", ondelete="RESTRICT"))
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("interview_evaluations.id", ondelete="RESTRICT"))
    __table_args__ = (UniqueConstraint("observation_id", "evaluation_id", name="uq_observation_evaluation"),)
