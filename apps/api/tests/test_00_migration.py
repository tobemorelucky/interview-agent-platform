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


async def _schema_state(database_url: str) -> tuple[set[str], set[str], set[str]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            def inspect_schema(sync_connection):
                inspector = inspect(sync_connection)
                tables = set(inspector.get_table_names())
                experience_columns = {
                    column["name"]
                    for column in inspector.get_columns("interview_experiences")
                }
                question_columns = {
                    column["name"]
                    for column in inspector.get_columns("interview_questions")
                }
                return tables, experience_columns, question_columns

            return await connection.run_sync(inspect_schema)
    finally:
        await engine.dispose()


def test_0018_upgrade_and_downgrade(database_url: str) -> None:
    _run_alembic(database_url, "upgrade", "0018")
    tables, experience_columns, question_columns = asyncio.run(
        _schema_state(database_url)
    )
    assert {
        "companies",
        "company_aliases",
        "jobs",
        "job_description_versions",
        "job_requirements",
        "job_tags",
        "sources",
        "source_snapshots",
        "claims",
        "claim_evidence",
        "public_data_refresh_tasks",
    } <= tables
    assert {"company_id", "job_id", "content_fingerprint"} <= experience_columns
    assert {"company_id", "job_id", "question_fingerprint"} <= question_columns

    _run_alembic(database_url, "downgrade", "0017")
    tables, experience_columns, question_columns = asyncio.run(
        _schema_state(database_url)
    )
    assert "companies" not in tables
    assert "company_id" not in experience_columns
    assert "job_id" not in question_columns
    assert "company" in experience_columns
    assert "position" in question_columns

    _run_alembic(database_url, "upgrade", "0018")

