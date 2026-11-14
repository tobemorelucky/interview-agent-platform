"""0029 purge_requests: upgrade 0027->0028->0029 and a safe downgrade.

The downgrade drops ONLY the new table. 0028's lifecycle columns and 0027's
snapshot CHECK must survive. Also proves a data-bearing purge request row does
not break the downgrade (nothing else in the schema references this table).
"""
import asyncio

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from test_00_migration import _run_alembic


def _tables(database_url):
    async def run():
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as conn:
                return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
        finally:
            await engine.dispose()
    return asyncio.run(run())


def _columns(database_url, table):
    async def run():
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as conn:
                return {c["name"] for c in await conn.run_sync(lambda c: inspect(c).get_columns(table))}
        finally:
            await engine.dispose()
    return asyncio.run(run())


def _insert_request(database_url):
    async def run():
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO users (email, password_hash, username, role) "
                    "VALUES ('r23-mig@example.invalid','x','r23mig','USER')"))
                uid = (await conn.execute(text("SELECT id FROM users WHERE email='r23-mig@example.invalid'"))).scalar()
                await conn.execute(text(
                    "INSERT INTO purge_requests (user_id, resource_type, resource_id, idempotency_key, "
                    "status, policy_version) VALUES (:u,'RESUME',1,'mig-key','FENCED','r23-l3-1')"),
                    dict(u=uid))
                return (await conn.execute(text("SELECT count(*) FROM purge_requests"))).scalar()
        finally:
            await engine.dispose()
    return asyncio.run(run())


def test_0029_upgrade_downgrade(database_url):
    assert make_url(database_url).database.startswith("interview_agent_v21_"), (
        "migration tests only run against an isolated interview_agent_v21_* database")

    # Other migration cases in the same pass may already be at head. Start from
    # a known state by going DOWN to 0027, then walk the real 0027->0028->0029
    # chain. Alembic ignores an already-lower target, so downgrade explicitly.
    _run_alembic(database_url, "upgrade", "head")
    _run_alembic(database_url, "downgrade", "0027")
    assert "archived_at" not in _columns(database_url, "resumes")
    assert "purge_requests" not in _tables(database_url)

    _run_alembic(database_url, "upgrade", "0028")
    assert "archived_at" in _columns(database_url, "resumes")
    assert "purge_requests" not in _tables(database_url)

    _run_alembic(database_url, "upgrade", "head")  # 0029
    assert "purge_requests" in _tables(database_url)
    cols = _columns(database_url, "purge_requests")
    for required in ("user_id", "resource_type", "resource_id", "idempotency_key",
                     "status", "policy_version", "scope_json", "completed_steps",
                     "object_key", "error_code", "error_diagnostic", "attempts",
                     "residual_json", "created_at", "fenced_at", "cleaning_at",
                     "verifying_at", "completed_at"):
        assert required in cols, required

    # A real row (with a FK to users) round-trips; the fence design is usable.
    assert _insert_request(database_url) == 1

    # Downgrade 0029 -> 0028 removes ONLY the new table.
    _run_alembic(database_url, "downgrade", "0028")
    assert "purge_requests" not in _tables(database_url)
    assert "archived_at" in _columns(database_url, "resumes"), "0028 must survive"
    assert "archived_at" in _columns(database_url, "user_job_targets")
    assert "parse_status" in _columns(database_url, "user_job_targets"), "0027 must survive"

    _run_alembic(database_url, "upgrade", "head")
    assert "purge_requests" in _tables(database_url)
