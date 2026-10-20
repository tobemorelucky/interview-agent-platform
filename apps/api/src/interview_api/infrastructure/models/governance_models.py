"""Durable admission ledger: application quotas, not provider billing."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from interview_api.infrastructure.db.base import Base


class ModelBudgetOwner(Base):
    __tablename__ = "model_budget_owners"
    owner_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), unique=True)


class ModelAdmission(Base):
    __tablename__ = "model_admissions"
    call_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_key: Mapped[str] = mapped_column(ForeignKey("model_budget_owners.owner_key", ondelete="RESTRICT"), index=True)
    route: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    reserved_tokens: Mapped[int] = mapped_column(Integer)
    charged_tokens: Mapped[int] = mapped_column(Integer)
    actual_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('RESERVED','SETTLED','UNKNOWN')", name="ck_model_admission_status"),
        CheckConstraint("reserved_tokens > 0 AND charged_tokens >= 0", name="ck_model_admission_tokens"),
    )
