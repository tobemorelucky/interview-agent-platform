"""L2 private-JD archive: idempotent, owner-scoped, out of all new business.

Frozen session snapshots, reports and the ledger are untouched by an archive.
"""
import pytest
from sqlalchemy import select

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.context import build_context
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.models import InterviewContextSnapshot
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.job_target.models import UserJobTarget
from interview_api.modules.job_target.schemas import CreateJobTargetRequest, RequirementInput
from interview_api.modules.job_target.service import JobTargetService
from test_job_target import create_target, run, seed_user


async def _archive(factory, user_id, target_id):
    async with factory() as db, db.begin():
        return await JobTargetService(db).archive(user_id, target_id)


async def _build_context(factory, user_id, target_id):
    async with factory() as db:
        return await build_context(db, user_id, CreateSessionRequest(
            job_target_id=target_id, question_count=3,
            interviewer_skill_id="ai_agent_application", adaptive_interview=True))


def test_archive_is_idempotent_owner_scoped_and_restorable(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        other_id = await seed_user(factory)

        with pytest.raises(AppError) as err:
            await _archive(factory, other_id, target_id)
        assert err.value.code == "JOB_TARGET_NOT_FOUND"

        first = await _archive(factory, user_id, target_id)
        second = await _archive(factory, user_id, target_id)
        assert first["archived_at"] == second["archived_at"] is not None

        async with factory() as db:
            target = await db.get(UserJobTarget, target_id)
            # Archive is a lifecycle flag only: the confirmed state is intact.
            assert target.parse_status == "CONFIRMED" and target.version == 1

        async with factory() as db, db.begin():
            data = await JobTargetService(db).restore(user_id, target_id)
        assert data["archived_at"] is None and data["parse_status"] == "CONFIRMED"
        async with factory() as db:
            target = await db.get(UserJobTarget, target_id)
            assert target.archived_at is None

        # Restoring a target that is not archived is refused, mirroring resumes.
        async with factory() as db, db.begin():
            with pytest.raises(AppError) as err:
                await JobTargetService(db).restore(user_id, target_id)
        assert err.value.code == "JOB_TARGET_NOT_ARCHIVED"

    run(database_url, scenario)


def test_archived_target_is_out_of_new_business(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        await _archive(factory, user_id, target_id)

        # edit
        async with factory() as db, db.begin():
            with pytest.raises(AppError) as err:
                await JobTargetService(db).update_requirements(user_id, target_id,
                    [RequirementInput(statement="归档后还想改", importance="UNKNOWN")])
        assert err.value.code == "JOB_TARGET_ARCHIVED"

        # confirm
        async with factory() as db, db.begin():
            with pytest.raises(AppError) as err:
                await JobTargetService(db).confirm(user_id, target_id)
        assert err.value.code == "JOB_TARGET_ARCHIVED"

        # recommendations and match preview
        async with factory() as db:
            with pytest.raises(AppError) as err:
                await JobTargetService(db).skill_recommendations(user_id, target_id)
        assert err.value.code == "JOB_TARGET_ARCHIVED"
        async with factory() as db:
            with pytest.raises(AppError) as err:
                await JobTargetService(db).match_preview(user_id, target_id)
        assert err.value.code == "JOB_TARGET_ARCHIVED"

        # default history hides it; the management view opts in
        async with factory() as db:
            default = await JobTargetService(db).history(user_id, 20, 0)
            incl = await JobTargetService(db).history(user_id, 20, 0, include_archived=True)
        assert all(row["id"] != target_id for row in default)
        assert any(row["id"] == target_id and row["archived_at"] for row in incl)

        # new session / training through the direct id must be refused
        with pytest.raises(AppError) as err:
            await _build_context(factory, user_id, target_id)
        assert err.value.code == "JOB_TARGET_ARCHIVED"

    run(database_url, scenario)


def test_sessions_created_before_the_archive_keep_their_frozen_snapshot(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)

        core = MockInterviewService(factory, ModelRouter({}))
        created = await core.create(user_id, CreateSessionRequest(
            job_target_id=target_id, question_count=3,
            interviewer_skill_id="ai_agent_application", adaptive_interview=True))
        frozen = created["context_snapshot"]
        assert frozen["job_target"]["version"] == 1

        await _archive(factory, user_id, target_id)

        async with factory() as db:
            snapshot = await db.scalar(select(InterviewContextSnapshot)
                .where(InterviewContextSnapshot.job_target_id == target_id))
        assert snapshot is not None, "archiving must not break the historical reference"
        assert snapshot.context_json["jd_fingerprint"] == frozen["jd_fingerprint"]

        # The old session's target can no longer create a *second* session.
        with pytest.raises(AppError) as err:
            await _build_context(factory, user_id, target_id)
        assert err.value.code == "JOB_TARGET_ARCHIVED"

    run(database_url, scenario)


def test_detail_view_still_opens_for_an_archived_target(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        await _archive(factory, user_id, target_id)
        async with factory() as db:
            data = JobTargetService(db).serialize(await JobTargetService(db).read(user_id, target_id))
        assert data["archived_at"] is not None
        assert data["raw_text"], "the owner can still review the archived material"
    run(database_url, scenario)
