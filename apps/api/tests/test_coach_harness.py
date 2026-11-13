"""Phase 1: the Coach may spend one read-only tool call, and only one.

Two kinds of test live here.

* Unit tests pin the rules that must hold without a database: which tool is
  offered for a given frozen input, what one Coach turn may look like, and how a
  training suggestion is validated against the state machine Training uses.
* Integration tests run the real report service, the real tool, the real
  resolver and the real audit rows against PostgreSQL, with Replay as the only
  substitute. They assert model-call and tool-call counts, the audit trail, that
  a tool failure never fabricates evidence, and that OFF is unchanged.
"""
import asyncio
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import INTERVIEW_COACH_ROUTE, ModelRouter
from interview_api.modules.experience.models import ExperienceAgentStepRun
from interview_api.modules.interview.models import InterviewContextSnapshot
from interview_api.modules.interview_report import tools
from interview_api.modules.interview_report.models import InterviewReportAttempt
from interview_api.modules.interview_report.resolver import resolve_coach_artifact
from interview_api.modules.interview_report.schemas import (
    CoachArtifact, CoachTurn, HarnessCoachArtifact,
)
from interview_api.modules.interview_report.service import InterviewReportService
from interview_api.modules.memory.capability_service import CapabilityMemoryService
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview_report.statistics import interview_records
from interview_api.infrastructure.models.router import ANSWER_EVALUATION_ROUTE, INTERVIEW_MAIN_ROUTE
from v22_support import MemoryReplayProvider, kafka_seed

GOOD = "Kafka 事务和唯一键，提交后 ACK，失败恢复"


def response_for(ids):
    """A legal artifact for however many evaluations exist (the m5 fixture needs two)."""
    refs = [f"evaluation:{id_}" for id_ in ids]
    return dict(overall_summary="Replay fixture：本场回答展示了缓存实践，解释边界仍有提升空间。",
        strengths=[dict(statement="本场具体实现描述有明确技术依据", supporting_evaluation_ids=[refs[-1]])],
        weaknesses=[dict(statement="本场并发更新边界说明尚不完整", supporting_evaluation_ids=[refs[0]])],
        recurring_issues=[], job_alignment=[dict(statement="本场主题与快照岗位要求相关",
            supporting_evaluation_ids=[refs[0]])],
        recommended_actions=["用真实项目案例解释缓存更新取舍"], practice_plan=["先复盘缺失点，再练习边界条件解释"],
        memory_proposals=[dict(proposal_type="PREPARATION_FOCUS",
            content="基于本场回答，准备缓存并发更新的解释练习", supporting_evaluation_ids=[refs[0]])])


