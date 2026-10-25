"""Versioned system inference; intentionally separate from approved user facts."""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from interview_api.infrastructure.db.base import Base


class CapabilityMemory(Base):
    __tablename__ = "capability_memories"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusions: Mapped[list] = mapped_column(JSON, default=list)
    correction_note: Mapped[str | None] = mapped_column(String(500))
    projection: Mapped[dict] = mapped_column(JSON, default=dict)
    input_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    __table_args__ = (UniqueConstraint("user_id", "topic", name="uq_capability_owner_topic"),)


class CapabilityMemoryVersion(Base):
    __tablename__ = "capability_memory_versions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    memory_id: Mapped[int] = mapped_column(ForeignKey("capability_memories.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(20))
    previous_state: Mapped[str] = mapped_column(String(30))
    projection: Mapped[dict] = mapped_column(JSON)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("memory_id", "revision", name="uq_capability_version"),)
