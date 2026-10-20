"""Only isolated test DB; bring full regression beyond legacy 0024 tests."""
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
import asyncio
from test_00_migration import _run_alembic


def test_0025_upgrade_downgrade(database_url):
    _run_alembic(database_url, "upgrade", "0025")
    async def tables():
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(lambda c: inspect(c).get_table_names())
        finally:
            await engine.dispose()
    assert "model_admissions" in asyncio.run(tables())
    _run_alembic(database_url, "downgrade", "0024")
    assert "model_admissions" not in asyncio.run(tables())
    _run_alembic(database_url, "upgrade", "0025")
