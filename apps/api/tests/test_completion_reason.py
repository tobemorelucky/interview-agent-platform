"""Termination-reason taxonomy: normal completion must not look like a budget stop.

The stop test is one deterministic function (`SessionController.stop_reason`);
these tests pin its classification order and its wiring through commit_answer,
the report payload and the Coach input. Offline only.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from interview_api.core.config import settings
from interview_api.modules.interview.controller import SessionController, STOP_REASONS
from interview_api.modules.interview_report.service import InterviewReportService
from interview_api.infrastructure.models.router import ModelRouter
from test_budget_termination import (ANSWER, BudgetProvider, CapturingCoach, coach_fixture,
    committed_evaluations, open_session)
from test_m4_interview import run_with_database

def context(**overrides):
    budgets = dict(question_count=20, max_follow_ups=2, max_rounds=40,
                   max_duration_seconds=7_200, max_model_tokens=650_000)
    budgets.update(overrides)
    return {"budgets": budgets}


def questions(n):
    return [object() for _ in range(n)]


def stop(budgets=None, count=0, rounds=0, tokens=0, elapsed=0):
    return SessionController.stop_reason(context(**(budgets or {})), questions(count),
        rounds=rounds, tokens=tokens, started_at=datetime.now(timezone.utc) - timedelta(seconds=elapsed))


def test_planned_question_count_is_completion_not_a_budget_stop():
    # Every budget is spent; finishing the planned count still wins.
    assert stop(budgets=dict(max_model_tokens=1_000, max_rounds=1, max_duration_seconds=60),
        count=20, rounds=20, tokens=999_999, elapsed=99_999) == "QUESTION_COUNT_REACHED"


def test_round_limit_is_its_own_reason():
    assert stop(count=3, rounds=40) == "ROUND_LIMIT"


def test_token_limit_is_its_own_reason():
    assert stop(tokens=650_000) == "TOKEN_LIMIT"
    assert stop(tokens=649_999) is None


def test_duration_limit_is_its_own_reason():
    assert stop(elapsed=7_200) == "DURATION_LIMIT"
    assert stop(elapsed=7_199) is None


def test_under_every_limit_keeps_the_interview_running():
    assert stop(count=5, rounds=6, tokens=10_000, elapsed=60) is None
    assert stop() is None


def test_classification_order_is_question_count_rounds_tokens_duration():
    assert STOP_REASONS == ("QUESTION_COUNT_REACHED", "ROUND_LIMIT", "TOKEN_LIMIT", "DURATION_LIMIT")
    # Rounds beat tokens and duration; tokens beat duration.
    assert stop(rounds=40, tokens=650_000, elapsed=7_200) == "ROUND_LIMIT"
    assert stop(tokens=650_000, elapsed=7_200) == "TOKEN_LIMIT"


def test_round_limit_completion_commits_the_evaluation_and_skips_the_next_question(database_url, monkeypatch):
    monkeypatch.setattr(settings, "interview_max_rounds", 1)
    monkeypatch.setattr(settings, "interview_max_model_tokens", 200_000)

    async def run(factory):
        provider = BudgetProvider(evaluation_usage=1_000)
        uid, core, _, sid, question = await open_session(factory, provider, count=3)
        events = await core.answer(sid, uid, question["question_id"], "wb-round-limit", ANSWER)

        detail = await core.detail(sid, uid)
        completion = detail["completion_json"]
        assert detail["status"] == "COMPLETED"
        assert completion["completion_reason"] == "ROUND_LIMIT"
        assert "completion_detail" not in completion
        assert completion["answered_count"] == 1 and completion["evaluated_count"] == 1
        assert [row[0] for row in events] == ["evaluation", "complete", "done"]
        # No next question was generated or invented.
        assert len(detail["questions"]) == 1
        assert provider.calls == ["AdaptiveSelection", "AdaptiveEvaluation"]

    asyncio.run(run_with_database(database_url, run))


def test_coach_sees_planned_completion_as_full_not_early(database_url, monkeypatch):
    monkeypatch.setattr(settings, "interview_max_model_tokens", 200_000)

    async def run(factory):
        provider = BudgetProvider(evaluation_usage=1_000)
        uid, core, _, sid, question = await open_session(factory, provider, count=1)
        await core.answer(sid, uid, question["question_id"], "wb-coach-count", ANSWER)

        rows = await committed_evaluations(factory, sid)
        coach = CapturingCoach([coach_fixture(f"evaluation:{rows[0].id}")])
        service = InterviewReportService(factory, ModelRouter({"interview_coach": coach}))
        report = await service.generate(sid, uid)
        assert report["generation_status"] == "COMPLETED", report
        completion = coach.requests[-1]["session_completion"]
        assert completion["completion_reason"] == "QUESTION_COUNT_REACHED"
        assert completion["evaluated_count"] == completion["question_budget"] == 1
        assert completion["early_terminated"] is False

    asyncio.run(run_with_database(database_url, run))


def test_legacy_budget_limit_reason_still_reaches_the_coach_unchanged(database_url, monkeypatch):
    """Rows written before the split keep their stored reason; nothing rewrites them."""
    monkeypatch.setattr(settings, "interview_max_model_tokens", 200_000)

    async def run(factory):
        provider = BudgetProvider(evaluation_usage=1_000)
        uid, core, _, sid, question = await open_session(factory, provider, count=1)
        await core.answer(sid, uid, question["question_id"], "wb-legacy-reason", ANSWER)
        async with factory() as db:
            await db.execute(text(
                "update interview_sessions set completion_json = jsonb_set("
                "completion_json::jsonb, '{completion_reason}', '\"BUDGET_LIMIT\"')::json "
                "where id = :sid"), {"sid": sid})
            await db.commit()

        rows = await committed_evaluations(factory, sid)
        coach = CapturingCoach([coach_fixture(f"evaluation:{rows[0].id}")])
        service = InterviewReportService(factory, ModelRouter({"interview_coach": coach}))
        report = await service.generate(sid, uid)
        assert report["generation_status"] == "COMPLETED", report
        assert coach.requests[-1]["session_completion"]["completion_reason"] == "BUDGET_LIMIT"

    asyncio.run(run_with_database(database_url, run))
