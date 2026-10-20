import asyncio
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, func

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.controller import SessionController
from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation
from interview_api.modules.interview import system_design as design
from interview_api.modules.interview_report.service import InterviewReportService
from test_m4_interview import seed, run_with_database
from v12_support import DesignReplayProvider

PLANS = [dict(deduplication="NONE", capacity_guard="CHECK_THEN_WRITE", capacity=1),
    dict(deduplication="ATOMIC_KEY", capacity_guard="CHECK_THEN_WRITE", capacity=1),
    dict(deduplication="ATOMIC_KEY", capacity_guard="ATOMIC_RESERVATION", capacity=1)]


async def setup(factory, budget=3):
    core, _, _, uid, old = await seed(factory)
    provider = DesignReplayProvider()
    core._router = ModelRouter({r: provider for r in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE)})
    ctx = old["context_snapshot"]
    session = await core.create(uid, CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"],
        question_count=budget, interviewer_skill_id="java_backend", design_interview=True))
    await core.start(session["id"], uid)
    return core, provider, uid, session["id"]


@pytest.mark.parametrize("tool,plan,passed", [("verify_idempotency", 0, False), ("verify_idempotency", 1, True), ("verify_capacity", 1, False), ("verify_capacity", 2, True)])
def test_real_deterministic_schedule(tool, plan, passed):
    result = asyncio.run(design.execute_tool(tool, dict(plan_version=1, requests=3), PLANS[plan]))
    assert result["passed"] is passed
    assert result["accepted_count"] == sum(e["accepted"] for e in result["events"])


@pytest.mark.parametrize("tool,args", [("shell", {"plan_version": 1, "requests": 3}),
    ("verify_capacity", {"plan_version": 1, "requests": 100}), ("verify_capacity", {"plan_version": 1, "requests": 3, "code": "exec"})])
def test_invalid_tool_contract(tool, args):
    with pytest.raises((ValidationError, AppError)):
        asyncio.run(design.execute_tool(tool, args, PLANS[0]))


def test_model_cannot_invent_result_or_outcome():
    from interview_api.modules.interview.artifacts import MainArtifact
    value = MainArtifact(recommendation="COMPLETE", tool_result_ids=["fake"], tool_observed_outcome="PASS")
    with pytest.raises(AppError): design.validate_tool_citations(value, dict(tool_evidence={"id": "dt_real", "result": {"passed": False}}))
    value.tool_result_ids = ["dt_real"]
    with pytest.raises(AppError): design.validate_tool_citations(value, dict(tool_evidence={"id": "dt_real", "result": {"passed": False}}))


def test_revision_tool_selection_report_and_idempotency(database_url):
    async def scenario(factory):
        core, provider, uid, sid = await setup(factory)
        keys = []
        for plan in PLANS:
            state = await core.read_state(sid, uid)
            key = uuid4().hex
            args = (sid, uid, state[3].id, key, "DEMO 解释本轮策略与事务边界")
            result = await core.answer(*args, design_plan=plan)
            calls = len(provider.calls)
            assert result == await core.answer(*args, design_plan=plan)
            assert len(provider.calls) == calls
            keys.append(key)
        detail = await core.detail(sid, uid)
        assert detail["status"] == "COMPLETED"
        tools = detail["planner_trace_json"]["design_tools"]
        assert [i["tool"] for i in tools] == ["verify_idempotency", "verify_capacity", "verify_capacity"]
        assert [i["result"]["passed"] for i in tools] == [False, False, True]
        async with factory() as db:
            assert await db.scalar(select(func.count(InterviewAnswer.id)).where(InterviewAnswer.session_id == sid)) == 3
            assert await db.scalar(select(func.count(InterviewEvaluation.id)).where(InterviewEvaluation.session_id == sid)) == 3
        reports = InterviewReportService(factory, core.router)
        report = await reports.generate(sid, uid)
        assert report["generation_status"] == "COMPLETED"
        view = await reports.session_detail(sid, uid)
        assert len(view["design_trace"]["design_revisions"]) == 3
        assert len(view["evaluations"]) == 3 and view["evaluations"][0]["tool_evidence"]["id"] == tools[0]["id"]
        with pytest.raises(AppError): await core.detail(sid, uid + 100000)
        with pytest.raises(AppError): await reports.session_detail(sid, uid + 100000)
    asyncio.run(run_with_database(database_url, scenario))


@pytest.mark.parametrize("failure", ["error", "timeout", "cancel"])
def test_tool_failure_no_evaluation_and_safe_retry(database_url, failure):
    async def scenario(factory):
        core, provider, uid, sid = await setup(factory)
        state = await core.read_state(sid, uid)
        args = (sid, uid, state[3].id, uuid4().hex, "DEMO 缺陷方案")
        async def broken(*args):
            if failure == "error": raise RuntimeError("controlled tool failure")
            if failure == "timeout": raise TimeoutError("controlled timeout")
            raise asyncio.CancelledError()
        with patch.object(design, "execute_tool", broken):
            with pytest.raises((RuntimeError, TimeoutError, asyncio.CancelledError)):
                await core.answer(*args, design_plan=PLANS[0])
        detail = await core.detail(sid, uid)
        assert detail["status"] == "IN_PROGRESS" and detail["pending_answer"]["retryable"]
        assert detail["planner_trace_json"]["design_tools"][0]["status"] == "FAILED"
        assert "EvaluationArtifact" not in provider.calls
        await core.answer(*args, design_plan=PLANS[0])
        async with factory() as db:
            assert await db.scalar(select(func.count(InterviewAnswer.id)).where(InterviewAnswer.session_id == sid)) == 1
    asyncio.run(run_with_database(database_url, scenario))


