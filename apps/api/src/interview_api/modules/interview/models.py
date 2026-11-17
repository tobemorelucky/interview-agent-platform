from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    CheckConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from interview_api.infrastructure.db.base import Base


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workflow_mode: Mapped[str] = mapped_column(String(20), default="legacy", server_default="legacy")
    context_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("interview_context_snapshots.id", use_alter=True, name="fk_interview_snapshot", ondelete="RESTRICT"))
    active_question_id: Mapped[int | None] = mapped_column(ForeignKey("interview_session_questions.id", use_alter=True, name="fk_interview_active_question", ondelete="SET NULL"))
    state_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completion_json: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    resume_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE", server_default="ACTIVE"
    )
    current_question_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    question_generation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", server_default="PENDING"
    )
    question_generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_compressed_turn: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    target_position: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_position_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    interview_mode: Mapped[str] = mapped_column(
        String(50), nullable=False, default="comprehensive", server_default="comprehensive"
    )
    interview_plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    planner_trace_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    question_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20, server_default="20"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InterviewSessionQuestion(Base):
    __tablename__ = "interview_session_questions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_question_id: Mapped[int | None] = mapped_column(ForeignKey("interview_questions.id", ondelete="RESTRICT"))
    job_requirement_id: Mapped[int | None] = mapped_column(ForeignKey("job_requirements.id", ondelete="RESTRICT"))
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))
    candidate_refs_json: Mapped[dict | None] = mapped_column(JSON)
    question_kind: Mapped[str | None] = mapped_column(String(20))
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    standard_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    dimension: Mapped[str | None] = mapped_column(String(50), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="LLM_GENERATED", server_default="LLM_GENERATED"
    )
    evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    follow_up_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    parent_question_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("interview_session_questions.id", ondelete="SET NULL"), nullable=True
    )
    is_dynamic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    planned_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_points_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evaluation_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", server_default="PENDING"
    )
    # Explicit, reversible-by-record exclusion. A question is never deleted; its
    # reason says why it is out of every technical aggregate. NULL = counted.
    excluded_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InterviewMessage(Base):
    __tablename__ = "interview_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InterviewContextSnapshot(Base):
    __tablename__ = "interview_context_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    interview_session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"), unique=True)
    candidate_profile_version_id: Mapped[int] = mapped_column(ForeignKey("candidate_profile_versions.id", ondelete="RESTRICT"))
    # Exactly one source: the public company/job/JD triple, or a private user
    # target. A private paste never fabricates a public company/job/JD row.
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True)
    job_description_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_description_versions.id", ondelete="RESTRICT"), nullable=True)
    job_target_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_job_targets.id", ondelete="RESTRICT"), nullable=True)
    context_json: Mapped[dict] = mapped_column(JSON)
    content_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InterviewAnswer(Base):
    __tablename__ = "interview_answers"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"))
    session_question_id: Mapped[int] = mapped_column(ForeignKey("interview_session_questions.id", ondelete="CASCADE"), unique=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("interview_messages.id", ondelete="CASCADE"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    result_events: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("session_id", "idempotency_key", name="uq_interview_answer_key"),)


class InterviewEvaluation(Base):
    __tablename__ = "interview_evaluations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"))
    session_question_id: Mapped[int] = mapped_column(ForeignKey("interview_session_questions.id", ondelete="CASCADE"))
    answer_id: Mapped[int] = mapped_column(ForeignKey("interview_answers.id", ondelete="CASCADE"), unique=True)
    artifact_json: Mapped[dict] = mapped_column(JSON)
    technical_correctness: Mapped[int] = mapped_column(Integer)
    coverage: Mapped[int] = mapped_column(Integer)
    depth: Mapped[int] = mapped_column(Integer)
    relevance: Mapped[int] = mapped_column(Integer)
    communication: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(50))
    structured_retry: Mapped[int] = mapped_column(Integer)
    token_usage_json: Mapped[dict] = mapped_column(JSON)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("experience_agent_runs.id", ondelete="RESTRICT"))
    # A stored evaluation can be excluded from technical statistics without
    # rewriting its scores or the model artifact that produced them.
    excluded_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = tuple(CheckConstraint(f"{key} BETWEEN 1 AND 5", name=f"ck_interview_eval_{key}") for key in ("technical_correctness", "coverage", "depth", "relevance", "communication"))


class InterviewAgentCall(Base):
    __tablename__ = "interview_agent_calls"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"))
    answer_id: Mapped[int | None] = mapped_column(ForeignKey("interview_answers.id", ondelete="CASCADE"))
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("experience_agent_runs.id", ondelete="RESTRICT"), unique=True)
