"""ORM models for user-level layered memory."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
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


class UserMemoryItem(Base):
    __tablename__ = "user_memory_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="INTERVIEW")
    key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    source_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="PRIVATE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_user_memory_items_user_id", "user_id"),
        Index("idx_user_memory_items_user_type", "user_id", "memory_type"),
        Index("idx_user_memory_items_user_scope", "user_id", "scope"),
        Index("idx_user_memory_items_user_status", "user_id", "status"),
        Index("idx_user_memory_items_user_key", "user_id", "key"),
    )


class UserSkillProfile(Base):
    __tablename__ = "user_skill_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False)
    skill_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    level_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weakness_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    strength_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "skill_name", name="uq_user_skill_profiles_user_skill"),
        Index("idx_user_skill_profiles_user_id", "user_id"),
        Index("idx_user_skill_profiles_category", "skill_category"),
    )


class UserMemoryEvent(Base):
    __tablename__ = "user_memory_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    memory_item_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user_memory_items.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, default="SYSTEM")
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_user_memory_events_user_id", "user_id"),
        Index("idx_user_memory_events_memory_item_id", "memory_item_id"),
        Index("idx_user_memory_events_event_type", "event_type"),
    )
