import asyncio
from test_00_migration import _run_alembic
from test_04_m3_migration import tables


def test_0024_upgrade_downgrade(database_url):
    _run_alembic(database_url, "upgrade", "0024")
    assert {"job_match_runs", "job_match_candidates", "job_match_recommendations", "job_match_attempts"} <= asyncio.run(tables(database_url))
    _run_alembic(database_url, "downgrade", "0023")
    assert "job_match_runs" not in asyncio.run(tables(database_url))
    assert "final_interview_reports" in asyncio.run(tables(database_url))
    _run_alembic(database_url, "upgrade", "0024")