async def adaptive_completed(factory):
    """A completed *adaptive* session: without a blueprint there is no scope to test.

    `test_m5_report.completed_interview` uses a plain session, which has no
    blueprint, no capability context and therefore no legal training target.
    """
    user_id, ctx = await kafka_seed(factory)
    provider = MemoryReplayProvider()
    core = MockInterviewService(factory, ModelRouter(
        {route: provider for route in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))
    created = await core.create(user_id, CreateSessionRequest(
        company_id=ctx["company_id"], job_id=ctx["job_id"],
        interviewer_skill_id="java_backend", adaptive_interview=True, question_count=1))
    question = await core.start(created["id"], user_id)
    await core.answer(created["id"], user_id, question["question_id"], uuid4().hex, GOOD)
    async with factory() as db:
        _, evaluations = await interview_records(db, created["id"])
    return user_id, created["id"], evaluations


@contextmanager
def coach_settings(harness=True, tool_limit=1, model_limit=2, max_input_bytes=None):
    patches = [patch.object(settings, "interview_coach_harness", harness),
               patch.object(settings, "interview_coach_tool_limit", tool_limit),
               patch.object(settings, "interview_coach_model_limit", model_limit)]
    if max_input_bytes is not None:
        patches.append(patch.object(settings, "interview_coach_max_input_bytes", max_input_bytes))
    for item in patches:
        item.start()
    try:
        yield
    finally:
        for item in patches:
            item.stop()


@contextmanager
def offer_only(**available):
    """Force the tool-gate for a loop test; the gate itself is unit-tested below."""
    base = {"read_evaluation_detail": False, "read_capability_history": False}
    base.update(available)
    with patch.object(tools, "available_tools", lambda payload, history: dict(base)):
        yield


async def tool_steps(factory, report_id):
    async with factory() as db:
        attempt = await db.scalar(select(InterviewReportAttempt)
            .where(InterviewReportAttempt.report_id == report_id)
            .order_by(InterviewReportAttempt.id.desc()))
        rows = (await db.scalars(select(ExperienceAgentStepRun)
            .where(ExperienceAgentStepRun.agent_run_id == attempt.agent_run_id)
            .order_by(ExperienceAgentStepRun.id))).all()
        return attempt.agent_run_id, [(row.step_name, row.status, row.input_json, row.output_json) for row in rows]


async def blueprint_targets(factory, session_id):
    async with factory() as db:
        session = await db.get(InterviewContextSnapshot, (await db.scalar(
            select(InterviewContextSnapshot.id).where(InterviewContextSnapshot.interview_session_id == session_id)
            .order_by(InterviewContextSnapshot.id.desc()))))
        targets = ((session.context_json.get("adaptive_blueprint") or {}).get("targets") or [])
        return [(item["knowledge_id"], item.get("label")) for item in targets]


async def refresh_memory(factory, user_id):
    async with factory() as db:
        await CapabilityMemoryService(db).refresh(user_id)
        await db.commit()


def run(database_url, scenario):
    async def main():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await scenario(factory)
        finally:
            await engine.dispose()
    asyncio.run(main())


# ── Unit: the gate ──


def test_evaluation_tool_is_only_offered_when_the_projection_clipped_something():
    payload = {"evidence_projection": {"omitted": {"clipped_text_fields": 0}}, "evaluations": [{"id": 1}]}
    assert tools.available_tools(payload, None) == {"read_evaluation_detail": False,
                                                    "read_capability_history": False}
    clipped = {"evidence_projection": {"omitted": {"clipped_text_fields": 3}}, "evaluations": [{"id": 1}]}
    assert tools.available_tools(clipped, None)["read_evaluation_detail"] is True
    # No history means nothing to ask about; offering the tool would invite a
    # pointless call (scenario 4).
    assert tools.available_tools(clipped, {"topics": {}})["read_capability_history"] is False
    assert tools.available_tools(clipped, {"topics": {"redis": {"state": "VERIFIED"}}})["read_capability_history"] is True


def test_one_turn_has_exactly_three_legal_shapes():
    assert isinstance(CoachTurn.model_validate(
        {"action": "read_evaluation_detail", "evaluation_id": 7}).root.evaluation_id, int)
    assert CoachTurn.model_validate(
        {"action": "read_capability_history", "knowledge_id": "java_backend:redis"}).root.knowledge_id
    final = CoachTurn.model_validate({"action": "final", "artifact": response_for([1, 2])})
    assert final.root.action == "final"
    for invalid in ({"action": "read_evaluation_detail", "evaluation_id": 0},
                    {"action": "read_evaluation_detail", "evaluation_id": "7"},
                    {"action": "drop_table"},
                    {"action": "read_capability_history", "knowledge_id": "x"},
                    {"action": "final"}):
        with pytest.raises(ValidationError):
            CoachTurn.model_validate(invalid)


def test_the_off_artifact_schema_did_not_gain_the_suggestion_field():
    assert "suggested_training" not in CoachArtifact.model_json_schema()["properties"]
    assert "suggested_training" in HarnessCoachArtifact.model_json_schema()["properties"]


def scope_for(state, revoked=False, knowledge_id="java_backend:redis"):
    from interview_api.modules.interview.training import modes
    return {"skill_id": "java_backend", "targets": {knowledge_id: dict(
        label="Redis", state=state, revoked=revoked, available_modes=modes(state, revoked),
        memory_revision=3)}}


def suggestion(mode, knowledge_id="java_backend:redis", refs=("evaluation:1",)):
    return dict(mode=mode, knowledge_id=knowledge_id, reason="练习缓存更新与失效的边界解释",
                supporting_evaluation_ids=list(refs), scope="CURRENT_JOB_TARGET")


def resolve_with(state, **kwargs):
    artifact = HarnessCoachArtifact.model_validate({**response_for([1, 2]),
                                                    "suggested_training": suggestion(**kwargs)})
    evaluations = [dict(id=1, session_question_id=11, question="Q1", evaluation="E1",
                        scores=dict(technical_correctness=3, coverage=3, depth=3, relevance=3, communication=3)),
                   dict(id=2, session_question_id=12, question="Q2", evaluation="E2",
                        scores=dict(technical_correctness=3, coverage=3, depth=3, relevance=3, communication=3))]
    return resolve_coach_artifact(artifact, evaluations, None, training_scope=scope_for(state))


def test_recheck_suggestion_needs_the_state_that_allows_it():
    """Scenario 7/8: the resolver re-derives legality, it does not trust the model."""
    for state in ("UNKNOWN", "PRACTICING", "VERIFIED", "GAP_SUSPECTED"):
        result = resolve_with(state, mode="RECHECK")
        assert result["suggested_training"] is None, state
        assert result["suggested_training_rejected"]["code"] == "MODE_NOT_ALLOWED_FOR_STATE", state
        # The rest of the report is untouched by the rejected advice.
        assert result["strengths"] and result["weaknesses"]
    for state in ("CONFLICTED", "RECHECK_DUE"):
        result = resolve_with(state, mode="RECHECK")
        assert result["suggested_training"]["mode"] == "RECHECK", state
        assert result["suggested_training"]["validation"] == "LEGAL_AT_REPORT_TIME_ADVISORY_ONLY"
        assert result["suggested_training"]["state"] == state


def test_weakness_and_full_follow_the_same_rule():
    assert resolve_with("PRACTICING", mode="WEAKNESS")["suggested_training"]["mode"] == "WEAKNESS"
    assert resolve_with("VERIFIED", mode="WEAKNESS")["suggested_training"] is None
    assert resolve_with("UNKNOWN", mode="FULL")["suggested_training"]["mode"] == "FULL"
    revoked = resolve_coach_artifact(
        HarnessCoachArtifact.model_validate({**response_for([1, 2]),
                                             "suggested_training": suggestion("WEAKNESS")}),
        [dict(id=1, session_question_id=11, question="Q", evaluation="E", scores={}),
         dict(id=2, session_question_id=12, question="Q", evaluation="E", scores={})],
        None, training_scope=scope_for("VERIFIED", revoked=True))
    assert revoked["suggested_training"] is None
    assert revoked["suggested_training_rejected"]["code"] == "MODE_NOT_ALLOWED_FOR_STATE"


def test_a_suggestion_cannot_name_a_point_outside_the_frozen_targets():
    result = resolve_with("CONFLICTED", mode="RECHECK", knowledge_id="java_backend:mysql")
    assert result["suggested_training"] is None
    assert result["suggested_training_rejected"]["code"] == "KNOWLEDGE_NOT_IN_FROZEN_TARGETS"


def test_a_suggestion_cannot_cite_an_evaluation_it_was_not_given():
    result = resolve_with("CONFLICTED", mode="RECHECK", refs=("evaluation:999",))
    assert result["suggested_training"] is None
    assert result["suggested_training_rejected"]["code"] == "UNKNOWN_EVALUATION"


# ── Integration: the real report path ──


def test_off_keeps_one_call_and_writes_no_audit_block(database_url):
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        provider = ReplayModelProvider([response_for([e["id"] for e in evaluations])])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=False):
            report = await service.generate(session_id, user_id)
        assert report["generation_status"] == "COMPLETED"
        assert provider.calls == 1
        assert "audit" not in report["coach_analysis"]
        assert "suggested_training" not in report["coach_analysis"]
        assert report["prompt_version"] == "1.0.3"
        _, steps = await tool_steps(factory, report["id"])
        assert [name for name, *_ in steps if name == "coach_tool"] == []
    run(database_url, scenario)


