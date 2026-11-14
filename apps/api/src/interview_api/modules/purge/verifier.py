"""Round 23 / L3: independent verification. COMPLETED depends only on this.

The verifier opens its own queries against the committed database and checks
the *agreed scope*, not just the cleaner's own bookkeeping. A purge that erased
only part of the scope, lost its MinIO object, left audit payloads intact, or
can still be read by the user's normal endpoints is a failure.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import erasure
from .constants import (ERASED_HASH, ERASED_JSON, ERASED_TEXT, ERR_DEPENDENCY,
                        ERR_OBJECT_REMOVE, ERR_VERIFY, PRIVATE_JD, RESUME,
                        STEP_DB_CONTENT, STEP_MINIO_OBJECT, STEP_REDIS_CACHE)
from .models import PurgeRequest
from .cleaner import ObjectProbe, default_object_exists

logger = logging.getLogger(__name__)


def _erased(value, kind: str) -> bool:
    if value is None:
        return kind in ("text", "hash", "json_obj", "json_arr")  # NULL is erased
    if kind == "hash":
        return value == ERASED_HASH
    if kind == "json_obj":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                return False
        return isinstance(value, dict) and value.get("erased") is True
    if kind == "json_arr":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                return False
        return isinstance(value, list) and len(value) == 0
    return value == ERASED_TEXT


async def verify(db: AsyncSession, row: PurgeRequest,
                 object_exists: ObjectProbe | None = None) -> dict:
    """Return {ok, failures, residual, checks}. Never writes COMPLETED."""
    failures: list[str] = []
    checks: dict[str, bool] = {}
    uid, rtype, rid = row.user_id, row.resource_type, row.resource_id
    steps = list(row.completed_steps or [])
    scope = dict(row.scope_json or {})

    for step in (STEP_DB_CONTENT, STEP_MINIO_OBJECT, STEP_REDIS_CACHE):
        if step not in steps:
            failures.append(f"step_not_recorded:{step}")
    checks["steps_recorded"] = not any(f.startswith("step_not_recorded") for f in failures)

    # 1. MinIO object must not exist.
    probe = object_exists or default_object_exists
    if row.object_key:
        try:
            still_there = await probe(row.object_key)
            checks["minio_object_absent"] = not still_there
            if still_there:
                failures.append("minio_object_still_present")
        except Exception as exc:
            checks["minio_object_absent"] = False
            failures.append(f"minio_probe_failed:{type(exc).__name__}")
    else:
        checks["minio_object_absent"] = True  # no object (e.g. private JD)

    # 2. Resource row: gone (HARD_DELETE) or content-free skeleton.
    table = "resumes" if rtype == RESUME else "user_job_targets"
    exists = await db.scalar(text(f"SELECT 1 FROM {table} WHERE id = :rid"), dict(rid=rid))
    if scope.get("strategy") == "HARD_DELETE":
        checks["resource_row_deleted"] = not exists
        if exists:
            failures.append("resource_row_still_exists")
    else:
        checks["resource_skeleton_kept"] = bool(exists)
        if not exists:
            failures.append("historical_skeleton_lost")

    # 3. Every in-scope content column is erased (or its row is gone).
    # scope_json deliberately omits internal ids; rebuild the typed rids from
    # the request itself so verification scopes exactly the same rows.
    params = erasure._params({"uid": uid, "rid": rid,
                              "resource_type": rtype,
                              "resume_rid": rid if rtype == RESUME else -1,
                              "target_rid": rid if rtype == PRIVATE_JD else -1,
                              **{k: scope.get(k, []) for k in erasure.EXPANDING}})
    unclean: list[str] = []
    jtypes = await erasure._json_types(db)
    for tname, columns in erasure.ERASE_PLAN.items():
        where = erasure.ROW_SCOPES[tname]
        cols = ", ".join(c for c, _ in columns)
        sql = f"SELECT {cols} FROM {tname} WHERE {where}"
        try:
            rows = (await db.execute(erasure._expand(text(sql), sql), params)).fetchall()
        except Exception as exc:
            unclean.append(f"{tname}:query_failed:{type(exc).__name__}")
            continue
        for r in rows:
            for i, (col, kind) in enumerate(columns):
                actual = jtypes.get(f"{tname}.{col}")
                effective = (kind if kind in ("json_obj", "json_arr") else
                             ("json_obj" if actual in ("json", "jsonb") else kind))
                if not _erased(r[i], effective):
                    unclean.append(f"{tname}.{col}")
                    break
    checks["scope_content_erased"] = not unclean
    if unclean:
        failures.append("scope_content_not_erased:" + ",".join(sorted(set(unclean))[:8]))

    # 4. User-level aggregates of THIS owner are redacted too.
    unclean_user: list[str] = []
    for tname, (where, columns) in erasure.USER_LEVEL_PLAN.items():
        cols = ", ".join(c for c, _ in columns)
        sql = f"SELECT {cols} FROM {tname} WHERE {where}"
        try:
            rows = (await db.execute(text(sql), dict(uid=uid))).fetchall()
        except Exception as exc:
            unclean_user.append(f"{tname}:query_failed:{type(exc).__name__}")
            continue
        for r in rows:
            for i, (col, kind) in enumerate(columns):
                actual = jtypes.get(f"{tname}.{col}")
                effective = (kind if kind in ("json_obj", "json_arr") else
                             ("json_obj" if actual in ("json", "jsonb") else kind))
                if not _erased(r[i], effective):
                    unclean_user.append(f"{tname}.{col}")
                    break
    checks["user_level_redacted"] = not unclean_user
    if unclean_user:
        failures.append("user_level_not_redacted:" + ",".join(sorted(set(unclean_user))[:8]))

    # 5. Ledger integrity: admission count unchanged, no new call caused by purge.
    kept = scope.get("audit_keep", {})
    now = await db.scalar(text(
        "SELECT count(*) FROM model_admissions a "
        "JOIN model_budget_owners o ON o.owner_key = a.owner_key WHERE o.user_id = :uid"),
        dict(uid=uid))
    checks["model_ledger_intact"] = now == kept.get("model_admissions", now)
    if now != kept.get("model_admissions", now):
        failures.append("model_ledger_changed")

    # 6. Fence still active: a normal read cannot silently re-enable it.
    from .fence import is_fenced
    checks["fence_active"] = await is_fenced(db, rtype, rid)
    if not checks["fence_active"]:
        failures.append("fence_missing")

    # 7. Redis step recorded (the cleaner already retried it on failure).
    checks["redis_step_done"] = STEP_REDIS_CACHE in steps

    residual = {
        "external_boundaries": scope.get("external_boundaries", []),
        "public_pipeline_untouched": scope.get("public_pipeline_untouched", []),
        "user_level_tables_redacted": list(erasure.USER_LEVEL_PLAN),
        "audit_kept": kept,
        "strategy": scope.get("strategy"),
        "historical_sessions": ("erased_content_skeleton_kept"
                                if scope.get("strategy") == "ERASE_KEEP_SKELETON" else "none"),
    }
    return dict(ok=not failures, failures=failures, checks=checks, residual=residual,
                code=ERR_OBJECT_REMOVE if any("minio" in f for f in failures) else ERR_VERIFY)
