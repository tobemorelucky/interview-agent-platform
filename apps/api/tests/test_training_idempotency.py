"""Round 12: creating a training session is one idempotent operation.

The defect was a routing condition, not a missing mechanism. `create` sent a
request to `TrainingService` only when it carried *both* `training_mode` and
`training_request_key`; a client that sent the mode without a key got a plain
session that still carried a frozen `training` block, so nothing serialised or
deduplicated the click and a retried request inserted a second session that
looked like legitimate training.

These tests use the real services, the real transaction boundaries and the real
advisory lock. Only the model boundary is replayed. No memory projection and no
`request_key` row is written by hand.
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
from interview_api.modules.interview.models import InterviewContextSnapshot, InterviewSession
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.training import TrainingService
from test_job_target import create_target, seed_user
from v22_support import MemoryReplayProvider

GOOD = "Kafka 事务和唯一键，提交后 ACK，失败恢复"
WEAK = "Kafka ACK，失败重试"
# Deterministic for this fixture, as established in `test_private_recheck.py`.
CONFLICT_HISTORY = [GOOD, GOOD, WEAK, GOOD, WEAK]


def request(target_id, **extra):
    extra.setdefault("question_count", 1)
    return CreateSessionRequest(job_target_id=target_id, interviewer_skill_id="java_backend",
        adaptive_interview=True, **extra)


def core_for(factory):
    provider = MemoryReplayProvider()
    return MockInterviewService(factory, ModelRouter(
        {route: provider for route in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))


async def counts(factory, user_id):
    """(sessions, snapshots) for one account; a retry must move neither."""
    async with factory() as db:
        return (await db.scalar(select(func.count()).select_from(InterviewSession)
                .where(InterviewSession.user_id == user_id)),
            await db.scalar(select(func.count()).select_from(InterviewContextSnapshot)
                .where(InterviewContextSnapshot.user_id == user_id)))


async def session_for(factory, user_id, target_id, answer):
    core = core_for(factory)
    created = await core.create(user_id, request(
        target_id, training_mode="FULL", training_request_key=uuid4().hex))
    question = await core.start(created["id"], user_id)
    await core.answer(created["id"], user_id, question["question_id"], uuid4().hex, answer)


def test_training_without_a_request_key_is_refused_not_silently_created(database_url):
    """An old client is told to send a key; the server never fakes one.

    Minting a key here would make the endpoint *look* idempotent while every
    retry still inserted a row, which is exactly the failure this round fixes.
    """
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        before = await counts(factory, user_id)
        with pytest.raises(AppError) as denied:
            await core_for(factory).create(user_id, request(target_id, training_mode="FULL"))
        assert denied.value.code == "TRAINING_REQUEST_KEY_REQUIRED"
        assert denied.value.status_code == 422
        # Refused before any write: no session, no snapshot.
        assert await counts(factory, user_id) == before
    body(database_url, scenario)


def test_one_key_returns_the_same_session_instead_of_inserting_a_second(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        before = await counts(factory, user_id)
        core = core_for(factory)
        key = uuid4().hex
        first = await core.create(user_id, request(
            target_id, training_mode="FULL", training_request_key=key))
        after_first = await counts(factory, user_id)
        assert after_first == (before[0] + 1, before[1] + 1)
        second = await core.create(user_id, request(
            target_id, training_mode="FULL", training_request_key=key))
        # Same session, same frozen context, and no second row anywhere.
        assert second["id"] == first["id"]
        assert second["context_snapshot"] == first["context_snapshot"]
        assert await counts(factory, user_id) == after_first
    body(database_url, scenario)


def test_the_same_key_cannot_be_reused_for_different_content(database_url):
    """A key names one operation; different content is a client bug, not a retry."""
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        core = core_for(factory)
        key = uuid4().hex
        first = await core.create(user_id, request(
            target_id, training_mode="FULL", training_request_key=key))
        before = await counts(factory, user_id)
        with pytest.raises(AppError) as denied:
            await core.create(user_id, request(
                target_id, training_mode="FULL", question_count=2, training_request_key=key))
        assert denied.value.code == "TRAINING_REQUEST_CONFLICT"
        assert denied.value.status_code == 409
        # A changed mode is refused the same way, and nothing was written.
        with pytest.raises(AppError) as mode:
            await core.create(user_id, request(
                target_id, training_mode="WEAKNESS", training_knowledge_id="java_backend:redis",
                training_request_key=key))
        assert mode.value.code in ("TRAINING_REQUEST_CONFLICT", "TRAINING_TARGET_INVALID")
        assert await counts(factory, user_id) == before
        # The original session is still the one this key resolves to.
        again = await core.create(user_id, request(
            target_id, training_mode="FULL", training_request_key=key))
        assert again["id"] == first["id"]
    body(database_url, scenario)


def test_two_accounts_never_share_a_key(database_url):
    async def scenario(factory):
        first_user = await seed_user(factory)
        second_user = await seed_user(factory)
        first_target = await create_target(factory, first_user)
        second_target = await create_target(factory, second_user)
        core = core_for(factory)
        # Deliberately the same key: identity comes from the account, not the key.
        key = uuid4().hex
        mine = await core.create(first_user, request(
            first_target, training_mode="FULL", training_request_key=key))
        theirs = await core.create(second_user, request(
            second_target, training_mode="FULL", training_request_key=key))
        assert mine["id"] != theirs["id"]
        assert mine["context_snapshot"]["job_target"]["id"] == first_target
        assert theirs["context_snapshot"]["job_target"]["id"] == second_target
        with pytest.raises(AppError) as denied:
            await TrainingService(factory).feedback(first_user, theirs["id"])
        assert denied.value.status_code == 404
    body(database_url, scenario)


def test_a_deliberate_new_training_still_creates_a_new_session(database_url):
    """Idempotency must not trap the user in the session they just finished."""
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        core = core_for(factory)
        first = await core.create(user_id, request(
            target_id, training_mode="FULL", training_request_key=uuid4().hex))
        second = await core.create(user_id, request(
            target_id, training_mode="FULL", training_request_key=uuid4().hex))
        assert first["id"] != second["id"]
        assert (first["context_snapshot"]["training"]["request_key"]
                != second["context_snapshot"]["training"]["request_key"])
    body(database_url, scenario)


def test_concurrent_duplicates_cannot_both_insert(database_url):
    """The advisory lock, not application timing, is what makes this true."""
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        core = core_for(factory)
        key = uuid4().hex
        before = await counts(factory, user_id)
        results = await asyncio.gather(*[
            core.create(user_id, request(target_id, training_mode="FULL", training_request_key=key))
            for _ in range(3)], return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, errors
        assert len({r["id"] for r in results}) == 1
        assert await counts(factory, user_id) == (before[0] + 1, before[1] + 1)
    body(database_url, scenario)


def test_a_retry_after_a_restart_still_finds_the_same_session(database_url):
    """The key lives in the database, so a new process resolves it identically.

    A second engine and factory stand in for a restarted worker: no in-process
    cache, no shared client, only the committed snapshot.
    """
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        key = uuid4().hex
        first = await core_for(factory).create(user_id, request(
            target_id, training_mode="FULL", training_request_key=key))
        before = await counts(factory, user_id)
        restarted = create_async_engine(database_url)
        try:
            fresh = async_sessionmaker(restarted, expire_on_commit=False)
            again = await core_for(fresh).create(user_id, request(
                target_id, training_mode="FULL", training_request_key=key))
        finally:
            await restarted.dispose()
        assert again["id"] == first["id"]
        assert await counts(factory, user_id) == before
    body(database_url, scenario)


def test_weakness_creation_is_idempotent_too(database_url):
    """Focused modes use the same entry, so they must share the same guarantee."""
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        await session_for(factory, user_id, target_id, GOOD)
        board = await TrainingService(factory).workbench(user_id, request(target_id, question_count=8))
        goal = next(t["knowledge_id"] for t in board["targets"] if "WEAKNESS" in t["available_modes"])
        core = core_for(factory)
        key = uuid4().hex
        body_kwargs = dict(training_mode="WEAKNESS", training_knowledge_id=goal,
            training_request_key=key)
        first = await core.create(user_id, request(target_id, **body_kwargs))
        before = await counts(factory, user_id)
        second = await core.create(user_id, request(target_id, **body_kwargs))
        assert second["id"] == first["id"]
        assert second["context_snapshot"]["training"]["mode"] == "WEAKNESS"
        assert await counts(factory, user_id) == before
    body(database_url, scenario)


def test_recheck_creation_is_idempotent_too(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        for answer in CONFLICT_HISTORY:
            await session_for(factory, user_id, target_id, answer)
        board = await TrainingService(factory).workbench(user_id, request(target_id, question_count=8))
        goal = next(t["knowledge_id"] for t in board["targets"] if "RECHECK" in t["available_modes"])
        core = core_for(factory)
        key = uuid4().hex
        body_kwargs = dict(training_mode="RECHECK", training_knowledge_id=goal,
            training_request_key=key)
        first = await core.create(user_id, request(target_id, **body_kwargs))
        before = await counts(factory, user_id)
        second = await core.create(user_id, request(target_id, **body_kwargs))
        assert second["id"] == first["id"]
        assert second["context_snapshot"]["training"]["mode"] == "RECHECK"
        assert second["context_snapshot"]["memory_guided_plan"] == first["context_snapshot"]["memory_guided_plan"]
        assert await counts(factory, user_id) == before
    body(database_url, scenario)


def test_public_training_and_a_plain_interview_are_unaffected(database_url):
    async def scenario(factory):
        from v22_support import kafka_seed
        user_id, ctx = await kafka_seed(factory)
        core = core_for(factory)
        # A plain interview still needs no key at all.
        plain = await core.create(user_id, CreateSessionRequest(
            company_id=ctx["company_id"], job_id=ctx["job_id"],
            interviewer_skill_id="java_backend", adaptive_interview=True, question_count=1))
        assert "training" not in plain["context_snapshot"]
        # Public training keeps the same key semantics as the private path.
        key = uuid4().hex
        body_kwargs = dict(company_id=ctx["company_id"], job_id=ctx["job_id"],
            interviewer_skill_id="java_backend", adaptive_interview=True, question_count=1,
            training_mode="FULL", training_request_key=key)
        first = await core.create(user_id, CreateSessionRequest(**body_kwargs))
        second = await core.create(user_id, CreateSessionRequest(**body_kwargs))
        assert second["id"] == first["id"]
        assert first["context_snapshot"]["job_target"] is None
        assert first["context_snapshot"]["training"]["mode"] == "FULL"
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
