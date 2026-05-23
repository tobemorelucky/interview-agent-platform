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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from interview_api.infrastructure.db.base import Base


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
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