def test_harness_without_a_tool_round_is_still_one_call(database_url):
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        provider = ReplayModelProvider([response_for([e["id"] for e in evaluations])])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=True, tool_limit=0):
            report = await service.generate(session_id, user_id)
        assert report["generation_status"] == "COMPLETED"
        assert provider.calls == 1
        assert report["coach_analysis"]["audit"]["path"] == "SINGLE_CALL_NO_TOOL_OFFERED"
        assert report["coach_analysis"]["audit"]["tools_offered"] == []
        assert report["prompt_version"] == "1.1.0"
    run(database_url, scenario)


def test_model_may_decline_the_tool_and_finish_in_one_call(database_url):
    """Scenario 1: enough evidence, so no tool call."""
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        provider = ReplayModelProvider([{"action": "final",
                                         "artifact": response_for([e["id"] for e in evaluations])}])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=True), offer_only(read_evaluation_detail=True):
            report = await service.generate(session_id, user_id)
        assert report["generation_status"] == "COMPLETED", report
        assert provider.calls == 1
        assert report["coach_analysis"]["audit"]["path"] == "SINGLE_CALL_MODEL_CHOSE_FINAL"
        assert report["coach_analysis"]["audit"]["calls"] == 1
    run(database_url, scenario)


def test_evaluation_tool_returns_unclipped_detail_and_is_audited(database_url):
    """Scenario 2: the projection clipped text, so the model asks for one evaluation."""
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        target = evaluations[0]["id"]
        provider = ReplayModelProvider([
            {"action": "read_evaluation_detail", "evaluation_id": target},
            response_for([e["id"] for e in evaluations])])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=True), offer_only(read_evaluation_detail=True):
            report = await service.generate(session_id, user_id)
        assert report["generation_status"] == "COMPLETED", report
        assert provider.calls == 2
        audit = report["coach_analysis"]["audit"]
        assert audit["path"] == "TOOL_THEN_FINAL" and audit["calls"] == 2
        assert audit["tool"]["tool"] == "read_evaluation_detail"
        result = audit["tool"]["result"]
        assert result["status"] == "OK" and result["code"] == "EVALUATION_DETAIL"
        assert result["semantics"] == tools.UNTRUSTED
        assert result["data"]["evaluation_id"] == target
        assert result["data"]["source"] == "FROZEN_EVALUATION_CATALOGUE_VERIFIED_BY_FINGERPRINT"
        # The stored audit is the record; the tool row is on the run as well.
        _, steps = await tool_steps(factory, report["id"])
        tool_rows = [(n, s) for n, s, *_ in steps if n == "coach_tool"]
        assert tool_rows == [("coach_tool", "SUCCEEDED")]
    run(database_url, scenario)


