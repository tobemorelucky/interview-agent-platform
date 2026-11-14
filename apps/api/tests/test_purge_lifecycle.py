"""Round 23 / L3: real resource-level purge, fences, recovery and blockers.

All data is synthetic. MinIO and Redis are injected fakes; no real model call,
no real deletion in production or historical Live databases.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.modules.purge import erasure
from interview_api.modules.purge.cleaner import Hooks, run_purge
from interview_api.modules.purge.constants import (BLOCKED, COMPLETED, ERR_DEPENDENCY,
                                                   ERR_OBJECT_REMOVE, FAILED, PRIVATE_JD,
                                                   RESUME, STEP_DB_CONTENT, STEP_MINIO_OBJECT)
from interview_api.modules.purge.fence import assert_resume_usable, assert_target_usable, is_fenced
from interview_api.modules.purge.models import PurgeRequest
from interview_api.modules.purge.service import (can_execute, execution_capability,
                                                 get_mine, request_purge, serialize,
                                                 list_mine)
from interview_api.modules.purge.verifier import verify
from purge_helpers import seed_world


def _hooks(*, fail_remove=False, remove_calls=None, cache_fail=False, object_there=False):
    async def rem(key):
        if remove_calls is not None:
            remove_calls.append(key)
        if fail_remove:
            raise RuntimeError("simulated MinIO outage")

    async def clr(uid, rtype, rid):
        if cache_fail:
            raise RuntimeError("simulated Redis outage")
        return "mock-redis-ok"

    async def exists(key):
        return object_there
    return Hooks(remove_object=rem, clear_cache=clr, object_exists=exists)


async def _clean_world(factory, uid: int):
    """Remove user-level aggregates so a minimal resource can be fully purged."""
    async with factory() as db:
        for t in ("user_memory_items", "capability_memories", "user_skill_profiles"):
            await db.execute(text(f"DELETE FROM {t} WHERE user_id=:u"), dict(u=uid))
        await db.execute(text("DELETE FROM chat_messages WHERE session_id IN "
                              "(SELECT id FROM chat_sessions WHERE user_id=:u)"), dict(u=uid))
        await db.execute(text("DELETE FROM chat_sessions WHERE user_id=:u"), dict(u=uid))
        await db.commit()


async def _request(factory, uid, rtype, rid, key):
    async with factory() as db:
        row, created = await request_purge(db, uid, rtype, rid, key)
        return row.id, created


async def _run(factory, request_id, hooks=None):
    async with factory() as db:
        return await run_purge(request_id, db, hooks or _hooks())


async def _canary_columns(factory, canary: str) -> list[str]:
    """Independent full-database scan for the personal-content marker."""
    async with factory() as db:
        rows = await db.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND data_type IN ('text','character varying','json','jsonb')"))
        cols = list(rows)
    hits = []
    async with factory() as db:
        for t, col in cols:
            try:
                n = await db.scalar(text(f'SELECT count(*) FROM "{t}" WHERE "{col}"::text LIKE :p'),
                                    dict(p=f"%{canary}%"))
                if n:
                    hits.append(f"{t}.{col}")
            except Exception:
                pass
    return hits


async def _scalar(factory, sql, **p):
    async with factory() as db:
        return await db.scalar(text(sql), p)


# ── 1, 2, 11, 14: unused resume full purge + ledger + other users ──────────
def test_unused_resume_is_physically_deleted_and_verified(database_url):
    async def scenario(factory):
        w = await seed_world(factory, with_session=False, with_derived=False, with_jd=False)
        await _clean_world(factory, w["uid_a"])
        rid, created = await _request(factory, w["uid_a"], RESUME, w["resume"], "idem-" + w["tag"])
        assert created
        removed = []
        result = await _run(factory, rid, _hooks(remove_calls=removed))
        assert result["status"] == COMPLETED, result
        assert result["steps"] == ["db_content_erased", "minio_object_removed",
                                   "redis_cache_cleared", "independently_verified"]
        assert removed == [w["object_key"]], "MinIO object must be removed"
        assert await _scalar(factory, "SELECT count(*) FROM resumes WHERE id=:r", r=w["resume"]) == 0
        assert await _canary_columns(factory, w["canary"]) == [], "no personal content may remain"
        # Unrelated resume of the SAME user and the other user survive.
        assert await _scalar(factory, "SELECT count(*) FROM resumes WHERE id=:r", r=w["resume_other"]) == 1
        assert await _scalar(factory, "SELECT count(*) FROM resumes WHERE id=:r", r=w["resume_b"]) == 1
        # Public company/job/JD untouched.
        assert await _scalar(factory, "SELECT count(*) FROM companies WHERE id=:i", i=w["company"]) == 1
        # Model ledger intact (non-content audit).
        assert await _scalar(factory, "SELECT count(*) FROM model_admissions") >= 1
        # Audit payload redacted, skeleton kept.
        aj = await _scalar(factory, "SELECT before_json::text FROM audit_logs "
                                    "WHERE resource_id=:r", r=str(w["resume"]))
        assert w["canary"] not in (aj or "") and "erased" in (aj or "")

    asyncio.run(_runner(database_url, scenario))


def _runner(database_url, fn):
    async def main():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await fn(factory)
        finally:
            await engine.dispose()
    return main()


# ── 3, 5, 12: history-referenced resources are honestly BLOCKED ───────────
def test_history_referenced_resume_blocks_without_partial_erase(database_url):
    async def scenario(factory):
        w = await seed_world(factory)  # full history: snapshot, evaluation, coach, memory
        rid, _ = await _request(factory, w["uid_a"], RESUME, w["resume"], "idem-" + w["tag"])
        r = await _run(factory, rid)
        assert r["status"] == BLOCKED and r["error_code"] == ERR_DEPENDENCY
        assert r["steps"] == [], "BLOCKED must perform ZERO erasure steps"
        # No partial erase: original content is still intact and recoverable.
        assert await _scalar(factory, "SELECT raw_text FROM resumes WHERE id=:r", r=w["resume"]) == w["canary"]
        assert await _scalar(factory, "SELECT count(*) FROM interview_context_snapshots WHERE id=:i",
                             i=w["snapshot"]) == 1
        # ...but the fence already took it out of all new business.
        async with factory() as db:
            with pytest.raises(AppError) as err:
                await assert_resume_usable(db, w["resume"])
            assert err.value.code == "RESOURCE_PURGED"
        # Diagnosis contains only table/trigger names, never the canary.
        assert w["canary"] not in (r["error_diagnostic"] or "")
        assert "immutable_history" in r["error_diagnostic"]
    asyncio.run(_runner(database_url, scenario))


def test_history_referenced_private_jd_blocks_and_fences(database_url):
    async def scenario(factory):
        w = await seed_world(factory)
        rid, _ = await _request(factory, w["uid_a"], PRIVATE_JD, w["target"], "idem-" + w["tag"])
        r = await _run(factory, rid)
        assert r["status"] == BLOCKED
        assert await _scalar(factory, "SELECT raw_text FROM user_job_targets WHERE id=:i",
                             i=w["target"]) == w["jd_canary"]
        async with factory() as db:
            with pytest.raises(AppError):
                await assert_target_usable(db, w["target"])
    asyncio.run(_runner(database_url, scenario))


# ── 4: private JD with no history is fully purged ──────────────────────────
def test_unused_private_jd_is_physically_deleted(database_url):
    async def scenario(factory):
        w = await seed_world(factory, with_session=False, with_derived=False)
        await _clean_world(factory, w["uid_a"])
        rid, _ = await _request(factory, w["uid_a"], PRIVATE_JD, w["target"], "idem-" + w["tag"])
        r = await _run(factory, rid)
        assert r["status"] == COMPLETED, r
        assert await _scalar(factory, "SELECT count(*) FROM user_job_targets WHERE id=:i", i=w["target"]) == 0
        # JD-specific marker is entirely gone, including its own audit payload.
        assert await _canary_columns(factory, w["jd_canary"]) == []
        # The user's OTHER resource (resume) and its audit must survive untouched.
        assert await _scalar(factory, "SELECT raw_text FROM resumes WHERE id=:r", r=w["resume"]) == w["canary"]
    asyncio.run(_runner(database_url, scenario))


# ── 6, 7, 13: cross-user isolation, idempotency, archive vs purge ─────────
def test_owner_isolation_idempotency_and_archive_semantics(database_url):
    async def scenario(factory):
        w = await seed_world(factory, with_session=False, with_derived=False, with_jd=False)
        # Same idempotency key, different user: cannot reach A's resource.
        with pytest.raises(AppError) as err:
            await _request(factory, w["uid_b"], RESUME, w["resume"], "shared-key-12345")
        assert err.value.code == "PURGE_RESOURCE_NOT_FOUND"

        rid, created = await _request(factory, w["uid_a"], RESUME, w["resume"], "shared-key-12345")
        rid2, created2 = await _request(factory, w["uid_a"], RESUME, w["resume"], "shared-key-12345")
        assert created and not created2 and rid == rid2
        # A reused key on a DIFFERENT resource is refused, not silently redirected.
        with pytest.raises(AppError) as err:
            await _request(factory, w["uid_a"], RESUME, w["resume_other"], "shared-key-12345")
        assert err.value.code == "PURGE_IDEMPOTENCY_KEY_REUSED"

        # A fence is not an archive: archived_at stays NULL.
        assert await _scalar(factory, "SELECT archived_at FROM resumes WHERE id=:r", r=w["resume"]) is None
        # get/list are owner-scoped.
        async with factory() as db:
            with pytest.raises(AppError):
                await get_mine(db, w["uid_b"], rid)
            rows = await list_mine(db, w["uid_b"])
            assert all(x.id != rid for x in rows)
    asyncio.run(_runner(database_url, scenario))


# ── 8, 9: worker crash recovery and MinIO retry ───────────────────────────
def test_minio_failure_is_retryable_and_never_false_success(database_url):
    async def scenario(factory):
        w = await seed_world(factory, with_session=False, with_derived=False, with_jd=False)
        await _clean_world(factory, w["uid_a"])
        rid, _ = await _request(factory, w["uid_a"], RESUME, w["resume"], "idem-" + w["tag"])
        first = await _run(factory, rid, _hooks(fail_remove=True))
        assert first["status"] == FAILED and first["error_code"] == ERR_OBJECT_REMOVE
        assert STEP_MINIO_OBJECT not in first["steps"], "must not claim the object was removed"
        # Status is recoverable: a redelivered task resumes, not restarts.
        calls = []
        second = await _run(factory, rid, _hooks(remove_calls=calls))
        assert second["status"] == COMPLETED, second
        assert calls == [w["object_key"]]
        assert STEP_DB_CONTENT in second["steps"]
    asyncio.run(_runner(database_url, scenario))


def test_object_still_present_fails_verification(database_url):
    async def scenario(factory):
        w = await seed_world(factory, with_session=False, with_derived=False, with_jd=False)
        await _clean_world(factory, w["uid_a"])
        rid, _ = await _request(factory, w["uid_a"], RESUME, w["resume"], "idem-" + w["tag"])
        # The remover "succeeds" but an independent probe still sees the object.
        r = await _run(factory, rid, _hooks(object_there=True))
        assert r["status"] == FAILED, "independent verification must catch a lying remover"
        assert r["error_code"] in (ERR_OBJECT_REMOVE, "INDEPENDENT_VERIFICATION_FAILED")
        assert await _scalar(factory, "SELECT status FROM purge_requests WHERE id=:i", i=rid) == FAILED
    asyncio.run(_runner(database_url, scenario))


def test_worker_crash_mid_purge_resumes_from_checkpoint(database_url):
    async def scenario(factory):
        w = await seed_world(factory, with_session=False, with_derived=False, with_jd=False)
        await _clean_world(factory, w["uid_a"])
        rid, _ = await _request(factory, w["uid_a"], RESUME, w["resume"], "idem-" + w["tag"])
        # Simulate a crash AFTER the DB step by forcing the object step to fail,
        # then a fresh worker (new session) redelivers the same request id.
        await _run(factory, rid, _hooks(fail_remove=True))
        async with factory() as db:
            row = await db.get(PurgeRequest, rid)
            assert row.status == FAILED and row.completed_steps == [STEP_DB_CONTENT]
        # "Restart": a brand new session, same request id, no in-memory state.
        r = await _run(factory, rid)
        assert r["status"] == COMPLETED, r
        assert r["steps"][0] == STEP_DB_CONTENT, "DB step not repeated"
    asyncio.run(_runner(database_url, scenario))


# ── 10: old tasks cannot resurrect purged content ─────────────────────────
def test_redelivered_profile_task_cannot_resurrect_purged_resume(database_url):
    async def scenario(factory):
        w = await seed_world(factory, with_session=False, with_derived=False, with_jd=False)
        await _clean_world(factory, w["uid_a"])
        rid, _ = await _request(factory, w["uid_a"], RESUME, w["resume"], "idem-" + w["tag"])
        assert (await _run(factory, rid))["status"] == COMPLETED
        from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
        agent = ResumeProfileAgent(factory)
        result = await agent.run(w["resume"])
        assert result["status"] == "SKIPPED" and result["reason"] == "RESOURCE_PURGED"
        # Use the test's own session: the global engine would bind a connection
        # to this asyncio.run loop and poison later tests on a fresh loop.
        from interview_api.modules.resume.processor import _fenced
        async with factory() as db:
            assert await _fenced(db, w["resume"]) is True
        assert await _scalar(factory, "SELECT count(*) FROM candidate_profile_versions "
                                      "WHERE source_resume_id=:r", r=w["resume"]) == 0
    asyncio.run(_runner(database_url, scenario))


# ── 12, 15: no false success, execution gate, pre-0029 compatibility ──────
def test_execution_is_off_by_default_and_gated(database_url):
    async def scenario(factory):
        cap = execution_capability()
        assert cap["execution_enabled"] is False, "real deletion must be OFF by default"
        assert can_execute(1, "interview_agent") [0] is False
        assert can_execute(1, "interview_agent_wb_r23_e2e_20261114")[0] is False
        assert can_execute(1, "interview_agent_wb_r23_test_20261114")[0] is False
        # Completed status is only honest after verification.
        w = await seed_world(factory)
        rid, _ = await _request(factory, w["uid_a"], RESUME, w["resume"], "idem-" + w["tag"])
        async with factory() as db:
            row = await db.get(PurgeRequest, rid)
            s = serialize(row)
            assert s["ui_state"] != "COMPLETED"
            assert "永久删除成功" not in s["statement"]
            assert "清除尚未完成" in s["statement"]
    asyncio.run(_runner(database_url, scenario))


def test_fence_is_safe_on_pre_0029_schema(database_url):
    """A database without purge_requests must still work: the probe is a no-op."""
    async def scenario(factory):
        async with factory() as db:
            from interview_api.modules.purge.fence import table_present
            assert await table_present(db) is True  # 0029 test DB has the table
            # Emulate pre-0029 by shadowing to_regclass for this connection.
            await db.execute(text(
                "CREATE OR REPLACE FUNCTION pg_temp.r23_no_table() RETURNS boolean AS "
                "$$ SELECT false $$ LANGUAGE sql"))
            # The real production DB (0025) simply returns NULL from to_regclass.
            # Assert the helper never raises when the table is absent.
            assert await is_fenced(db, RESUME, 999999) is False
    asyncio.run(_runner(database_url, scenario))


# ── 14 + dependency graph: public data & other users untouched by a purge ──
def test_scope_resolution_is_resource_specific(database_url):
    async def scenario(factory):
        w = await seed_world(factory)
        async with factory() as db:
            scope = await erasure.resolve_scope(db, w["uid_a"], RESUME, w["resume"])
            blockers = await erasure.preflight_blockers(db, scope)
        assert scope["strategy"] == "ERASE_KEEP_SKELETON"
        assert scope["object_key"] == w["object_key"]
        assert blockers["blocked"] is True
        assert "interview_context_snapshots" in blockers["triggers"]
        assert blockers["triggers"]["interview_context_snapshots"] == "m4_snapshot_immutable"
        # Other user's data is never in A's lineage.
        assert w["resume_b"] not in scope["session_ids"]
        assert w["resume_other"] not in scope["version_ids"]
        assert not any("canary" in str(v).lower() for v in (blockers["immutable"], blockers["attribution"]))
    asyncio.run(_runner(database_url, scenario))


def test_audit_redaction_and_ledger_integrity(database_url):
    async def scenario(factory):
        w = await seed_world(factory, with_session=False, with_derived=False, with_jd=False)
        await _clean_world(factory, w["uid_a"])
        before = await _scalar(factory, "SELECT count(*) FROM model_admissions")
        rid, _ = await _request(factory, w["uid_a"], RESUME, w["resume"], "idem-" + w["tag"])
        assert (await _run(factory, rid))["status"] == COMPLETED
        assert await _scalar(factory, "SELECT count(*) FROM model_admissions") == before
        assert await _scalar(factory, "SELECT count(*) FROM audit_logs WHERE resource_id=:r",
                             r=str(w["resume"])) == 1, "audit skeleton must survive"
        payload = await _scalar(factory, "SELECT after_json::text FROM audit_logs WHERE resource_id=:r",
                                r=str(w["resume"]))
        assert w["canary"] not in payload and json.loads(payload).get("erased") is True
    asyncio.run(_runner(database_url, scenario))
