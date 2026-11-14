"""L1 resume archive: owner-scoped, idempotent, and out of all new business.

An archived resume keeps its file, report and profile; only *new* use stops.
"""
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError, ResumeNotFoundError
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
from interview_api.modules.candidate_profile.router import generate
from interview_api.modules.candidate_profile.schemas import GenerationRequest
from interview_api.modules.resume.models import Resume
from interview_api.modules.resume.service import ResumeService
from test_job_target import run
from test_m3_candidate_profile import model_router, response as profile_response, seed as seed_resume
from test_job_target import seed_user


async def _archive(factory, resume_id, user_id):
    async with factory() as db, db.begin():
        return await ResumeService(db).archive_resume(resume_id, user_id)


async def _set_status(factory, resume_id, status):
    async with factory() as db, db.begin():
        await db.execute(update(Resume).where(Resume.id == resume_id).values(status=status))


def test_archive_is_idempotent_and_blocked_while_processing(database_url):
    async def scenario(factory):
        user_id, resume_id = await seed_resume(factory)

        # A processing resume cannot be archived: the Celery task owns status.
        await _set_status(factory, resume_id, "PROCESSING")
        with pytest.raises(AppError) as err:
            await _archive(factory, resume_id, user_id)
        assert err.value.code == "RESUME_PROCESSING"

        await _set_status(factory, resume_id, "COMPLETED")
        first = await _archive(factory, resume_id, user_id)
        assert first["archived_at"] is not None
        second = await _archive(factory, resume_id, user_id)
        assert second["archived_at"] == first["archived_at"], "archive must be idempotent"

        # The default list hides it; the management view opts in.
        async with factory() as db:
            default = await ResumeService(db).list_resumes(user_id)
            incl = await ResumeService(db).list_resumes(user_id, include_archived=True)
        assert all(item["id"] != resume_id for item in default["items"])
        assert any(item["id"] == resume_id and item["archived_at"] for item in incl["items"])

        # Restore revalidates state: restoring a non-archived row is refused.
        async with factory() as db, db.begin():
            await ResumeService(db).restore_resume(resume_id, user_id)
        async with factory() as db, db.begin():
            with pytest.raises(AppError) as err:
                await ResumeService(db).restore_resume(resume_id, user_id)
        assert err.value.code == "RESUME_NOT_ARCHIVED"
        async with factory() as db:
            default = await ResumeService(db).list_resumes(user_id)
        assert any(item["id"] == resume_id for item in default["items"])

    run(database_url, scenario)


def test_archive_is_owner_scoped(database_url):
    async def scenario(factory):
        owner_id, resume_id = await seed_resume(factory)
        other_id, _ = await seed_resume(factory)
        with pytest.raises(ResumeNotFoundError):
            await _archive(factory, resume_id, other_id)
        async with factory() as db:
            resume = await db.get(Resume, resume_id)
            assert resume.archived_at is None, "a foreign archive attempt changes nothing"

    run(database_url, scenario)


def test_profile_generation_refuses_an_archived_resume(database_url):
    async def scenario(factory):
        user_id, resume_id = await seed_resume(factory)
        await _archive(factory, resume_id, user_id)
        from interview_api.modules.users.models import User
        async with factory() as db:
            user = await db.get(User, user_id)
            with pytest.raises(AppError) as err:
                await generate(GenerationRequest(resume_id=resume_id), db=db, user=user)
        assert err.value.code == "RESUME_ARCHIVED"

    run(database_url, scenario)


def test_redelivery_does_not_resurrect_a_profile_for_an_archived_resume(database_url, monkeypatch):
    from interview_api.modules.resume import processor

    async def scenario(factory):
        user_id, resume_id = await seed_resume(factory)
        await _archive(factory, resume_id, user_id)

        called = []

        async def recorder(rid):
            called.append(rid)

        from interview_api.modules.candidate_profile import agent as profile_agent
        monkeypatch.setattr(profile_agent, "generate_profile_best_effort", recorder)
        # Same task id as the original: exactly the Celery redelivery shape.
        await processor.process_resume_async(resume_id, task_id="original-task-id")
        assert called == [], "an archived resume must not gain a new profile"

    run(database_url, scenario)


def test_archived_resume_blocks_new_sessions(database_url):
    from interview_api.modules.interview.context import build_context
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from test_job_target import create_target

    async def scenario(factory):
        # A real profile whose source resume we then archive.
        user_id, resume_id = await seed_resume(factory)
        router, _ = model_router([profile_response(resume_id)])
        async with factory() as db:
            profile = await ResumeProfileAgent(factory, router).run(resume_id)
        assert profile["status"] == "COMPLETED", profile
        target_id = await create_target(factory, user_id)

        async def build():
            async with factory() as db:
                return await build_context(db, user_id, CreateSessionRequest(
                    job_target_id=target_id, question_count=3,
                    interviewer_skill_id="ai_agent_application", adaptive_interview=True))

        context, _, _ = await build()
        assert context["candidate_profile_version_id"]

        await _archive(factory, resume_id, user_id)
        with pytest.raises(AppError) as err:
            await build()
        assert err.value.code == "RESUME_ARCHIVED"

    run(database_url, scenario)
