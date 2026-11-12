"""Round 10: a confirmed private JD can run real training, and stays private.

The training centre was built for published jobs. A user-supplied JD has no
company/job/JD row at all, so every path here must work from the frozen
``job_target`` alone: no public job is created, no public question pool is
consulted, and WEAKNESS/RECHECK stay impossible until a real evaluation exists.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import (
    ANSWER_EVALUATION_ROUTE, INTERVIEW_MAIN_ROUTE, ModelRouter,
)
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.training import TrainingService
from interview_api.modules.job_target.schemas import RequirementInput
from interview_api.modules.job_target.service import JobTargetService
from interview_api.modules.public_career.models import (
    Company, Job, JobDescriptionVersion, JobRequirement,
)
from test_job_target import create_target, seed_user
from v22_support import MemoryReplayProvider

SOURCE_LABEL = "用户提供（未经企业核实）"


def request(target_id, **extra):
    """Private-target training request; the caller has a profile but no public job."""
    extra.setdefault("question_count", 4)
    return CreateSessionRequest(
        job_target_id=target_id, interviewer_skill_id="java_backend",
        adaptive_interview=True, **extra)


async def public_rows(factory):
    """Counts of every table a private target must never write to."""
    async with factory() as db:
        return tuple([
            await db.scalar(select(func.count()).select_from(model))
            for model in (Company, Job, JobDescriptionVersion, JobRequirement)])


def test_private_target_board_reports_its_own_source_and_no_invented_weakness(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        board = await TrainingService(factory).workbench(user_id, request(target_id, question_count=8))
        # Identity comes from the private target; public ids stay empty so the
        # frontend cannot accidentally re-open a company job.
        assert board["job_target_id"] == target_id
        assert board["company_id"] is None and board["job_id"] is None
        assert board["source_label"] == SOURCE_LABEL
        assert board["company_name"] == "某互联网公司"
        assert board["targets"], "a confirmed JD must still yield legal training goals"
        # No evaluation exists yet: only the full simulation may be offered.
        assert all(t["available_modes"] == ["FULL"] for t in board["targets"])
        assert board["coverage"]["reviewed_question_count"] == 0
        assert board["coverage"]["fallback"] == "GENERIC_SKILL"
    run(database_url, scenario)


def test_private_full_training_freezes_the_confirmed_version_and_creates_no_public_rows(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        before = await public_rows(factory)
        core = MockInterviewService(factory, ModelRouter(
            {route: MemoryReplayProvider() for route in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))
        created = await core.create(user_id, request(
            target_id, question_count=1, training_mode="FULL", training_request_key=uuid4().hex))
        snapshot = created["context_snapshot"]
        assert snapshot["job_target"]["id"] == target_id
        assert snapshot["job_target"]["version"] == 1
        assert snapshot["company_id"] is None and snapshot["job_id"] is None
        assert snapshot["jd_version_number"] is None
        # The user edits and re-confirms the same JD after the session started.
        async with factory() as db, db.begin():
            await JobTargetService(db).update_requirements(user_id, target_id, [
                RequirementInput(statement="熟悉 Kubernetes 调度", importance="REQUIRED")])
        async with factory() as db, db.begin():
            reconfirmed = await JobTargetService(db).confirm(user_id, target_id)
        assert reconfirmed["version"] == 2
        # The started session keeps the version it froze; no silent rewrite.
        detail = await core.detail(created["id"], user_id)
        assert detail["context_snapshot"] == snapshot
        # A private target never materialises as a public company/job/JD/requirement.
        assert await public_rows(factory) == before
        # And it stays invisible to every other account.
        with pytest.raises(AppError) as denied:
            await TrainingService(factory).feedback(user_id + 999_999, created["id"])
        assert denied.value.status_code == 404
    run(database_url, scenario)


def test_private_weakness_needs_a_real_evaluation_before_it_is_offered(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        service = TrainingService(factory)
        core = MockInterviewService(factory, ModelRouter(
            {route: MemoryReplayProvider() for route in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))
        board = await service.workbench(user_id, request(target_id, question_count=8))
        goal = next(t["knowledge_id"] for t in board["targets"])
        # Nothing has been evaluated: a focused mode must be refused, not invented.
        with pytest.raises(AppError) as denied:
            await core.create(user_id, request(
                target_id, question_count=1, training_mode="WEAKNESS",
                training_knowledge_id=goal, training_request_key=uuid4().hex))
        assert denied.value.code == "TRAINING_TARGET_INVALID"
        assert denied.value.status_code == 422
        # One real private session produces a real legal observation.
        full = await core.create(user_id, request(
            target_id, question_count=1, training_mode="FULL", training_request_key=uuid4().hex))
        question = await core.start(full["id"], user_id)
        await core.answer(full["id"], user_id, question["question_id"], uuid4().hex, "Kafka ACK，失败重试")
        after = await service.workbench(user_id, request(target_id, question_count=8))
        observed = [t for t in after["targets"] if "WEAKNESS" in t["available_modes"]]
        assert observed, "a real evaluation must unlock a legal weakness goal"
        goal = observed[0]["knowledge_id"]
        focused = await core.create(user_id, request(
            target_id, question_count=1, training_mode="WEAKNESS",
            training_knowledge_id=goal, training_request_key=uuid4().hex))
        frozen = focused["context_snapshot"]
        assert [t["knowledge_id"] for t in frozen["adaptive_blueprint"]["targets"]] == [goal]
        assert frozen["job_target"]["id"] == target_id
        assert frozen["training"]["mode"] == "WEAKNESS"
        assert frozen["company_id"] is None and frozen["job_id"] is None
    run(database_url, scenario)


def test_public_training_entry_is_unchanged_for_the_same_user(database_url):
    async def scenario(factory):
        from v22_support import kafka_seed
        user_id, ctx = await kafka_seed(factory)
        board = await TrainingService(factory).workbench(user_id, CreateSessionRequest(
            company_id=ctx["company_id"], job_id=ctx["job_id"],
            interviewer_skill_id="java_backend", adaptive_interview=True, question_count=8))
        assert board["job_target_id"] is None
        assert board["source_label"] is None
        assert board["company_id"] == ctx["company_id"] and board["job_id"] == ctx["job_id"]
        assert board["targets"]
    run(database_url, scenario)


def run(database_url, scenario):
    async def main():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await scenario(factory)
        finally:
            await engine.dispose()
    asyncio.run(main())
