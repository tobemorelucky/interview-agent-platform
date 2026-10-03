"""Persistent audit records for public-data tool calls."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from interview_api.infrastructure.db.base import Base, TimestampMixin


class ToolInvocation(TimestampMixin, Base):
    __tablename__ = "tool_invocations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    refresh_task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("public_data_refresh_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_tool_invocations_refresh_task", "refresh_task_id", "created_at"),
        Index("idx_tool_invocations_tool", "tool_name", "created_at"),
        Index("idx_tool_invocations_status", "status"),
    )

