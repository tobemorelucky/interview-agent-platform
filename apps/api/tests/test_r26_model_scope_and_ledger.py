"""Round 26 P0: explicit attribution, ledger target guard, per-request isolation.

The failure these pin down is not hypothetical: a sandbox harness booked eight real
admissions into the wrong owner because the scope was set once per session, and the
gateway silently fell back to `legacy_llm` plus the process-wide session factory
whenever a caller forgot one. Every test here asserts that nothing is written and
nothing is sent when the attribution is wrong.
"""
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.config import settings
from interview_api.infrastructure.models.governance import governed_call
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
from interview_api.infrastructure.models.scope import (
    current_scope, database_of, expected_ledger_database, model_scope_for,
)
from interview_api.modules.interview.context_projection import TERMINAL_CODES


class GovernedStub(ModelProvider):
    """Behaves like a governed provider without any network at all."""

    provider_name = "modelscope"
    model_name = "stub-model"
    timeout = 5.0
    max_output_tokens = 64

    def __init__(self, tokens=12):
        self.calls = 0
        self.sent = 0
        self.tokens = tokens

    async def generate(self, messages, *, schema):
        self.calls += 1

        async def operation():
            self.sent += 1
            return ModelResponse('{"ok": true}', {
                "input_tokens": 8, "output_tokens": 4, "total_tokens": self.tokens})

        return await governed_call(self, messages, operation)


def _factory(url):
    return async_sessionmaker(create_async_engine(url), expire_on_commit=False)


async def _count(factory, like):
    async with factory() as db:
        return await db.scalar(text("SELECT count(*) FROM model_admissions WHERE owner_key LIKE :p"),
                               {"p": like})


def test_missing_scope_is_refused_before_anything_is_sent(database_url, monkeypatch):
    """J: no scope must never degrade into the process-wide factory."""
    import interview_api.infrastructure.db.session as session_module

    def forbidden(*args, **kwargs):  # pragma: no cover - only fires on a regression
        raise AssertionError("the gateway must not fall back to the process session factory")

    monkeypatch.setattr(session_module, "async_session_factory", forbidden)

    async def scenario():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        provider = GovernedStub()
        try:
            assert current_scope() is None
            with pytest.raises(Exception) as caught:
                await provider.generate([{"role": "user", "content": "x"}], schema={})
            assert getattr(caught.value, "code", None) == "MODEL_SCOPE_REQUIRED"
            assert provider.sent == 0
            assert await _count(factory, "scope-test:%") == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    assert "MODEL_SCOPE_REQUIRED" in TERMINAL_CODES


def test_a_scope_pointing_at_another_database_is_refused(database_url, monkeypatch):
    """I: the API and a worker must not disagree about the ledger target."""
    alt = "interview_agent_wb_r26_alt_20261115"
    monkeypatch.setattr(settings, "model_ledger_database", alt)

    async def scenario():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        alt_engine = create_async_engine(database_url.replace("interview_agent_wb_r26_test_20261115", alt))
        alt_factory = async_sessionmaker(alt_engine, expire_on_commit=False)
        provider = GovernedStub()
        try:
            assert expected_ledger_database() == alt
            with model_scope_for(factory, user_id=11, route="answer_evaluation"):
                with pytest.raises(Exception) as caught:
                    await provider.generate([{"role": "user", "content": "x"}], schema={})
            assert getattr(caught.value, "code", None) == "MODEL_LEDGER_TARGET_MISMATCH"
            assert provider.sent == 0
            assert await _count(factory, "user:11") == 0
            assert await _count(alt_factory, "user:11") == 0
        finally:
            await engine.dispose()
            await alt_engine.dispose()

    asyncio.run(scenario())
    assert "MODEL_LEDGER_TARGET_MISMATCH" in TERMINAL_CODES


def test_two_owners_in_flight_keep_their_own_attribution(database_url):
    """H: concurrency must not let one task overwrite another's attribution."""
    async def scenario():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            first, second = GovernedStub(), GovernedStub()
            a, b = "concurrency:" + uuid4().hex, "concurrency:" + uuid4().hex

            async def call(provider, owner):
                with model_scope_for(factory, owner=owner, route="answer_evaluation"):
                    return await provider.generate([{"role": "user", "content": owner}], schema={})

            await asyncio.gather(call(first, a), call(second, b))
            async with factory() as db:
                rows = (await db.execute(text(
                    "SELECT owner_key, route, status, actual_tokens, charged_tokens FROM model_admissions "
                    "WHERE owner_key IN (:a, :b) ORDER BY owner_key"), {"a": a, "b": b})).fetchall()
            assert [row[0] for row in rows] == sorted([a, b])
            assert {row[1] for row in rows} == {"answer_evaluation"}
            assert {row[2] for row in rows} == {"SETTLED"}
            assert {row[3] for row in rows} == {12}
            assert {row[4] for row in rows} == {12}
            assert current_scope() is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scope_names_the_database_it_will_bill(database_url):
    async def scenario():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            assert database_of(factory) == expected_ledger_database()
            with model_scope_for(factory, user_id=5, route="answer_evaluation") as scope:
                assert scope.owner == "user:5" and scope.user_id == 5
                assert scope.ledger_database == database_of(factory)
            assert current_scope() is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
