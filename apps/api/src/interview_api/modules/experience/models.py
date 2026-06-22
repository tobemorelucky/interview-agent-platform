"""Phase 4: Experience collection ORM models."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from interview_api.infrastructure.db.base import Base


class ExperienceKeywordPreset(Base):
    __tablename__ = "experience_keyword_presets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    preset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    aliases_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("preset_type", "name", name="uq_experience_keyword_presets_type_name"),
    )


class ExperienceCollectionTask(Base):
    __tablename__ = "experience_collection_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    time_window_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    job_keywords_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    company_keywords_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    platforms_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    search_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="JOB", server_default="JOB"
    )
    max_results: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    review_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="MANUAL"
    )
    write_to_question_db: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    write_to_vector_index: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    update_public_summary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING"
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    found_url_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extracted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExperienceSourceItem(Base):
    __tablename__ = "experience_source_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("experience_collection_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    engine: Mapped[str | None] = mapped_column(String(128), nullable=True)
    matched_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    filtered_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetch_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="DISCOVERED"
    )
    extract_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("task_id", "normalized_url_hash", name="uq_source_items_task_url"),
    )


class InterviewExperience(Base):
    __tablename__ = "interview_experiences"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_item_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("experience_source_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("experience_collection_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company: Mapped[str | None] = mapped_column(String(128), nullable=True)
    position: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_direction: Mapped[str | None] = mapped_column(String(128), nullable=True)
    interview_round: Mapped[str | None] = mapped_column(String(64), nullable=True)
    experience_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    reliability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extraction_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality_flags_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InterviewQuestion(Base):
    __tablename__ = "interview_questions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    experience_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("interview_experiences.id", ondelete="CASCADE"),
        nullable=True,
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    original_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    standard_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="LLM_GENERATED"
    )
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(32), nullable=True)
    company: Mapped[str | None] = mapped_column(String(128), nullable=True)
    position: Mapped[str | None] = mapped_column(String(128), nullable=True)
    interview_round: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reliability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    tags_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    routing_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING"
    )
    index_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="NOT_INDEXED"
    )
    question_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ExperienceAgentRun(Base):
    __tablename__ = "experience_agent_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_item_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("experience_source_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("experience_collection_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    graph_name: Mapped[str] = mapped_column(
        String(100), nullable=False, default="experience_extraction_graph"
    )
    graph_version: Mapped[str] = mapped_column(String(50), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_experience_agent_runs_source_item_id", "source_item_id"),
        Index("idx_experience_agent_runs_task_id", "task_id"),
        Index("idx_experience_agent_runs_status", "status"),
        Index("idx_experience_agent_runs_created_at", "created_at"),
    )


class ExperienceAgentStepRun(Base):
    __tablename__ = "experience_agent_step_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("experience_agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_experience_agent_step_runs_agent_run_id", "agent_run_id"),
        Index("idx_experience_agent_step_runs_step_name", "step_name"),
        Index("idx_experience_agent_step_runs_status", "status"),
    )


class ExperienceRecentSummary(Base):
    __tablename__ = "experience_recent_summaries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    time_window_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    job_keywords_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    company_keywords_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generated_by_task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("experience_collection_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
