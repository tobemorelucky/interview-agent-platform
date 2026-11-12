"""Private, user-supplied JD targets.

A row here is never a substitute for a public Company / Job /
JobDescriptionVersion: nothing in this table is published, indexed, retrieved by
Job Matching or readable by another user. The public career tables keep their
unique names and review flow untouched.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from interview_api.infrastructure.db.base import Base, TimestampMixin

# The only provenance this table can carry. It must never be presented as
# Research-verified, administrator-published or company-confirmed.
SOURCE_USER_SUPPLIED = "USER_SUPPLIED"

DRAFT, PARSED, CONFIRMED, FAILED = "DRAFT", "PARSED", "CONFIRMED", "FAILED"


class UserJobTarget(TimestampMixin, Base):
    __tablename__ = "user_job_targets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
                                         nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_title: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default=SOURCE_USER_SUPPLIED,
                                             server_default=SOURCE_USER_SUPPLIED)
    source_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    structured_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    requirements_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    parse_status: Mapped[str] = mapped_column(String(20), nullable=False, default=DRAFT, server_default=DRAFT)
    # LLM or RULE. A rule result is never described as model understanding.
    parse_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    parse_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "content_fingerprint", name="uq_user_job_targets_owner_content"),
        Index("idx_user_job_targets_owner_created", "user_id", "created_at"),
    )
