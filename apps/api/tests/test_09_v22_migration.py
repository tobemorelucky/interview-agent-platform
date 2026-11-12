"""Only the explicitly configured isolated PostgreSQL test DB is migrated."""
from test_08_v13_migration import _run_alembic


def test_0026_isolated_upgrade_downgrade(database_url):
    from sqlalchemy.engine import make_url
    assert make_url(database_url).database.startswith("interview_agent_v21_"), (
        "migration tests only run against an isolated interview_agent_v21_* database")
    _run_alembic(database_url, "upgrade", "head")
    _run_alembic(database_url, "downgrade", "0025")
    _run_alembic(database_url, "upgrade", "head")
