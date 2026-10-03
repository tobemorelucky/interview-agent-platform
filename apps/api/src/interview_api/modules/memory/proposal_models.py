"""Controlled interview-derived candidates; only approval writes existing Memory."""
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from interview_api.infrastructure.db.base import Base


PROPOSAL_TYPES = {"GOAL", "PREFERENCE", "EXPERIENCE", "SKILL_OBSERVATION", "PREPARATION_FOCUS"}
PROPOSAL_STATUSES = {"PENDING", "APPROVED", "CORRECTED", "REJECTED"}
CONFIDENCE_SIGNALS = {"SINGLE_EVALUATION", "REPEATED_EVALUATIONS"}


class MemoryProposal(Base):
    __tablename__ = "memory_proposals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id", ondelete="RESTRICT"))
    report_id: Mapped[int] = mapped_column(ForeignKey("final_interview_reports.id", ondelete="RESTRICT"))
    proposal_type: Mapped[str] = mapped_column(String(32))
    original_content: Mapped[str] = mapped_column(Text)
    corrected_content: Mapped[str | None] = mapped_column(Text)
    content_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", server_default="PENDING")
    confidence: Mapped[str] = mapped_column(String(40))
    supporting_evaluation_ids: Mapped[list] = mapped_column(JSON)
    observation_ids: Mapped[list] = mapped_column(JSON)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    confirmation_reason: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed_by: Mapped[str | None] = mapped_column(String(20))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    memory_item_id: Mapped[int | None] = mapped_column(ForeignKey("user_memory_items.id", ondelete="RESTRICT"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("report_id", "content_fingerprint", name="uq_memory_proposal_report_content"),
        CheckConstraint("status IN ('PENDING','APPROVED','CORRECTED','REJECTED')", name="ck_memory_proposal_status"),
        CheckConstraint("proposal_type IN ('GOAL','PREFERENCE','EXPERIENCE','SKILL_OBSERVATION','PREPARATION_FOCUS')", name="ck_memory_proposal_type"),
        CheckConstraint("confidence IN ('SINGLE_EVALUATION','REPEATED_EVALUATIONS')", name="ck_memory_proposal_confidence"),
        CheckConstraint("status NOT IN ('APPROVED','CORRECTED') OR (memory_item_id IS NOT NULL AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)", name="ck_memory_proposal_approved_memory"),
        CheckConstraint("status != 'CORRECTED' OR (corrected_content IS NOT NULL AND corrected_at IS NOT NULL AND changed_by = 'user')", name="ck_memory_proposal_corrected"),
        CheckConstraint("status NOT IN ('PENDING','REJECTED') OR memory_item_id IS NULL", name="ck_memory_proposal_uncommitted"),
        Index("idx_memory_proposals_user_status", "user_id", "status"),
    )


class MemoryProposalEvaluation(Base):
    __tablename__ = "memory_proposal_evaluations"
    proposal_id: Mapped[int] = mapped_column(ForeignKey("memory_proposals.id", ondelete="CASCADE"), primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("interview_evaluations.id", ondelete="RESTRICT"), primary_key=True)


class MemoryProposalObservation(Base):
    __tablename__ = "memory_proposal_observations"
    proposal_id: Mapped[int] = mapped_column(ForeignKey("memory_proposals.id", ondelete="CASCADE"), primary_key=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("interview_performance_observations.id", ondelete="RESTRICT"), primary_key=True)
