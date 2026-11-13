"""Round 11: which states may legally enter RECHECK, and on whose JD.

The rule (from `training.modes` and `capability_rules.project`) is narrow:

    RECHECK  <- state in ("CONFLICTED", "RECHECK_DUE"), never revoked
    WEAKNESS <- state in ("GAP_SUSPECTED", "PRACTICING")
    FULL     <- always

`CONFLICTED` needs two *independent* observations on the same scope where an
earlier one is good and a later one is not, and no later independent recheck has
resolved it. `RECHECK_DUE` needs a VERIFIED topic whose last independent
verification is older than 30/60/90 days.

Nothing here writes a `VERIFIED`/`CONFLICTED` projection by hand. Every
observation comes from a real evaluation produced by the replay provider; the
only thing the expiry test moves is the read clock (the service already takes a
`clock`, and `TrainingService` is pointed at it, so no production code changed).

The history below is deterministic for this fixture (a private JD whose
requirements are Python + Kafka/Redis): the blueprint rotates redis first, so the
sequence GOOD, GOOD, WEAK, GOOD, WEAK reaches CONFLICTED on `kafka_reliability`.
Each step asserts the state it is supposed to produce, so a rotation change fails
loudly instead of silently testing the wrong thing.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
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
from interview_api.modules.memory.capability_models import CapabilityMemory
from interview_api.modules.memory.capability_service import CapabilityMemoryService
from test_job_target import create_target, seed_user
from v22_support import MemoryReplayProvider

GOOD = "Kafka 事务和唯一键，提交后 ACK，失败恢复"
WEAK = "Kafka ACK，失败重试"
CONFLICT_HISTORY = [GOOD, GOOD, WEAK, GOOD, WEAK]
VERIFIED_HISTORY = [GOOD, GOOD]


def request(target_id, **extra):
    extra.setdefault("question_count", 1)
    return CreateSessionRequest(job_target_id=target_id, interviewer_skill_id="java_backend",
        adaptive_interview=True, **extra)


def core_for(factory):
    provider = MemoryReplayProvider()
    return MockInterviewService(factory, ModelRouter(
        {route: provider for route in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))


async def session_for(factory, user_id, target_id, answer):
    """One private FULL session with a single answered question."""
    core = core_for(factory)
    created = await core.create(user_id, request(
        target_id, training_mode="FULL", training_request_key=uuid4().hex))
    question = await core.start(created["id"], user_id)
    await core.answer(created["id"], user_id, question["question_id"], uuid4().hex, answer)
    return created


async def states(factory, user_id, target_id):
    board = await TrainingService(factory).workbench(user_id, request(target_id, question_count=8))
    return board, {t["knowledge_id"].split(":")[-1]: t for t in board["targets"]}


def target_with(board, mode):
    return [t for t in board["targets"] if mode in t["available_modes"]]


def test_a_private_target_with_no_conflict_refuses_recheck(database_url):
    """A single good session is PRACTICING: WEAKNESS yes, RECHECK no."""
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        await session_for(factory, user_id, target_id, GOOD)
        board, index = await states(factory, user_id, target_id)
        assert index["redis"]["state"] == "PRACTICING", index["redis"]["state"]
        assert index["redis"]["available_modes"] == ["FULL", "WEAKNESS"]
        assert not target_with(board, "RECHECK"), [t["state"] for t in board["targets"]]
        core = core_for(factory)
        with pytest.raises(AppError) as denied:
            await core.create(user_id, request(target_id, training_mode="RECHECK",
                training_knowledge_id="java_backend:redis", training_request_key=uuid4().hex))
        assert denied.value.code == "TRAINING_TARGET_INVALID"
        assert denied.value.status_code == 422
        # The unrelated topic is still UNKNOWN and offers nothing but FULL.
        assert index["kafka_reliability"]["state"] == "UNKNOWN"
        assert index["kafka_reliability"]["available_modes"] == ["FULL"]
    body(database_url, scenario)


def test_opposite_independent_answers_make_the_private_target_conflicted(database_url):
    """A good answer, then a weak one, on the same frozen question is a conflict."""
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        for step, answer in enumerate(CONFLICT_HISTORY, 1):
            await session_for(factory, user_id, target_id, answer)
            _, index = await states(factory, user_id, target_id)
            expected = {1: ("redis", "PRACTICING"), 2: ("redis", "VERIFIED"),
                        3: ("kafka_reliability", "GAP_SUSPECTED"),
                        4: ("kafka_reliability", "PRACTICING"),
                        5: ("kafka_reliability", "CONFLICTED")}[step]
            actual = (index[expected[0]]["state"], expected[1])
            assert index[expected[0]]["state"] == expected[1], (step, expected, actual)
        board, index = await states(factory, user_id, target_id)
        target = index["kafka_reliability"]
        assert target["available_modes"] == ["FULL", "RECHECK"], target
        assert any(c["status"] == "OPEN" for c in target["conflicts"]), target["conflicts"]
        # One good independent answer, then a weak one on the same scope: that is
        # a conflict, not two independent successes.
        assert target["independent_sessions"] == 1
        core = core_for(factory)
        # The independent-recheck style guard stays in force on the private path.
        with pytest.raises(AppError) as style:
            await core.create(user_id, request(target_id, training_mode="RECHECK",
                training_knowledge_id=target["knowledge_id"], interviewer_style="gentle",
                training_request_key=uuid4().hex))
        assert style.value.code == "INDEPENDENT_RECHECK_STYLE"
        recheck = await core.create(user_id, request(target_id, training_mode="RECHECK",
            training_knowledge_id=target["knowledge_id"], training_request_key=uuid4().hex))
        snapshot = recheck["context_snapshot"]
        assert snapshot["training"]["mode"] == "RECHECK"
        assert [t["knowledge_id"] for t in snapshot["adaptive_blueprint"]["targets"]] == [target["knowledge_id"]]
        assert snapshot["job_target"]["id"] == target_id
        assert snapshot["company_id"] is None and snapshot["job_id"] is None
        # The frozen plan points at the very evaluations the board showed.
        plan = snapshot["memory_guided_plan"]["items"]
        assert plan, snapshot["memory_guided_plan"]
        assert plan[0]["knowledge_id"] == target["knowledge_id"]
        assert plan[0]["needs_recheck"] is True
        assert all(ref.startswith("evaluation:") for ref in plan[0]["observation_refs"])
        # A conflicting target does not also advertise WEAKNESS.
        assert "WEAKNESS" not in target["available_modes"]
    body(database_url, scenario)


def test_expired_verification_is_the_only_other_legal_recheck_entry(database_url):
    """VERIFIED does not offer RECHECK until the verification actually expires."""
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        for answer in VERIFIED_HISTORY:
            await session_for(factory, user_id, target_id, answer)
        board, index = await states(factory, user_id, target_id)
        redis = index["redis"]
        assert redis["state"] == "VERIFIED", redis["state"]
        # Independent support came from two sessions, not one repeated answer.
        assert redis["independent_sessions"] == 2
        assert redis["available_modes"] == ["FULL"], redis
        assert not target_with(board, "RECHECK")
        core = core_for(factory)
        with pytest.raises(AppError) as denied:
            await core.create(user_id, request(target_id, training_mode="RECHECK",
                training_knowledge_id=redis["knowledge_id"], training_request_key=uuid4().hex))
        assert denied.value.code == "TRAINING_TARGET_INVALID"
        # Move only the read clock: the observations stay exactly as evaluated.
        # `build_context` imports the service lazily from its defining module,
        # while `training.py` bound it at import time, so both names are pointed
        # at the future clock. No row is rewritten.
        from interview_api.modules.interview import training as training_module
        from interview_api.modules.memory import capability_service as memory_module
        future = lambda: datetime.now(timezone.utc) + timedelta(days=120)
        bound = training_module.CapabilityMemoryService
        defining = memory_module.CapabilityMemoryService
        training_module.CapabilityMemoryService = lambda db: bound(db, clock=future)
        memory_module.CapabilityMemoryService = lambda db, **kwargs: defining(db, clock=future, **kwargs)
        try:
            expired, expired_index = await states(factory, user_id, target_id)
            due = expired_index["redis"]
            assert due["state"] == "RECHECK_DUE", due["state"]
            assert due["available_modes"] == ["FULL", "RECHECK"], due
            assert due["independent_sessions"] == 2
            recheck = await core.create(user_id, request(target_id, training_mode="RECHECK",
                training_knowledge_id=due["knowledge_id"], training_request_key=uuid4().hex))
        finally:
            training_module.CapabilityMemoryService = bound
            memory_module.CapabilityMemoryService = defining
        snapshot = recheck["context_snapshot"]
        assert snapshot["training"]["mode"] == "RECHECK"
        assert snapshot["job_target"]["id"] == target_id
        assert snapshot["company_id"] is None and snapshot["job_id"] is None
        # Back at the real clock the same session's memory is still VERIFIED.
        _, later = await states(factory, user_id, target_id)
        assert later["redis"]["state"] == "VERIFIED"
    body(database_url, scenario)


def test_a_recheck_session_keeps_the_jd_version_it_froze(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        for answer in CONFLICT_HISTORY:
            await session_for(factory, user_id, target_id, answer)
        _, index = await states(factory, user_id, target_id)
        assert index["kafka_reliability"]["state"] == "CONFLICTED"
        core = core_for(factory)
        recheck = await core.create(user_id, request(target_id, training_mode="RECHECK",
            training_knowledge_id=index["kafka_reliability"]["knowledge_id"],
            training_request_key=uuid4().hex))
        frozen = recheck["context_snapshot"]
        assert frozen["job_target"]["version"] == 1
        async with factory() as db, db.begin():
            await JobTargetService(db).update_requirements(user_id, target_id, [
                RequirementInput(statement="熟悉 Kubernetes 调度", importance="REQUIRED")])
        async with factory() as db, db.begin():
            assert (await JobTargetService(db).confirm(user_id, target_id))["version"] == 2
        assert (await core.detail(recheck["id"], user_id))["context_snapshot"] == frozen
        # Another account still cannot read this private training session.
        with pytest.raises(AppError) as denied:
            await TrainingService(factory).feedback(user_id + 999_999, recheck["id"])
        assert denied.value.status_code == 404
    body(database_url, scenario)


def test_the_memory_behind_a_recheck_is_a_real_aggregate(database_url):
    """Guard against the tempting shortcut: writing CONFLICTED into the row itself."""
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        for answer in CONFLICT_HISTORY:
            await session_for(factory, user_id, target_id, answer)
        async with factory() as db:
            root = await db.scalar(select(CapabilityMemory).where(
                CapabilityMemory.user_id == user_id, CapabilityMemory.topic == "kafka_reliability"))
            assert root is not None
            observations = root.projection.get("observations", [])
            assert len(observations) == 3, observations
            assert len({o["session_id"] for o in observations}) == 3
            assert all(o["evaluation_id"] and o["scope"] for o in observations)
            # Two of the three share one scope: that is what makes it a conflict.
            scopes = [o["scope"] for o in observations]
            assert len(set(scopes)) == 2, scopes
            assert root.projection["state"] == "CONFLICTED"
            assert [c["reason"] for c in root.projection["conflicts"]] == ["OPPOSITE_INDEPENDENT_OBSERVATIONS"]
            present = await CapabilityMemoryService(db).list(user_id)
            by_topic = {row["topic"]: row for row in present}
            assert by_topic["kafka_reliability"]["state"] == "CONFLICTED"
            assert by_topic["redis"]["state"] == "VERIFIED"
    body(database_url, scenario)


def body(database_url, scenario):
    async def main():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await scenario(factory)
        finally:
            await engine.dispose()
    asyncio.run(main())
