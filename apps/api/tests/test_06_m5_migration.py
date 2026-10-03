"""Upgrade/downgrade only on an explicitly isolated, empty test database."""
import asyncio

from test_00_migration import _run_alembic
from test_04_m3_migration import tables


def test_0023_upgrade_downgrade(database_url):
    _run_alembic(database_url, "upgrade", "0023")
    assert {"final_interview_reports", "interview_performance_observations", "memory_proposals"} <= asyncio.run(tables(database_url))
    _run_alembic(database_url, "downgrade", "0022")
    assert "final_interview_reports" not in asyncio.run(tables(database_url))
    assert "interview_context_snapshots" in asyncio.run(tables(database_url))
    _run_alembic(database_url, "upgrade", "0023")
