"""Round 23 / L3: request intake, ownership isolation and the execution gate.

The intake creates a persistent fence (the request row) immediately. Execution
is a SEPARATE, server-side decision: it is OFF by default and additionally
requires an isolated test/E2E database plus an explicit synthetic-user
allowlist. A front-end button can never enable real deletion.
"""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.config import settings
from interview_api.core.db_targets import (classify, E2E, UI, TEST, MIGRATION,
                                           ARCHIVED_LIVE, DEMO, PRODUCTION, SYSTEM)
from interview_api.core.exceptions import AppError
from . import erasure
from .constants import (COMPLETED, FAILED, BLOCKED, PRIVATE_JD, REQUESTED, RESUME,
                        RESOURCE_TYPES, POLICY_VERSION)
from .models import PurgeRequest

# Human-readable UI states. Completion is only ever reported for COMPLETED.
UI_STATES = {
    "REQUESTED": "PENDING", "FENCED": "PROCESSING", "CLEANING": "PROCESSING",
    "VERIFYING": "PROCESSING", "COMPLETED": "COMPLETED",
    "FAILED": "FAILED_OR_BLOCKED", "BLOCKED": "FAILED_OR_BLOCKED",
}


def execution_capability() -> dict:
    """What the front end is allowed to show. Never an execution switch."""
    enabled = bool(getattr(settings, "purge_execution_enabled", False))
    return dict(execution_enabled=enabled,
                notice=("仅隔离测试环境启用清除执行" if enabled
                        else "当前环境未启用清除执行，申请仅建立业务围栏"))


def _allowlist() -> set[int]:
    raw = (getattr(settings, "purge_test_user_ids", "") or "").strip()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def can_execute(user_id: int, database_name: str) -> tuple[bool, str]:
    """Three independent gates. All must pass before a Celery task is sent."""
    if not getattr(settings, "purge_execution_enabled", False):
        return False, "PURGE_EXECUTION_DISABLED"
    if user_id not in _allowlist():
        return False, "PURGE_USER_NOT_ALLOWLISTED"
    kind = classify(database_name)
    if kind not in (TEST, MIGRATION, E2E, UI):
        return False, "PURGE_DATABASE_NOT_ISOLATED"
    return True, ""


def serialize(row: PurgeRequest) -> dict:
    """Client view. object_key and scope internals are never exposed."""
    return dict(
        id=row.id, resource_type=row.resource_type, resource_id=row.resource_id,
        status=row.status, ui_state=UI_STATES.get(row.status, "PROCESSING"),
        policy_version=row.policy_version,
        steps=list(row.completed_steps or []),
        error_code=row.error_code, error_diagnostic=row.error_diagnostic,
        attempts=row.attempts,
        created_at=row.created_at.isoformat() if row.created_at else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
        residual_external=list(erasure.EXTERNAL_BOUNDARIES),
        # An honest, fixed statement -- never "permanently deleted" before done.
        statement=("该资料的约定清除范围已完成并经独立验证；历史会话骨架、"
                   "非内容型模型账本、外部备份与模型服务端日志不在可清除范围内。"
                   if row.status == COMPLETED else
                   "清除尚未完成。归档隐藏不等于清除；此时个人数据仍可能保留在库中。"),
    )


async def _owned_resource_exists(db: AsyncSession, uid: int, rtype: str, rid: int) -> bool:
    table = "resumes" if rtype == RESUME else "user_job_targets"
    row = await db.scalar(text(f"SELECT 1 FROM {table} WHERE id = :rid AND user_id = :uid"),
                          dict(rid=rid, uid=uid))
    return bool(row)


async def request_purge(db: AsyncSession, uid: int, rtype: str, rid: int,
                        idempotency_key: str) -> tuple[PurgeRequest, bool]:
    """Idempotent intake. Returns (row, created).

    Ownership is checked with the SAME query for "missing" and "someone else's",
    so a forged resource_id cannot probe or delete another user's resource.
    object_key is never taken from a request -- the cleaner derives it from the
    database row. A PROCESSING resume is accepted (no permanent 409): the fence
    stops new business at once and the running task checks the fence.
    """
    if rtype not in RESOURCE_TYPES:
        raise AppError("PURGE_INVALID_RESOURCE_TYPE", "不支持的资源类型", 422)
    if not await _owned_resource_exists(db, uid, rtype, rid):
        # Same answer for missing and foreign: no existence oracle.
        raise AppError("PURGE_RESOURCE_NOT_FOUND", "资源不存在", 404)

    existing = await db.scalar(select(PurgeRequest).where(
        PurgeRequest.user_id == uid, PurgeRequest.resource_type == rtype,
        PurgeRequest.resource_id == rid).limit(1))
    if existing is not None:
        return existing, False

    by_key = await db.scalar(select(PurgeRequest).where(
        PurgeRequest.user_id == uid,
        PurgeRequest.idempotency_key == idempotency_key).limit(1))
    if by_key is not None:
        # Same key on a different resource: refuse rather than silently
        # redirecting a purge to another resource.
        raise AppError("PURGE_IDEMPOTENCY_KEY_REUSED", "该请求键已用于其他资源", 409)

    row = PurgeRequest(user_id=uid, resource_type=rtype, resource_id=rid,
                       idempotency_key=idempotency_key[:128], status=REQUESTED,
                       policy_version=POLICY_VERSION, scope_json={}, completed_steps=[],
                       attempts=0, residual_json={})
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, True


async def get_mine(db: AsyncSession, uid: int, request_id: int) -> PurgeRequest:
    row = await db.scalar(select(PurgeRequest).where(
        PurgeRequest.id == request_id, PurgeRequest.user_id == uid).limit(1))
    if row is None:
        raise AppError("PURGE_REQUEST_NOT_FOUND", "清除请求不存在", 404)
    return row


async def list_mine(db: AsyncSession, uid: int, limit: int = 50) -> list[PurgeRequest]:
    return list((await db.scalars(select(PurgeRequest).where(
        PurgeRequest.user_id == uid).order_by(PurgeRequest.id.desc()).limit(limit))).all())
