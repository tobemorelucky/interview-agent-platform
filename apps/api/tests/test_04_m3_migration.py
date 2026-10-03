import asyncio
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from test_00_migration import _run_alembic


async def tables(url):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as db:
            return await db.run_sync(lambda conn: set(inspect(conn).get_table_names()))
    finally:
        await engine.dispose()


def test_0021_upgrade_downgrade(database_url):
    _run_alembic(database_url, "upgrade", "0021")
    assert {"candidate_profiles", "candidate_profile_versions", "resume_evidence", "candidate_user_skills"} <= asyncio.run(tables(database_url))
    _run_alembic(database_url, "downgrade", "0020")
    assert "candidate_profiles" not in asyncio.run(tables(database_url))
    _run_alembic(database_url, "upgrade", "0021")