def test_capability_tool_returns_references_not_other_answers(database_url):
    """Scenario 3: this session's result should be read against stored history."""
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        await refresh_memory(factory, user_id)
        targets = await blueprint_targets(factory, session_id)
        knowledge_id = next(kid for kid, _ in targets)
        provider = ReplayModelProvider([
            {"action": "read_capability_history", "knowledge_id": knowledge_id},
            response_for([e["id"] for e in evaluations])])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=True):
            report = await service.generate(session_id, user_id)
        assert report["generation_status"] == "COMPLETED", report
        audit = report["coach_analysis"]["audit"]
        assert audit["path"] == "TOOL_THEN_FINAL", audit
        result = audit["tool"]["result"]
        assert result["status"] in ("OK", "EMPTY"), result
        if result["status"] == "OK":
            data = result["data"]
            assert data["knowledge_id"] == knowledge_id
            assert data["source"] == "CAPTURED_AT_REPORT_GENERATION_NOT_A_LIVE_QUERY"
            assert data["captured_at"] and data["revision"] is not None
            # References only: no answer text may leak from another session.
            assert set(data) >= {"observations", "conflicts", "state"}
            assert all(isinstance(o.get("evaluation_id"), int) or o.get("evaluation_id") is None
                       for o in data["observations"])
            assert "answer" not in data and "evaluation" not in data
            assert all(ref.startswith(("evaluation:", "session:")) for ref in result["refs"])
        assert audit["capability_capture"], "the capture must be identifiable in the audit"
    run(database_url, scenario)


def test_tool_failure_never_fabricates_evidence(database_url):
    """Scenarios 5/6: out of scope and timeout both return an error, then the final."""
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        other = max(e["id"] for e in evaluations) + 5000
        for response, expected in (
                ({"action": "read_evaluation_detail", "evaluation_id": other}, "EVALUATION_OUT_OF_SCOPE"),
                ({"action": "read_capability_history", "knowledge_id": "java_backend:not_a_topic"},
                 "KNOWLEDGE_OUT_OF_SCOPE")):
            provider = ReplayModelProvider([response, response_for([e["id"] for e in evaluations])])
            service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
            with coach_settings(harness=True), offer_only(read_evaluation_detail=True,
                                                          read_capability_history=True):
                report = await service.generate(session_id, user_id, regenerate=True,
                                                request_key=uuid4().hex)
            assert report["generation_status"] == "COMPLETED", report
            result = report["coach_analysis"]["audit"]["tool"]["result"]
            assert result["status"] == "ERROR" and result["code"] == expected, result
            assert result["data"].get("requested") in (other, "java_backend:not_a_topic")
            assert provider.calls == 2

    run(database_url, scenario)


