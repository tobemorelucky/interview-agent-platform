from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine


API_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic(database_url: str, *args: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


async def _state(database_url: str) -> tuple[set[str], set[str], set[str]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            def inspect_schema(sync_connection):
                inspector = inspect(sync_connection)
                return (
                    set(inspector.get_table_names()),
                    {
                        item["name"]
                        for item in inspector.get_columns("source_snapshots")
                    },
                    {
                        item["name"]
                        for item in inspector.get_columns(
                            "experience_collection_tasks"
                        )
                    },
                )

            return await connection.run_sync(inspect_schema)
    finally:
        await engine.dispose()


def test_0019_upgrade_and_downgrade(database_url: str) -> None:
    _run_alembic(database_url, "upgrade", "0019")
    tables, snapshot_columns, legacy_columns = asyncio.run(_state(database_url))
    assert "tool_invocations" in tables
    assert "created_by_tool_invocation_id" in snapshot_columns
    assert "public_refresh_task_id" in legacy_columns

    _run_alembic(database_url, "downgrade", "0018")
    tables, snapshot_columns, legacy_columns = asyncio.run(_state(database_url))
    assert "tool_invocations" not in tables
    assert "created_by_tool_invocation_id" not in snapshot_columns
    assert "public_refresh_task_id" not in legacy_columns

    _run_alembic(database_url, "upgrade", "0019")
