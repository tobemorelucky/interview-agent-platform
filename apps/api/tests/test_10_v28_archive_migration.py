"""0028 archive lifecycle: two nullable columns, upgrade/downgrade both safe."""
from test_00_migration import _run_alembic


def test_0028_upgrade_downgrade(database_url):
    import asyncio

    from sqlalchemy import inspect
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    assert make_url(database_url).database.startswith("interview_agent_v21_"), (
        "migration tests only run against an isolated interview_agent_v21_* database")

    async def columns(table):
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as conn:
                return {c["name"] for c in await conn.run_sync(lambda c: inspect(c).get_columns(table))}
        finally:
            await engine.dispose()

    _run_alembic(database_url, "upgrade", "head")
    assert "archived_at" in asyncio.run(columns("resumes"))
    assert "archived_at" in asyncio.run(columns("user_job_targets"))

    # Downgrade only removes the lifecycle columns; 0027's snapshot CHECK and
    # the 0026 memory tables must survive untouched.
    _run_alembic(database_url, "downgrade", "0027")
    assert "archived_at" not in asyncio.run(columns("resumes"))
    assert "archived_at" not in asyncio.run(columns("user_job_targets"))
    # 0027's own objects are untouched by the 0028 downgrade.
    assert "parse_status" in asyncio.run(columns("user_job_targets"))

    _run_alembic(database_url, "upgrade", "head")
    assert "archived_at" in asyncio.run(columns("resumes"))
