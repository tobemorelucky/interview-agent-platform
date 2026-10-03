import asyncio
from test_00_migration import _run_alembic
from test_04_m3_migration import tables


def test_0022_upgrade_downgrade(database_url):
    _run_alembic(database_url, "upgrade", "0022")
    assert {"interview_context_snapshots", "interview_answers", "interview_evaluations", "interview_agent_calls"} <= asyncio.run(tables(database_url))
    _run_alembic(database_url, "downgrade", "0021")
    assert "interview_evaluations" not in asyncio.run(tables(database_url))
    _run_alembic(database_url, "upgrade", "0022")