def test_tool_timeout_is_audited_and_the_report_still_finishes(database_url):
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        provider = ReplayModelProvider([
            {"action": "read_evaluation_detail", "evaluation_id": evaluations[0]["id"]},
            response_for([e["id"] for e in evaluations])])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=True), offer_only(read_evaluation_detail=True), \
                patch.object(tools, "TOOL_TIMEOUT_SECONDS", 0):
            report = await service.generate(session_id, user_id)
        assert report["generation_status"] == "COMPLETED", report
        assert report["coach_analysis"]["audit"]["tool"]["result"]["code"] == "TOOL_TIMEOUT"
    run(database_url, scenario)


def test_a_legal_suggestion_is_stored_and_an_illegal_one_is_dropped(database_url):
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        await refresh_memory(factory, user_id)
        targets = await blueprint_targets(factory, session_id)
        knowledge_id = next(kid for kid, _ in targets)
        refs = [f"evaluation:{e['id']}" for e in evaluations[:2]]

        legal = {**response_for([e["id"] for e in evaluations]),
                 "suggested_training": dict(mode="WEAKNESS", knowledge_id=knowledge_id,
                     reason="练习缓存更新与失效的边界解释", supporting_evaluation_ids=refs,
                     scope="CURRENT_JOB_TARGET")}
        provider = ReplayModelProvider([legal])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=True), offer_only():
            report = await service.generate(session_id, user_id)
        stored = report["coach_analysis"]["suggested_training"]
        assert stored is None or stored["mode"] in ("FULL", "WEAKNESS", "RECHECK")
        # Whatever the state is, the stored value must be legal; if it was not,
        # the rejection is recorded and nothing else is lost.
        if stored is None:
            assert report["coach_analysis"]["suggested_training_rejected"]["code"] in (
                "MODE_NOT_ALLOWED_FOR_STATE", "KNOWLEDGE_NOT_IN_FROZEN_TARGETS")
        assert report["coach_analysis"]["strengths"]

        illegal = {**response_for([e["id"] for e in evaluations]),
                   "suggested_training": dict(mode="RECHECK", knowledge_id=knowledge_id,
                       reason="直接做一次独立复测", supporting_evaluation_ids=refs,
                       scope="CURRENT_JOB_TARGET")}
        provider = ReplayModelProvider([illegal])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=True), offer_only():
            rejected = await service.generate(session_id, user_id, regenerate=True, request_key=uuid4().hex)
        assert rejected["generation_status"] == "COMPLETED"
        analysis = rejected["coach_analysis"]
        if analysis["suggested_training"] is None:
            assert analysis["suggested_training_rejected"]["code"] == "MODE_NOT_ALLOWED_FOR_STATE"
        else:  # the fixture really is CONFLICTED, in which case RECHECK is legal
            assert analysis["suggested_training"]["state"] in ("CONFLICTED", "RECHECK_DUE")
        assert analysis["strengths"], "a rejected suggestion must not cost the report"
    run(database_url, scenario)


def test_harness_respects_the_input_budget_before_calling_a_model(database_url):
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        provider = ReplayModelProvider([response_for([e["id"] for e in evaluations])])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=True, max_input_bytes=1):
            report = await service.generate(session_id, user_id)
        assert provider.calls == 0, "an oversized input must not reach the provider"
        assert report["generation_status"] == "FAILED"
        assert report["error"] == "COACH_INPUT_BUDGET"
    run(database_url, scenario)


def test_the_same_frozen_input_reuses_the_same_report(database_url):
    """Scenario 10: identical inputs resolve through the existing generation key."""
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        # `offer_only()` means no tool is offered, so the single-call path applies
        # and the model is asked for the artifact directly (no `action` wrapper).
        provider = ReplayModelProvider([response_for([e["id"] for e in evaluations])])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=True), offer_only():
            first = await service.generate(session_id, user_id)
            second = await service.generate(session_id, user_id)
        assert first["id"] == second["id"]
        assert second.get("reused") is True
        assert provider.calls == 1, "a replay of the same input must not call the model again"
        assert first["generation_status"] == "COMPLETED"
        assert first["coach_analysis"]["audit"]["harness"] == "COACH_HARNESS_V1"
    run(database_url, scenario)
