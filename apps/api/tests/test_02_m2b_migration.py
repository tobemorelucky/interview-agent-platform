import asyncio
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from test_00_migration import _run_alembic


async def columns(url):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as db:
            return await db.run_sync(lambda conn: {
                name: {col["name"]: col for col in inspect(conn).get_columns(name)}
                for name in ("experience_agent_runs", "experience_agent_step_runs",
                             "job_description_versions", "interview_experiences", "interview_questions")
            })
    finally:
        await engine.dispose()


def test_0020_upgrade_downgrade(database_url):
    _run_alembic(database_url, "upgrade", "0020")
    state = asyncio.run(columns(database_url))
    assert state["experience_agent_runs"]["source_item_id"]["nullable"]
    assert "refresh_task_id" in state["experience_agent_runs"]
    assert "structured_retry" in state["experience_agent_step_runs"]
    assert "publication_status" in state["job_description_versions"]
    assert "source_snapshot_id" in state["interview_experiences"]
    assert "claim_id" in state["interview_questions"]
    _run_alembic(database_url, "downgrade", "0019")
    state = asyncio.run(columns(database_url))
    assert not state["experience_agent_runs"]["source_item_id"]["nullable"]
    assert "refresh_task_id" not in state["experience_agent_runs"]
    assert "claim_id" not in state["interview_questions"]
    _run_alembic(database_url, "upgrade", "0020")