def test_stale_result_and_tool_budget_rejected(database_url):
    async def scenario(factory):
        core, provider, uid, sid = await setup(factory)
        state = await core.read_state(sid, uid)
        await core.answer(sid, uid, state[3].id, uuid4().hex, "DEMO 初始方案", design_plan=PLANS[0])
        detail = await core.detail(sid, uid)
        item = detail["planner_trace_json"]["design_tools"][0]
        async with factory() as db:
            with pytest.raises(AppError) as stale:
                await SessionController(db).finish_design_tool(sid, uid, item["state_version"], item["id"], result={"passed": True})
            assert stale.value.code == "DESIGN_STALE"
            await db.rollback()
        state = await core.read_state(sid, uid)
        args = (sid, uid, state[3].id, uuid4().hex, "DEMO 下一版")
        async def fail(*a): raise RuntimeError("bounded repeated tool failure")
        with patch.object(design, "execute_tool", fail):
            for _ in range(5):
                with pytest.raises(RuntimeError): await core.answer(*args, design_plan=PLANS[1])
        assert len((await core.detail(sid, uid))["planner_trace_json"]["design_tools"]) == 6
        with pytest.raises(AppError) as budget:
            await core.answer(*args, design_plan=PLANS[1])
        assert budget.value.code == "DESIGN_TOOL_BUDGET"
        assert (await core.detail(sid, uid))["status"] == "IN_PROGRESS"
    asyncio.run(run_with_database(database_url, scenario))


def test_real_tool_deadline_cancels_slow_execution(database_url):
    async def scenario(factory):
        core, _, uid, sid = await setup(factory)
        state = await core.read_state(sid, uid)
        async def slow(*args): await asyncio.sleep(3)
        with patch.object(design, "execute_tool", slow):
            with pytest.raises(TimeoutError):
                await core.answer(sid, uid, state[3].id, uuid4().hex, "DEMO 实际超时", design_plan=PLANS[0])
        detail = await core.detail(sid, uid)
        assert detail["status"] == "IN_PROGRESS"
        assert detail["planner_trace_json"]["design_tools"][0]["error"] == "DESIGN_TOOL_TIMEOUT"
    asyncio.run(run_with_database(database_url, scenario))


@pytest.mark.parametrize("invalid", [{"tool": "shell", "arguments": {"plan_version": 1, "requests": 3}, "reason": "bad"},
    {"tool": "verify_capacity", "arguments": {"plan_version": 1, "requests": 999}, "reason": "bad"}])
def test_invalid_model_decision_audited_without_tool_execution(database_url, invalid):
    import json
    from interview_api.infrastructure.models.provider import ModelResponse
    from interview_api.modules.experience.models import ExperienceAgentRun
    from interview_api.modules.interview.models import InterviewAgentCall
    async def scenario(factory):
        core, provider, uid, sid = await setup(factory)
        state = await core.read_state(sid, uid)
        async def invalid_response(*a, **k): return ModelResponse(json.dumps(invalid), {})
        with patch.object(provider, "generate", invalid_response):
            with pytest.raises(Exception):
                await core.answer(sid, uid, state[3].id, uuid4().hex, "DEMO 非法模型工具测试", design_plan=PLANS[0])
        detail = await core.detail(sid, uid)
        assert detail["status"] == "IN_PROGRESS" and detail["pending_answer"]["status"] == "FAILED"
        assert not detail["planner_trace_json"].get("design_tools")
        async with factory() as db:
            assert await db.scalar(select(ExperienceAgentRun.id).join(InterviewAgentCall,
                ExperienceAgentRun.id == InterviewAgentCall.agent_run_id).where(
                InterviewAgentCall.session_id == sid, ExperienceAgentRun.status == "FAILED").limit(1))
    asyncio.run(run_with_database(database_url, scenario))


def test_parallel_answer_does_not_duplicate_tool(database_url):
    async def scenario(factory):
        core, _, uid, sid = await setup(factory)
        state = await core.read_state(sid, uid)
        args = (sid, uid, state[3].id, uuid4().hex, "DEMO 并发重试")
        entered, release = asyncio.Event(), asyncio.Event()
        original = design.execute_tool
        async def paused(*a):
            entered.set()
            await release.wait()
            return await original(*a)
        with patch.object(design, "execute_tool", paused):
            first = asyncio.create_task(core.answer(*args, design_plan=PLANS[0]))
            await asyncio.wait_for(entered.wait(), 3)
            try:
                with pytest.raises(AppError) as duplicate: await core.answer(*args, design_plan=PLANS[0])
                assert duplicate.value.code == "INTERVIEW_ANSWER_RUNNING"
            finally: release.set()
            await first
        assert len((await core.detail(sid, uid))["planner_trace_json"]["design_tools"]) == 1
    asyncio.run(run_with_database(database_url, scenario))
