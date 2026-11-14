"""Round 23 / L3: recoverable, idempotent purge executor.

A purge is a sequence of recorded steps. Each step is committed on its own,
so a worker crash mid-purge leaves a known checkpoint and redelivery resumes
from the first unfinished step. COMPLETED is written only by the independent
verifier; no step ever sets it.

Order is deliberate: DB content is erased first (a transaction we can roll
back), then the irreversible external object, then best-effort cache cleanup.
A MinIO failure therefore leaves a fenced, content-free database and a FAILED
request that can be retried -- never a false success.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.exceptions import AppError
from . import erasure
from .constants import (BLOCKED, CLEANING, COMPLETED, ERR_CACHE_CLEAR, ERR_DEPENDENCY,
                        ERR_OBJECT_REMOVE, ERR_UNRESOLVED_MODEL, ERR_UNKNOWN,
                        ERR_VERIFY, FAILED, FENCED, POLICY_VERSION, REQUESTED,
                        STEP_DB_CONTENT, STEP_MINIO_OBJECT, STEP_REDIS_CACHE,
                        STEP_VERIFIED, VERIFYING)
from .models import PurgeRequest

logger = logging.getLogger(__name__)

# Injected adapters keep the cleaner testable without real MinIO/Redis and let
# the offline acceptance harness use replayed implementations.
ObjectRemover = Callable[[str], Awaitable[None]]
CacheClearer = Callable[[int, str, int], Awaitable[str]]
ObjectProbe = Callable[[str], Awaitable[bool]]


class Hooks:
    def __init__(self, *, remove_object: ObjectRemover | None = None,
                 clear_cache: CacheClearer | None = None,
                 object_exists: ObjectProbe | None = None):
        self.remove_object = remove_object
        self.clear_cache = clear_cache
        self.object_exists = object_exists


async def default_remove_object(object_key: str) -> None:
    from interview_api.core.config import settings
    from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
    await MinioObjectStorageProvider().delete(
        bucket_name=settings.minio_bucket, object_key=object_key)


async def default_object_exists(object_key: str) -> bool:
    """True means the object is still there. NoSuchKey/NoSuchBucket -> False."""
    from interview_api.core.config import settings
    from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
    provider = MinioObjectStorageProvider()
    import asyncio
    def _sync() -> bool:
        try:
            provider._client.stat_object(settings.minio_bucket, object_key)
            return True
        except Exception as exc:  # S3Error NoSuchKey/NoSuchBucket
            if getattr(exc, "code", None) in ("NoSuchKey", "NoSuchBucket"):
                return False
            raise
    return await asyncio.to_thread(_sync)


async def default_clear_cache(uid: int, resource_type: str, rid: int) -> str:
    """Redis holds rate-limit counters, lock tokens and Celery envelopes only --
    no resume/JD content. Cached items are short-lived; failure must not claim
    completion, but a purge can retry this step safely."""
    import redis.asyncio as aioredis
    from interview_api.core.config import settings
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        # Only keys this service owns, scoped to this user/resource. No KEYS *.
        patterns = [f"rate:*:user:{uid}", f"lock:purge:{resource_type}:{rid}"]
        removed = 0
        for pattern in patterns:
            async for key in client.scan_iter(match=pattern, count=100):
                await client.delete(key)
                removed += 1
        return f"redis keys removed={removed}"
    finally:
        await client.aclose()


def _steps(row: PurgeRequest) -> list[str]:
    return list(row.completed_steps or [])


def _mark(row: PurgeRequest, step: str) -> None:
    steps = _steps(row)
    if step not in steps:
        steps.append(step)
        row.completed_steps = steps


async def _save(db: AsyncSession, row: PurgeRequest) -> None:
    await db.flush()
    await db.commit()
    # expire_on_commit leaves the ORM attributes stale; a later attribute read
    # would trigger an implicit (sync) load and raise MissingGreenlet. Refresh
    # eagerly with an await so every subsequent step reads committed state.
    await db.refresh(row)


async def _fail(db: AsyncSession, row: PurgeRequest, code: str, diagnostic: str) -> None:
    # A preceding rollback may have expired the ORM attributes; refresh before
    # writing so the diagnostic is never masked by a lazy-load error.
    await db.refresh(row)
    row.status = FAILED
    row.error_code = code
    # Diagnostics are fixed, coded strings; never resource text.
    row.error_diagnostic = diagnostic[:300]
    row.attempts += 1
    await _save(db, row)


async def _block(db: AsyncSession, row: PurgeRequest, code: str, diagnostic: str) -> None:
    row.status = BLOCKED
    row.error_code = code
    row.error_diagnostic = diagnostic[:300]
    await _save(db, row)


async def run_purge(request_id: int, db: AsyncSession, hooks: Hooks | None = None) -> dict:
    """Execute (or resume) one purge request. Safe to call repeatedly."""
    hooks = hooks or Hooks()
    row = await db.get(PurgeRequest, request_id)
    if row is None:
        raise AppError("PURGE_REQUEST_NOT_FOUND", "清除请求不存在", 404)
    if row.status == COMPLETED:
        return _summary(row)
    if row.status == BLOCKED:
        return _summary(row)

    steps = _steps(row)
    uid, rtype, rid = row.user_id, row.resource_type, row.resource_id

    # ── Step 0: FENCED. The row itself is already the fence; make the state
    # explicit and re-check the one dependency we cannot verify.
    if row.status in (REQUESTED, FENCED, FAILED):
        row.status = FENCED
        row.fenced_at = erasure.utcnow()
        await _save(db, row)

    # UNKNOWN model outcomes: a request was sent but the result is unknown. We
    # must not silently re-send it and cannot prove what the provider stored.
    unknown = await db.scalar(text(
        "SELECT count(*) FROM model_admissions a "
        "JOIN model_budget_owners o ON o.owner_key = a.owner_key "
        "WHERE o.user_id = :uid AND a.status = 'UNKNOWN'"), dict(uid=uid))
    if unknown:
        await _block(db, row, ERR_UNRESOLVED_MODEL,
                     "unresolved model outcome exists; provider-side retention cannot be verified")
        return _summary(row)

    # ── Step 1: erase all in-scope personal content (single DB transaction).
    if STEP_DB_CONTENT not in steps:
        row.status = CLEANING
        row.cleaning_at = erasure.utcnow()
        row.error_code = None
        row.error_diagnostic = None
        await _save(db, row)
        try:
            scope = await erasure.resolve_scope(db, uid, rtype, rid)
            # Persist the scope BEFORE mutating: scope_json + object_key survive
            # the row deletion and let a retry verify exactly what was cleaned.
            row.scope_json = {k: v for k, v in scope.items()
                              if k not in ("uid", "rid", "resume_rid", "target_rid",
                                           "resource_type")}
            row.object_key = scope.get("object_key")
            row.policy_version = POLICY_VERSION
            await db.flush()

            # Round 23 decisive gate: database triggers make published history
            # immutable, and user-level aggregates cannot be attributed to one
            # resource. Detect BEFORE any write and BLOCK with a precise,
            # content-free diagnosis. No partial erase, nothing to recover.
            blockers = await erasure.preflight_blockers(db, scope)
            if blockers["blocked"]:
                from .constants import ERR_DEPENDENCY
                row.scope_json = {**row.scope_json, "blockers": blockers}
                await _block(db, row, ERR_DEPENDENCY,
                             "immutable_history=" + ",".join(sorted(blockers["immutable"]))
                             + "; unattributable=" + ",".join(sorted(blockers["attribution"])))
                return _summary(row)

            erased = await erasure.erase_content(db, scope)
            if scope["strategy"] == "HARD_DELETE":
                await erasure.delete_derivatives(db, scope, include_versions=True)
                await erasure.delete_resource_row(db, scope, rtype)
            else:
                await erasure.delete_derivatives(db, scope, include_versions=False)
            _mark(row, STEP_DB_CONTENT)
            await _save(db, row)
            logger.info("purge %s db_content_erased tables=%s strategy=%s",
                        request_id, len(erased), scope["strategy"])
        except Exception as exc:
            await db.rollback()
            await _fail(db, row, ERR_UNKNOWN, f"db erasure failed: {type(exc).__name__}")
            return _summary(row)

    # Reload after commit so row state is authoritative on a redelivered task.
    await db.refresh(row)
    key = row.object_key

    # ── Step 2: remove the MinIO object (external, irreversible, retryable).
    if STEP_MINIO_OBJECT not in _steps(row):
        try:
            if key:
                remove = hooks.remove_object or default_remove_object
                await remove(key)
            _mark(row, STEP_MINIO_OBJECT)
            await _save(db, row)
        except Exception as exc:
            await _fail(db, row, ERR_OBJECT_REMOVE,
                        f"object removal failed: {type(exc).__name__}")
            return _summary(row)

    # ── Step 3: best-effort Redis cleanup, with an explicit lifecycle note.
    if STEP_REDIS_CACHE not in _steps(row):
        try:
            clear = hooks.clear_cache or default_clear_cache
            note = await clear(uid, rtype, rid)
            residual = dict(row.residual_json or {})
            residual["redis"] = note or "no keys"
            row.residual_json = residual
            _mark(row, STEP_REDIS_CACHE)
            await _save(db, row)
        except Exception as exc:
            await _fail(db, row, ERR_CACHE_CLEAR,
                        f"cache cleanup failed: {type(exc).__name__}")
            return _summary(row)

    # ── Step 4: independent verification, then COMPLETED.
    row.status = VERIFYING
    row.verifying_at = erasure.utcnow()
    await _save(db, row)
    from .verifier import verify
    report = await verify(db, row, object_exists=hooks.object_exists)
    if not report["ok"]:
        await _fail(db, row, report.get("code", ERR_VERIFY),
                    "; ".join(report["failures"])[:300])
        return _summary(row)
    _mark(row, STEP_VERIFIED)
    row.status = COMPLETED
    row.completed_at = erasure.utcnow()
    row.error_code = None
    row.error_diagnostic = None
    row.residual_json = {**(row.residual_json or {}), **report["residual"]}
    await _save(db, row)
    return _summary(row)


def _summary(row: PurgeRequest) -> dict:
    return dict(id=row.id, user_id=row.user_id, resource_type=row.resource_type,
                resource_id=row.resource_id, status=row.status,
                policy_version=row.policy_version, steps=list(row.completed_steps or []),
                error_code=row.error_code, error_diagnostic=row.error_diagnostic,
                attempts=row.attempts, completed_at=row.completed_at.isoformat()
                if row.completed_at else None)
