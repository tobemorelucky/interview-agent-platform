"""Round 23 / L3: purge request ORM model."""
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from interview_api.infrastructure.db.base import Base, TimestampMixin
# SQLAlchemy resolves ForeignKey("users.id") lazily, at mapper configuration.
# Importing the users model here makes every subset/standalone import see the
# same registry -- the same fix conftest applies for the private-JD FK.
from interview_api.modules.users import models as _users_models  # noqa: F401
from .constants import COMPLETED


class PurgeRequest(Base, TimestampMixin):
    __tablename__ = "purge_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="REQUESTED")
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    completed_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Server-derived locator only; never supplied by the client, never logged.
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_diagnostic: Mapped[str | None] = mapped_column(String(300), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    residual_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    fenced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleaning_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verifying_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("resource_type IN ('RESUME','PRIVATE_JD')", name="ck_purge_resource_type"),
        CheckConstraint("status IN ('REQUESTED','FENCED','CLEANING','VERIFYING','COMPLETED','FAILED','BLOCKED')",
                        name="ck_purge_status"),
        UniqueConstraint("user_id", "resource_type", "resource_id", name="uq_purge_user_resource"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_purge_user_idempotency"),
        Index("idx_purge_status", "status"),
        Index("idx_purge_user", "user_id"),
    )

    @property
    def done(self) -> bool:
        return self.status == COMPLETED
