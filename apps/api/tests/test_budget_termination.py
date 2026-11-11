"""P1.2a: a valid Evaluation must commit even when no next question can be admitted.

Offline only. The provider is a deterministic local stub (no HTTP); the session
budget, Controller transaction and PostgreSQL writes are real.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.provider import ModelProvider, ModelProviderError, ModelResponse, ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.interview.adaptive import AdaptiveEvaluation
from interview_api.modules.interview.controller import SessionController
from interview_api.modules.interview.models import InterviewEvaluation
from interview_api.modules.interview.schemas import SendMessageRequest
from interview_api.modules.interview_report.service import InterviewReportService
from interview_api.modules.memory.capability_service import CapabilityMemoryService
from test_gap_scope_contract import interview_fixture
from test_m4_interview import run_with_database

BUDGET = 200_000
ANSWER = "缓存失效要走分层处理：先删缓存再更新数据库，并发更新用延迟双删兜底。"
EVALUATION_USAGE = 195_000


class BudgetProvider(ModelProvider):
    """Adaptive replay whose usage is chosen to exhaust the session admission."""

    provider_name, model_name = "openai_compatible", "WB_P12A_REPLAY"

    def __init__(self, *, evaluation_usage=EVALUATION_USAGE, invalid_quote=False):
        self.evaluation_usage = evaluation_usage
        self.invalid_quote = invalid_quote
        self.calls = []
        self.questions = 0

    async def generate(self, messages, *, schema):
        packet = json.loads(messages[-1]["content"])["input"]
        title = schema["title"]
        self.calls.append(title)
        if title in ("AdaptiveSelection", "MainHarnessAction"):
            value = self._selection(packet)
            if title == "MainHarnessAction":
                value = dict(kind="final", selection=value)
            usage = 1_000
        elif title == "AdaptiveEvaluation":
            value = self._evaluation(packet)
            usage = self.evaluation_usage
        else:  # pragma: no cover - a next question must never be generated here
            raise AssertionError(f"unexpected schema {title}")
        return ModelResponse(json.dumps(value, ensure_ascii=False), {"total_tokens": usage})

    def _selection(self, packet):
        adaptive = packet["adaptive"]
        answer_ref = adaptive.get("current_answer_ref")
        strategies = ("FOLLOW_UP",) if answer_ref else ("NEXT",)
        pair = next(c for c in adaptive["candidates"] if set(strategies) & set(c["strategies"]))
        label = adaptive["targets"][pair["target_knowledge_id"]]["label"]
        self.questions += 1
        # Wording must differ per turn or the deterministic duplicate check rejects it.
        question = f"请说明{label}的实现机制与故障边界（第{self.questions}问）？"
        return dict(candidate_id=pair["candidate_id"], strategy=strategies[0],
            supporting_refs=[pair["source"]["ref"]] + ([answer_ref] if answer_ref else []),
            reason_tags=["MISSING_MECHANISM"] if answer_ref else ["OPENING"], focus_gap_ids=[],
            proposed_question=dict(question=question, expected_points=["机制", "边界"],
                dimension=label, difficulty=packet["desired_difficulty"]))

    def _evaluation(self, packet):
        adaptive = packet["adaptive"]
        quote = "这段文字不在回答里" if self.invalid_quote else packet["answer"][:12]
        return dict(technical_correctness=4, coverage=3, depth=3, relevance=5, communication=4,
            evaluation="离线回放：本题说明了缓存失效与并发更新的处理顺序。",
            covered_points=["缓存失效"], missing_points=[], factual_or_reasoning_issues=[],
            follow_up_focus=[], next_action_recommendation="NEXT",
            knowledge_observations=[dict(knowledge_id=adaptive["active_knowledge_id"],
                state="PARTIALLY_DEMONSTRATED", answer_quotes=[quote],
                missing_mechanisms=[], resolved_gap_ids=[])])


class CapturingCoach(ReplayModelProvider):
    def __init__(self, responses):
        super().__init__(responses)
        self.requests = []

    async def generate(self, messages, *, schema):
        self.requests.append(json.loads(messages[-1]["content"])["input"])
        return await super().generate(messages, schema=schema)


def coach_fixture(evidence_ref):
    return dict(overall_summary="离线回放：本场在预算终止前完成了正式评价。",
        strengths=[dict(statement="本场回答给出了缓存失效的处理顺序。", supporting_evaluation_ids=[evidence_ref])],
        weaknesses=[dict(statement="本场回答对并发更新的边界说明仍可补充。", supporting_evaluation_ids=[evidence_ref])],
        recurring_issues=[], job_alignment=[], recommended_actions=["复盘并发更新边界"],
        practice_plan=["继续练习缓存一致性"], memory_proposals=[])


async def open_session(factory, provider, *, harness=False, count=3):
    uid, core, request = await interview_fixture(factory, provider, count)
    if harness:
        request = request.model_copy(update={"main_harness": True})
    session = await core.create(uid, request)
    question = await core.start(session["id"], uid)
    return uid, core, request, session["id"], question


async def start_and_answer(factory, provider, *, key, harness=False, count=3):
    uid, core, request, sid, question = await open_session(factory, provider, harness=harness, count=count)
    events = await core.answer(sid, uid, question["question_id"], key, ANSWER)
    return uid, core, request, sid, question, events


async def committed_evaluations(factory, sid):
    async with factory() as db:
        return (await db.scalars(select(InterviewEvaluation).where(
            InterviewEvaluation.session_id == sid).order_by(InterviewEvaluation.id))).all()


@pytest.mark.parametrize("harness", [False, True])
def test_valid_evaluation_commits_and_session_ends_on_next_question_budget(database_url, monkeypatch, harness):
    monkeypatch.setattr(settings, "interview_max_model_tokens", BUDGET)

    async def run(factory):
        provider = BudgetProvider()
        uid, core, _, sid, question, events = await start_and_answer(
            factory, provider, key="wb-budget-long", harness=harness)

        detail = await core.detail(sid, uid)
        assert detail["status"] == "COMPLETED" and detail["active_question_id"] is None
        assert detail["completion_json"]["completion_reason"] == "TOKEN_LIMIT"
        assert detail["completion_json"]["completion_detail"] == "NEXT_QUESTION_RESERVATION"
        assert detail["completion_json"]["answered_count"] == 1
        assert detail["completion_json"]["evaluated_count"] == 1
        assert detail["completion_json"]["question_budget"] == 3
        assert [row[0] for row in events] == ["evaluation", "complete", "done"]
        assert events[1][1]["completion_detail"] == "NEXT_QUESTION_RESERVATION"

        # The evaluated answer is kept; no question was invented for the gap.
        assert len(detail["questions"]) == 1
        assert detail["questions"][0]["question_id"] == question["question_id"]
        assert detail["questions"][0]["status"] == "COMPLETED"
        assert detail["pending_answer"] is None

        rows = await committed_evaluations(factory, sid)
        assert len(rows) == 1 and rows[0].answer_id is not None
        # Admission refused before any send, so nothing is left reserved.
        assert detail["interview_plan_json"]["model_call_reservations"] == {}
        assert detail["interview_plan_json"]["reserved_model_tokens"] == 0
        assert detail["interview_plan_json"].get("unreported_model_tokens", 0) == 0
        assert detail["interview_plan_json"]["budget_exhausted"] is True
        # One opening selection + one evaluation; the refused Main never sent HTTP.
        assert provider.calls == ["MainHarnessAction" if harness else "AdaptiveSelection", "AdaptiveEvaluation"]

        # Same idempotency key returns the committed events without a second write.
        assert await core.answer(sid, uid, question["question_id"], "wb-budget-long", ANSWER) == events
        assert provider.calls == ["MainHarnessAction" if harness else "AdaptiveSelection", "AdaptiveEvaluation"]
        assert len(await committed_evaluations(factory, sid)) == 1

    asyncio.run(run_with_database(database_url, run))


def test_invalid_evaluation_cannot_be_turned_into_a_budget_completion(database_url, monkeypatch):
    monkeypatch.setattr(settings, "interview_max_model_tokens", BUDGET)

    async def run(factory):
        provider = BudgetProvider(invalid_quote=True)
        uid, core, _, sid, question = await open_session(factory, provider)
        with pytest.raises(AppError) as error:
            await core.answer(sid, uid, question["question_id"], "wb-invalid-eval", ANSWER)
        assert error.value.code == "UNSUPPORTED_ANSWER_QUOTE"

        detail = await core.detail(sid, uid)
        assert detail["status"] == "IN_PROGRESS" and detail["completion_json"] is None
        assert detail["pending_answer"]["status"] == "FAILED"
        assert await committed_evaluations(factory, sid) == []
        assert len(detail["questions"]) == 1

    asyncio.run(run_with_database(database_url, run))


def test_affordable_budget_still_generates_the_next_question(database_url, monkeypatch):
    monkeypatch.setattr(settings, "interview_max_model_tokens", BUDGET)

    async def run(factory):
        provider = BudgetProvider(evaluation_usage=1_000)
        uid, core, _, sid, _, events = await start_and_answer(factory, provider, key="wb-budget-fits")

        detail = await core.detail(sid, uid)
        assert detail["status"] == "IN_PROGRESS" and detail["completion_json"] is None
        assert [row[0] for row in events] == ["evaluation", "follow_up", "done"]
        assert len(detail["questions"]) == 2 and len(await committed_evaluations(factory, sid)) == 1
        assert detail["questions"][1]["status"] == "ASKED"
        assert detail["interview_plan_json"]["model_call_reservations"] == {}

    asyncio.run(run_with_database(database_url, run))


def test_controller_rejects_a_budget_termination_that_keeps_a_next_question(database_url, monkeypatch):
    """Defence in depth: the internal flag cannot end a session that still needs a question."""
    monkeypatch.setattr(settings, "interview_max_model_tokens", BUDGET)

    async def run(factory):
        provider = BudgetProvider()
        uid, core, _, sid, question = await open_session(factory, provider)
        detail = await core.detail(sid, uid)
        target = detail["questions"][0]["evidence"]["adaptive_decision"]["target_knowledge_id"]
        answer_text = "再次作答用于校验终止守卫。"

        async with factory() as db:
            answer, expected, reused = await SessionController(db).prepare_answer(
                sid, uid, question["question_id"], "wb-guard-answer", answer_text)
            await db.commit()
        assert reused is False
        artifact = AdaptiveEvaluation(technical_correctness=3, coverage=3, depth=3, relevance=3,
            communication=3, evaluation="守卫用例", covered_points=[], missing_points=[],
            factual_or_reasoning_issues=[], follow_up_focus=[], next_action_recommendation="NEXT",
            knowledge_observations=[dict(knowledge_id=target, state="PARTIALLY_DEMONSTRATED",
                answer_quotes=[answer_text], missing_mechanisms=[], resolved_gap_ids=[])])
        evaluation = SimpleNamespace(artifact=artifact, run_id=1, provider="replay", model="fixture",
            prompt_version="2.3.3", retry=0, usage={"total_tokens": 0})
        async with factory() as db:
            with pytest.raises(AppError) as error:
                await SessionController(db).commit_answer(sid, uid, answer.id, expected, evaluation,
                    None, "NEXT", {}, terminal_detail="NEXT_QUESTION_RESERVATION", terminal_reason="BUDGET_LIMIT")
            await db.rollback()
        assert error.value.code == "INVALID_TERMINAL_REQUEST"

    asyncio.run(run_with_database(database_url, run))


def test_reserve_model_call_separates_token_and_duration_refusals(database_url, monkeypatch):
    """A refusal must say which limit refused it, not assume tokens."""
    monkeypatch.setattr(settings, "interview_max_model_tokens", BUDGET)

    async def run(factory):
        provider = BudgetProvider()
        uid, core, _, sid, _ = await open_session(factory, provider)

        async with factory() as db:
            controller = SessionController(db)
            session = await controller.owned(sid, uid)
            assert await controller.reserve_model_call(sid, uid, session.state_version, BUDGET + 1, "wb-tokens") is None
            await db.commit()
        session, context, _, _, _ = await core.read_state(sid, uid)
        plan = session.interview_plan_json
        assert plan["budget_exhausted"] is True and plan["budget_exhausted_reason"] == "TOKENS"
        assert plan["model_call_reservations"] == {}

        # A successful reservation clears the previous refusal label.
        async with factory() as db:
            controller = SessionController(db)
            session = await controller.owned(sid, uid)
            assert await controller.reserve_model_call(sid, uid, session.state_version, 10, "wb-ok") is not None
            await db.commit()
        session, _, _, _, _ = await core.read_state(sid, uid)
        assert "budget_exhausted_reason" not in session.interview_plan_json
        async with factory() as db:
            await SessionController(db).settle_model_call(sid, uid, "wb-ok", True, known_tokens=0)
            await db.commit()

        # Session duration is a different refusal than tokens.
        async with factory() as db:
            session = await SessionController(db).owned(sid, uid)
            session.started_at = datetime.now(timezone.utc) - timedelta(seconds=10_000)
            await db.commit()
        async with factory() as db:
            controller = SessionController(db)
            session = await controller.owned(sid, uid)
            assert await controller.reserve_model_call(sid, uid, session.state_version, 10, "wb-time") is None
            await db.commit()
        session, _, _, _, _ = await core.read_state(sid, uid)
        assert session.interview_plan_json["budget_exhausted_reason"] == "DURATION"

    asyncio.run(run_with_database(database_url, run))


def test_unidentified_admission_refusal_is_not_a_budget_completion(database_url, monkeypatch):
    """No recorded limit means no termination: the answer stays retryable."""
    monkeypatch.setattr(settings, "interview_max_model_tokens", BUDGET)
    original = SessionController.reserve_model_call

    async def run(factory):
        provider = BudgetProvider()
        calls = {"count": 0}

        async def refuse_without_reason(self, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] < 3:      # opening, then evaluation
                return await original(self, *args, **kwargs)
            return None                 # next question, no reason recorded

        with monkeypatch.context() as patch:
            patch.setattr(SessionController, "reserve_model_call", refuse_without_reason)
            uid, core, _, sid, question = await open_session(factory, provider)
            with pytest.raises(AppError) as error:
                await core.answer(sid, uid, question["question_id"], "wb-unknown-refusal", ANSWER)
        assert error.value.code == "INTERVIEW_BUDGET_EXHAUSTED"
        assert calls["count"] == 3
        detail = await core.detail(sid, uid)
        assert detail["status"] == "IN_PROGRESS" and detail["completion_json"] is None
        assert await committed_evaluations(factory, sid) == []

    asyncio.run(run_with_database(database_url, run))


def test_duration_stop_is_recorded_as_duration_not_budget(database_url, monkeypatch):
    """The controller writes the supplied reason and stage verbatim."""
    monkeypatch.setattr(settings, "interview_max_model_tokens", BUDGET)

    async def run(factory):
        provider = BudgetProvider()
        uid, core, _, sid, question = await open_session(factory, provider)
        detail = await core.detail(sid, uid)
        target = detail["questions"][0]["evidence"]["adaptive_decision"]["target_knowledge_id"]
        answer_text = "作答用于校验时长终止原因。"

        async with factory() as db:
            answer, expected, _ = await SessionController(db).prepare_answer(
                sid, uid, question["question_id"], "wb-duration-answer", answer_text)
            await db.commit()
        artifact = AdaptiveEvaluation(technical_correctness=3, coverage=3, depth=3, relevance=3,
            communication=3, evaluation="时长终止用例", covered_points=[], missing_points=[],
            factual_or_reasoning_issues=[], follow_up_focus=[], next_action_recommendation="NEXT",
            knowledge_observations=[dict(knowledge_id=target, state="PARTIALLY_DEMONSTRATED",
                answer_quotes=[answer_text], missing_mechanisms=[], resolved_gap_ids=[])])
        evaluation = SimpleNamespace(artifact=artifact, run_id=1, provider="replay", model="fixture",
            prompt_version="2.3.3", retry=0, usage={"total_tokens": 0})
        async with factory() as db:
            await SessionController(db).commit_answer(sid, uid, answer.id, expected, evaluation, None,
                "COMPLETE", {}, terminal_detail="NEXT_QUESTION_DURATION", terminal_reason="DURATION_LIMIT")
            await db.commit()
        detail = await core.detail(sid, uid)
        assert detail["status"] == "COMPLETED"
        assert detail["completion_json"]["completion_reason"] == "DURATION_LIMIT"
        assert detail["completion_json"]["completion_detail"] == "NEXT_QUESTION_DURATION"
        assert len(detail["questions"]) == 1

    asyncio.run(run_with_database(database_url, run))


def test_harness_rejections_are_not_turned_into_a_budget_completion(database_url, monkeypatch):
    """Model/schema/business rejections must surface, never become a completion."""
    monkeypatch.setattr(settings, "interview_max_model_tokens", BUDGET)

    class IllegalFinal(BudgetProvider):
        async def generate(self, messages, *, schema):
            if schema["title"] == "MainHarnessAction":
                self.calls.append("MainHarnessAction")
                return ModelResponse(json.dumps(dict(kind="final", selection=dict(
                    candidate_id="C_not_a_candidate", strategy="NEXT", supporting_refs=["skill:none"],
                    reason_tags=["OPENING"], focus_gap_ids=[], proposed_question=dict(
                        question="请说明检索机制与边界？", expected_points=["机制"], dimension="检索",
                        difficulty="MEDIUM")))), {"total_tokens": 100})
            return await super().generate(messages, schema=schema)

    class BrokenAction(BudgetProvider):
        async def generate(self, messages, *, schema):
            if schema["title"] == "MainHarnessAction":
                self.calls.append("MainHarnessAction")
                return ModelResponse(json.dumps(dict(kind="tool", name="read_active_knowledge_gaps",
                    arguments={"unexpected": 1})), {"total_tokens": 100})
            return await super().generate(messages, schema=schema)

    async def run(factory):
        uid, core, request = await interview_fixture(factory, IllegalFinal(), 3)
        session = await core.create(uid, request.model_copy(update={"main_harness": True}))
        with pytest.raises(AppError) as illegal:
            await core.start(session["id"], uid)
        assert illegal.value.code == "UNKNOWN_DIRECTOR_CANDIDATE"
        detail = await core.detail(session["id"], uid)
        assert detail["status"] == "READY" and detail["questions"] == []
        assert detail["completion_json"] is None
        assert detail["interview_plan_json"]["model_call_reservations"] == {}

        uid2, core2, request2 = await interview_fixture(factory, BrokenAction(), 3)
        session2 = await core2.create(uid2, request2.model_copy(update={"main_harness": True}))
        with pytest.raises(ModelProviderError):
            await core2.start(session2["id"], uid2)
        detail2 = await core2.detail(session2["id"], uid2)
        assert detail2["status"] == "READY" and detail2["questions"] == []
        assert detail2["completion_json"] is None
        assert detail2["interview_plan_json"]["model_call_reservations"] == {}

    asyncio.run(run_with_database(database_url, run))


def test_client_payload_has_no_way_to_request_early_termination():
    assert "completion_reason" not in SendMessageRequest.model_fields
    with pytest.raises(ValidationError):
        SendMessageRequest(question_id=0, idempotency_key="abcdefgh", content="x")
    request = SendMessageRequest(question_id=1, idempotency_key="abcdefgh", content="x",
        completion_reason="BUDGET_LIMIT")
    assert not hasattr(request, "completion_reason")
    assert "completion_reason" not in request.model_dump()


def test_planned_question_count_completion_still_persists_its_reason(database_url, monkeypatch):
    """Finishing the planned question count is normal completion, not a budget stop."""
    monkeypatch.setattr(settings, "interview_max_model_tokens", BUDGET)

    async def run(factory):
        provider = BudgetProvider(evaluation_usage=1_000)
        uid, core, _, sid, question = await open_session(factory, provider, count=1)
        events = await core.answer(sid, uid, question["question_id"], "wb-planned-end", ANSWER)

        detail = await core.detail(sid, uid)
        assert detail["status"] == "COMPLETED"
        assert detail["completion_json"]["completion_reason"] == "QUESTION_COUNT_REACHED"
        assert "completion_detail" not in detail["completion_json"]
        assert [row[0] for row in events] == ["evaluation", "complete", "done"]
        assert len(await committed_evaluations(factory, sid)) == 1

    asyncio.run(run_with_database(database_url, run))


def test_coach_receives_the_committed_evaluation_and_the_termination_reason(database_url, monkeypatch):
    monkeypatch.setattr(settings, "interview_max_model_tokens", BUDGET)

    async def run(factory):
        provider = BudgetProvider()
        uid, core, request, sid, _, _ = await start_and_answer(factory, provider, key="wb-coach")
        rows = await committed_evaluations(factory, sid)
        assert len(rows) == 1

        coach = CapturingCoach([coach_fixture(f"evaluation:{rows[0].id}")])
        service = InterviewReportService(factory, ModelRouter({"interview_coach": coach}))
        report = await service.generate(sid, uid)
        assert report["generation_status"] == "COMPLETED", report
        assert report["statistics"]["evaluated_count"] == 1
        assert report["statistics"]["answered_count"] == 1

        payload = coach.requests[-1]
        completion = payload["session_completion"]
        assert completion["completion_reason"] == "TOKEN_LIMIT"
        assert completion["completion_detail"] == "NEXT_QUESTION_RESERVATION"
        assert completion["early_terminated"] is True
        assert completion["evaluated_count"] == 1 and completion["question_budget"] == 3
        assert completion["semantics"] == "CONTROLLER_TERMINATION_FACT_NOT_PERFORMANCE_EVIDENCE"
        # The Coach still receives only persisted evaluations of this session.
        assert [row["id"] for row in payload["evaluations"]] == [rows[0].id]

        async with factory() as db:
            observations = await CapabilityMemoryService(db).observations(uid)
        assert sum(len(items) for items in observations.values()) == 1
        entry = next(iter(observations.values()))[0]
        assert entry["evaluation_id"] == rows[0].id

        # The next session may only reuse that committed evaluation.
        nxt = await core.create(uid, request)
        plan = nxt["context_snapshot"]["memory_guided_plan"]
        assert plan["items"]
        for item in plan["items"]:
            for ref in item["observation_refs"]:
                assert ref == f"evaluation:{rows[0].id}"

    asyncio.run(run_with_database(database_url, run))
