"""Round 10: ownership of a private JD is checked before any other validation.

Before this fix an attacker without a candidate profile got PROFILE_REQUIRED
when referencing someone else's job_target_id, which made the error code an
existence oracle and obscured the real refusal. Missing and foreign ids must
now take the same 404 path from every entry point that builds context.
"""

import pytest

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.training import TrainingService
from interview_api.modules.job_target.schemas import (
    CreateJobTargetRequest, RequirementInput,
)
from interview_api.modules.job_target.service import JobTargetService
from test_job_target import JD, create_target, run, seed_user
from test_m3_candidate_profile import seed as seed_resume


async def _owner_with_target_and_no_profile(factory):
    """A real user with a confirmed private target, but no candidate profile."""
    user_id, _ = await seed_resume(factory)
    async with factory() as db, db.begin():
        created = await JobTargetService(db).create(user_id, CreateJobTargetRequest(
            raw_text=JD, company_name="私人公司名称A", job_title="私人岗位A"))
    async with factory() as db, db.begin():
        await JobTargetService(db).update_requirements(user_id, created["id"], [
            RequirementInput(statement="熟练掌握 Python", importance="REQUIRED")])
    async with factory() as db, db.begin():
        await JobTargetService(db).confirm(user_id, created["id"])
    return user_id, created["id"]


def _request(target_id):
    return CreateSessionRequest(job_target_id=target_id, question_count=3,
                                interviewer_skill_id="ai_agent_application",
                                adaptive_interview=True)


def test_foreign_target_is_404_before_the_profile_check(database_url):
    async def scenario(factory):
        owner = await seed_user(factory)
        other, _ = await seed_resume(factory)  # no candidate profile
        target_id = await create_target(factory, owner)
        core = MockInterviewService(factory, ModelRouter({}))
        with pytest.raises(AppError) as failure:
            await core.create(other, _request(target_id))
        assert failure.value.code == "JOB_TARGET_NOT_FOUND"
        assert failure.value.status_code == 404
        text = str(failure.value.message)
        assert "私人" not in text and "JD" not in text and "画像" not in text
    run(database_url, scenario)


def test_missing_target_is_404_before_the_profile_check(database_url):
    async def scenario(factory):
        other, _ = await seed_resume(factory)
        core = MockInterviewService(factory, ModelRouter({}))
        with pytest.raises(AppError) as failure:
            await core.create(other, _request(999_999_999))
        assert failure.value.code == "JOB_TARGET_NOT_FOUND" and failure.value.status_code == 404
    run(database_url, scenario)


def test_training_workbench_rejects_foreign_target_with_the_same_404(database_url):
    async def scenario(factory):
        owner = await seed_user(factory)
        other, _ = await seed_resume(factory)
        target_id = await create_target(factory, owner)
        with pytest.raises(AppError) as failure:
            await TrainingService(factory).workbench(other, _request(target_id))
        assert failure.value.code == "JOB_TARGET_NOT_FOUND" and failure.value.status_code == 404
    run(database_url, scenario)


def test_owner_without_profile_still_sees_PROFILE_REQUIRED(database_url):
    async def scenario(factory):
        user_id, target_id = await _owner_with_target_and_no_profile(factory)
        core = MockInterviewService(factory, ModelRouter({}))
        with pytest.raises(AppError) as failure:
            await core.create(user_id, _request(target_id))
        assert failure.value.code == "PROFILE_REQUIRED" and failure.value.status_code == 409
        # The normal public path keeps its own hint too.
        with pytest.raises(AppError) as public:
            await core.create(user_id, CreateSessionRequest(
                company_id=1, job_id=1, question_count=3,
                interviewer_skill_id="ai_agent_application", adaptive_interview=True))
        assert public.value.code == "PROFILE_REQUIRED"
    run(database_url, scenario)


def test_foreign_user_cannot_list_or_read_private_material(database_url):
    async def scenario(factory):
        owner = await seed_user(factory)
        other, _ = await seed_resume(factory)
        target_id = await create_target(factory, owner)
        async with factory() as db:
            assert await JobTargetService(db).history(other, 20, 0) == []
            for method in ("read", "match_preview"):
                with pytest.raises(AppError) as failure:
                    await getattr(JobTargetService(db), method)(other, target_id)
                assert failure.value.code == "JOB_TARGET_NOT_FOUND"
            # A direct id swap on update/delete is refused the same way.
            with pytest.raises(AppError) as failure:
                await JobTargetService(db).update_requirements(other, target_id, [
                    RequirementInput(statement="overwrite")])
            assert failure.value.code == "JOB_TARGET_NOT_FOUND"
            with pytest.raises(AppError) as failure:
                await JobTargetService(db).delete(other, target_id)
            assert failure.value.code == "JOB_TARGET_NOT_FOUND"
    run(database_url, scenario)
