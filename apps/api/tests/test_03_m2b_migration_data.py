import asyncio
import subprocess

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

import interview_api.modules.models  # noqa: F401
from interview_api.modules.experience.models import (
    ExperienceCollectionTask, ExperienceSourceItem, ExperienceAgentRun, ExperienceAgentStepRun,
)
from interview_api.modules.public_career.models import PublicDataRefreshTask
from test_00_migration import _run_alembic


async def seed(url):
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            legacy = ExperienceCollectionTask()
            task = PublicDataRefreshTask(target_type="JOB_JD")
            db.add_all([legacy, task])
            await db.flush()
            source = ExperienceSourceItem(task_id=legacy.id, source_url="https://migration.example/test",
                                          normalized_url_hash="a" * 64)
            db.add(source)
            await db.flush()
            old_run = ExperienceAgentRun(source_item_id=source.id, task_id=legacy.id, status="SUCCEEDED")
            public_run = ExperienceAgentRun(refresh_task_id=task.id, agent_role="PublicDataResearchAgent", status="FAILED")
            db.add_all([old_run, public_run])
            await db.flush()
            db.add(ExperienceAgentStepRun(agent_run_id=public_run.id, step_name="test", status="FAILED"))
            await db.commit()
            return legacy.id, old_run.id, public_run.id, task.id
    finally:
        await engine.dispose()


async def check_and_archive_fixture(url, ids):
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as db:
            assert await db.scalar(text("SELECT count(*) FROM experience_agent_runs WHERE id IN (:old, :new)"),
                                   {"old": ids[1], "new": ids[2]}) == 2
            # Only test-owned records are removed to emulate explicit archival.
            await db.execute(delete(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == ids[2]))
            await db.execute(delete(ExperienceAgentRun).where(ExperienceAgentRun.id == ids[2]))
            await db.execute(delete(PublicDataRefreshTask).where(PublicDataRefreshTask.id == ids[3]))
            await db.commit()
    finally:
        await engine.dispose()


async def verify_legacy(url, run_id):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as db:
            assert await db.scalar(text("SELECT status FROM experience_agent_runs WHERE id=:id"), {"id": run_id}) == "SUCCEEDED"
    finally:
        await engine.dispose()


def test_downgrade_preserves_legacy_and_refuses_silent_audit_loss(database_url):
    ids = asyncio.run(seed(database_url))
    with pytest.raises(subprocess.CalledProcessError) as error:
        _run_alembic(database_url, "downgrade", "0019")
    assert "archiving public-only agent audits" in error.value.stderr
    asyncio.run(check_and_archive_fixture(database_url, ids))
    _run_alembic(database_url, "downgrade", "0019")
    asyncio.run(verify_legacy(database_url, ids[1]))
    _run_alembic(database_url, "upgrade", "0020")
    asyncio.run(verify_legacy(database_url, ids[1]))
