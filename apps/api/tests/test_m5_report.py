"""Replay-only coaching and PostgreSQL publication/provenance tests."""
import asyncio
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_COACH_ROUTE
from interview_api.modules.interview.models import InterviewSession, InterviewEvaluation, InterviewSessionQuestion
from interview_api.modules.interview_report.models import FinalInterviewReport, InterviewReportAttempt, InterviewPerformanceObservation, PerformanceObservationEvaluation
from interview_api.modules.interview_report.schemas import CoachArtifact
from interview_api.modules.interview_report.statistics import interview_records, deterministic_statistics
from interview_api.modules.interview_report.resolver import resolve_coach_artifact
from interview_api.modules.interview_report.service import InterviewReportService
from interview_api.modules.memory.models import UserMemoryItem
from interview_api.modules.memory.proposal_models import MemoryProposal
from test_m4_interview import seed, queue_start, queue_answer, run_with_database


def coach_response(ids):
    refs = [f"evaluation:{id_}" for id_ in ids]
    return dict(overall_summary="Replay fixture：本场回答展示了缓存实践，解释边界仍有提升空间。",
        strengths=[dict(statement="本场具体实现描述有明确技术依据", supporting_evaluation_ids=[refs[-1]])],
        weaknesses=[dict(statement="本场并发更新边界说明尚不完整", supporting_evaluation_ids=[refs[0]])],
        recurring_issues=[dict(statement="本场不同回答均需要更明确的并发更新说明", supporting_evaluation_ids=[refs[0], refs[1], refs[0]])],
        job_alignment=[dict(statement="本场缓存主题与快照岗位要求相关", supporting_evaluation_ids=[refs[0]])],
        recommended_actions=["用真实项目案例解释缓存更新取舍"], practice_plan=["先复盘缺失点，再练习边界条件解释"],
        memory_proposals=[dict(proposal_type="PREPARATION_FOCUS", content="基于本场回答，准备缓存并发更新的解释练习", supporting_evaluation_ids=[refs[0]]),
            dict(proposal_type="SKILL_OBSERVATION", content="本场缓存回答的并发边界说明需要进一步准备", supporting_evaluation_ids=[refs[0], refs[1]]),
            dict(proposal_type="EXPERIENCE", content="本场完成了缓存主题的模拟面试复盘", supporting_evaluation_ids=[refs[-1]])])


async def completed_interview(factory, *, budget=6):
    core, main, evaluator, user_id, detail = await seed(factory, budget=budget)
    session_id = detail["id"]
    await queue_start(core, main, user_id, session_id)
    sequence = (("FOLLOW_UP", 2), ("NEXT", 3), ("DEEP_DIVE", 4), ("ADJUST_DIFFICULTY", 5), ("NEXT", 4), ("COMPLETE", 1))
    for index in range(budget):
        action, score = sequence[index]
        request = await queue_answer(core, main, evaluator, user_id, session_id, action, score)
        await core.answer(session_id, user_id, request.question_id, request.idempotency_key, request.content)
    async with factory() as db:
        _, evaluations = await interview_records(db, session_id)
    return core, user_id, session_id, evaluations


async def seeded_report(factory):
    _, user_id, session_id, evaluations = await completed_interview(factory)
    provider = ReplayModelProvider([coach_response([e["id"] for e in evaluations])])
    service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
    report = await service.generate(session_id, user_id)
    assert report["generation_status"] == "COMPLETED", report
    return service, user_id, session_id, report, provider


def test_statistics_resolver_schema():
    ids = [10, 20]
    response = coach_response(ids)
    artifact = CoachArtifact.model_validate(response)
    evaluations = [dict(id=id_, session_question_id=id_ * 2, question="真实问题", evaluation="真实评价", scores=dict(technical_correctness=3, coverage=3, depth=3, relevance=3, communication=3)) for id_ in ids]
    result = resolve_coach_artifact(artifact, evaluations)
    issue = result["recurring_issues"][0]
    assert issue["occurrence_count"] == 2 and issue["supporting_evaluation_ids"] == ids
    assert issue["supporting_evaluations"][0]["question_id"] == 20
    empty = deterministic_statistics([], [])
    assert empty["evaluated_count"] == 0 and empty["overall_average"] is None
    assert all(value is None for value in empty["dimension_averages"].values())
    for field in ("overall_score", "source_count", "question_id", "occurrence_count", "technical_correctness"):
        invalid = deepcopy(response)
        invalid[field] = 3
        with pytest.raises(ValidationError): CoachArtifact.model_validate(invalid)
    invalid = deepcopy(response)
    invalid["strengths"][0]["supporting_evaluation_ids"] = [10]
    with pytest.raises(ValidationError): CoachArtifact.model_validate(invalid)


def test_report_persistence_idempotency_provenance(database_url):
    async def scenario(factory):
        service, user_id, session_id, report, provider = await seeded_report(factory)
        assert report["statistics"]["overall_average"] == 3.1667
        assert report["statistics"]["deep_dive_count"] == 1
        assert report["mode"] == "replay"
        repeated = await service.generate(session_id, user_id)
        assert repeated["id"] == report["id"] and repeated["reused"] and provider.calls == 1
        detail = await service.detail(report["id"], user_id)
        assert detail["current_report"]["id"] == report["id"] and len(detail["evaluations"]) == 6
        assert len(detail["memory_proposals"]) == 3 and len(detail["observations"]) == 3
        assert len((await service.list_reports(user_id))["items"]) == 1
        async with factory() as db:
            assert await db.scalar(select(func.count(UserMemoryItem.id)).where(UserMemoryItem.user_id == user_id)) == 0
            observations = (await db.scalars(select(InterviewPerformanceObservation).where(InterviewPerformanceObservation.report_id == report["id"]))).all()
            issue = next(o for o in observations if o.observation_type == "RECURRING_ISSUE")
            assert issue.occurrence_count == len(set(issue.supporting_evaluation_ids)) == 2
            ids = (await db.scalars(select(PerformanceObservationEvaluation.evaluation_id).where(PerformanceObservationEvaluation.observation_id == issue.id))).all()
            assert sorted(ids) == issue.supporting_evaluation_ids
            assert (await db.get(InterviewSession, session_id)).status == "COMPLETED"
        with pytest.raises(AppError) as exc: await service.detail(report["id"], user_id + 999999)
        assert exc.value.status_code == 404
        async with factory() as db:
            with pytest.raises(DBAPIError):
                await db.execute(update(FinalInterviewReport).where(FinalInterviewReport.id == report["id"]).values(coach_analysis_json={"overwritten": True}))
            await db.rollback()
    with patch("httpx.AsyncClient.request", side_effect=AssertionError("Replay must not use network")):
        asyncio.run(run_with_database(database_url, scenario))


@pytest.mark.parametrize("case", ["unknown", "recurring", "score", "numeric_prose", "absolute", "repair", "hard_fail", "failed_regenerate", "budget", "timeout"])
def test_coach_generation_edges(database_url, case):
    async def scenario(factory):
        _, user_id, session_id, evaluations = await completed_interview(factory)
        value = coach_response([e["id"] for e in evaluations])
        responses = []
        if case == "unknown": value["weaknesses"][0]["supporting_evaluation_ids"] = ["evaluation:99999999"]
        if case == "recurring": value["recurring_issues"][0]["supporting_evaluation_ids"] = [f"evaluation:{evaluations[0]['id']}"]
        if case == "score": value["overall_score"] = 5
        if case == "numeric_prose": value["overall_summary"] = "本场总分 5分"
        if case == "absolute": value["memory_proposals"][0]["content"] = "用户精通 Redis"
        if case == "repair":
            invalid = deepcopy(value)
            del invalid["job_alignment"]
            fixed = deepcopy(invalid)
            fixed["job_alignment"] = []
            responses = [invalid, fixed]
        elif case == "hard_fail": responses = [{"bad": "unchanged"}, {"bad": "unchanged"}]
        elif case == "score": responses = [value, value]
        else: responses = [value]
        provider = ReplayModelProvider(responses)
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        if case == "budget":
            from interview_api.core.config import settings
            with patch.object(settings, "interview_coach_max_input_bytes", 1): report = await service.generate(session_id, user_id)
            assert provider.calls == 0
        elif case == "timeout":
            from interview_api.infrastructure.models.provider import ModelProviderError
            async def timed_out(*_args, **_kwargs):
                raise ModelProviderError("model_timeout", "Replay injected model timeout")
            with patch.object(provider, "generate", timed_out):
                report = await service.generate(session_id, user_id)
            assert report["error"] == "model_timeout"
            assert report["attempts"][0]["status"] == "FAILED"
            assert report["attempts"][0]["steps"][0]["status"] == "FAILED"
            assert report["attempts"][0]["steps"][0]["error"]
        else: report = await service.generate(session_id, user_id)
        if case == "repair":
            assert report["generation_status"] == "COMPLETED"
            assert report["attempts"][0]["steps"][0]["structured_retry"] == 1
            return
        if case == "failed_regenerate":
            assert report["generation_status"] == "COMPLETED"
            provider.responses.extend([{"bad": "unchanged"}, {"bad": "unchanged"}])
            failed = await service.generate(session_id, user_id, regenerate=True, request_key=uuid4().hex)
            assert failed["generation_status"] == "FAILED" and failed["id"] != report["id"]
            assert (await service.session_detail(session_id, user_id))["current_report"]["id"] == report["id"]
            return
        assert report["generation_status"] == "FAILED" and not report["is_current"], report
        async with factory() as db:
            assert (await db.get(InterviewSession, session_id)).status == "COMPLETED"
            assert not await db.scalar(select(func.count(InterviewPerformanceObservation.id)).where(InterviewPerformanceObservation.report_id == report["id"]))
            assert not await db.scalar(select(func.count(MemoryProposal.id)).where(MemoryProposal.report_id == report["id"]))
            assert not await db.scalar(select(func.count(UserMemoryItem.id)).where(UserMemoryItem.user_id == user_id))
        assert report["statistics"]["evaluated_count"] == 6 and report["error"]
    asyncio.run(run_with_database(database_url, scenario))


def test_zero_evaluation_report_does_not_call_coach(database_url):
    async def scenario(factory):
        core, main, _, user_id, detail = await seed(factory, budget=1)
        await queue_start(core, main, user_id, detail["id"])
        await core.skip(detail["id"], user_id, (await core.read_state(detail["id"], user_id))[3].id)
        provider = ReplayModelProvider([])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        report = await service.generate(detail["id"], user_id)
        assert report["generation_status"] == "COMPLETED" and report["producer"] == "deterministic"
        assert report["statistics"]["overall_average"] is None and provider.calls == 0
        view = await service.detail(report["id"], user_id)
        assert view["observations"] == view["memory_proposals"] == []
        assert report["coach_analysis"]["analysis_status"] == "NO_PERFORMANCE_ANALYSIS"
    asyncio.run(run_with_database(database_url, scenario))


def test_report_concurrent_generation_keeps_newer_current(database_url):
    async def scenario(factory):
        _, user_id, session_id, evaluations = await completed_interview(factory)
        started, release = asyncio.Event(), asyncio.Event()
        value = coach_response([e["id"] for e in evaluations])
        class WaitingProvider(ReplayModelProvider):
            async def generate(self, messages, *, schema=None):
                started.set()
                await release.wait()
                return await super().generate(messages, schema=schema)
        slow = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: WaitingProvider([value])}, concurrency=3))
        fast = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: ReplayModelProvider([value])}, concurrency=3))
        first = asyncio.create_task(slow.generate(session_id, user_id))
        await asyncio.wait_for(started.wait(), 10)
        second = await fast.generate(session_id, user_id, regenerate=True, request_key=uuid4().hex)
        assert second["generation_status"] == "COMPLETED"
        release.set()
        with pytest.raises(AppError) as exc: await first
        assert exc.value.code == "REPORT_GENERATION_CONFLICT"
        detail = await fast.session_detail(session_id, user_id)
        assert detail["current_report"]["id"] == second["id"]
        assert sum(item["is_current"] for item in detail["history"]) == 1
    asyncio.run(run_with_database(database_url, scenario))


@pytest.mark.parametrize("case", ["retry", "same_generation", "publication_rollback", "response_read_failure"])
def test_report_attempt_and_publication_edges(database_url, case):
    async def scenario(factory):
        _, user_id, session_id, evaluations = await completed_interview(factory)
        value = coach_response([e["id"] for e in evaluations])
        provider = ReplayModelProvider([value])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}, concurrency=3))
        if case == "retry":
            provider.responses = [{"bad": "unchanged"}, {"bad": "unchanged"}, value]
            failed = await service.generate(session_id, user_id)
            assert failed["status"] == "FAILED"
            successful = await service.generate(session_id, user_id)
            assert successful["id"] == failed["id"] and successful["status"] == "COMPLETED"
            assert [a["status"] for a in successful["attempts"]] == ["FAILED", "SUCCEEDED"]
            assert successful["attempts"][0]["agent_run_id"] != successful["attempts"][1]["agent_run_id"]
            return
        if case == "same_generation":
            started, release = asyncio.Event(), asyncio.Event()
            class WaitingProvider(ReplayModelProvider):
                async def generate(self, messages, *, schema=None):
                    started.set()
                    await release.wait()
                    return await super().generate(messages, schema=schema)
            slow = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: WaitingProvider([value])}, concurrency=3))
            first = asyncio.create_task(slow.generate(session_id, user_id))
            await asyncio.wait_for(started.wait(), 10)
            second = await service.generate(session_id, user_id)
            release.set()
            with pytest.raises(AppError): await first
            record = await service.report_record(second["id"], user_id)
            assert record["status"] == "COMPLETED" and record["is_current"]
            assert len(record["attempts"]) == 2 and {a["status"] for a in record["attempts"]} == {"SUCCEEDED", "FAILED"}
            async with factory() as db:
                assert await db.scalar(select(func.count(FinalInterviewReport.id)).where(FinalInterviewReport.session_id == session_id)) == 1
            return
        if case == "publication_rollback":
            with patch("interview_api.modules.interview_report.service.create_report_proposals", side_effect=RuntimeError("forced publication failure")):
                failed = await service.generate(session_id, user_id)
            assert failed["status"] == "FAILED" and not failed["is_current"]
            async with factory() as db:
                assert not await db.scalar(select(func.count(InterviewPerformanceObservation.id)).where(InterviewPerformanceObservation.report_id == failed["id"]))
                assert not await db.scalar(select(func.count(MemoryProposal.id)).where(MemoryProposal.report_id == failed["id"]))
                assert (await db.get(InterviewSession, session_id)).status == "COMPLETED"
            return
        with patch.object(service, "report_record", side_effect=RuntimeError("simulated response read failure")):
            with pytest.raises(RuntimeError): await service.generate(session_id, user_id)
        record = (await service.session_detail(session_id, user_id))["current_report"]
        assert record["status"] == "COMPLETED" and record["attempts"][0]["status"] == "SUCCEEDED"
    asyncio.run(run_with_database(database_url, scenario))


def test_report_api_authorization_and_read(database_url):
    async def scenario(factory):
        import httpx
        from interview_api.main import app
        from interview_api.api.deps import get_current_user
        from interview_api.core.rate_limit import interview_chat_limit
        service, user_id, session_id, report, provider = await seeded_report(factory)
        actor = user_id
        async def user_override(): return SimpleNamespace(id=actor, role="USER")
        async def no_limit(): return None
        app.dependency_overrides[get_current_user] = user_override
        app.dependency_overrides[interview_chat_limit] = no_limit
        try:
            with patch("interview_api.modules.interview_report.router.build_core", return_value=service):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    listed = await client.get("/api/v1/interview-reports")
                    assert listed.status_code == 200 and listed.json()["data"]["items"][0]["id"] == report["id"]
                    response = await client.get(f"/api/v1/interview-reports/sessions/{session_id}")
                    assert response.status_code == 200 and response.json()["data"]["statistics"]["deep_dive_count"] == 1
                    response = await client.get(f"/api/v1/interview-reports/{report['id']}")
                    assert response.status_code == 200 and len(response.json()["data"]["evaluations"]) == 6
                    response = await client.post(f"/api/v1/interview-reports/sessions/{session_id}/generate", json={"regenerate": False})
                    assert response.status_code == 200 and response.json()["data"]["reused"]
                    assert provider.calls == 1
                    response = await client.post(f"/api/v1/interview-reports/sessions/{session_id}/generate", json={"regenerate": True})
                    assert response.status_code == 422
                    removed = await client.delete(f"/api/v1/interview/sessions/{session_id}")
                    assert removed.status_code == 409
                    actor = user_id + 999999
                    assert (await client.get(f"/api/v1/interview-reports/{report['id']}")).status_code == 404
                    assert (await client.get(f"/api/v1/interview-reports/sessions/{session_id}")).status_code == 404
                    assert (await client.get("/api/v1/interview-reports")).json()["data"]["items"] == []
        finally:
            app.dependency_overrides.clear()
    asyncio.run(run_with_database(database_url, scenario))


def test_successful_regeneration_preserves_history_and_restricts_delete(database_url):
    async def scenario(factory):
        service, user_id, session_id, first, provider = await seeded_report(factory)
        evaluations = (await service.session_detail(session_id, user_id))["evaluations"]
        provider.responses.append(coach_response([e["id"] for e in evaluations]))
        request_key = uuid4().hex
        second = await service.generate(session_id, user_id, regenerate=True, request_key=request_key)
        assert second["status"] == "COMPLETED" and second["id"] != first["id"]
        assert second["generation_number"] == first["generation_number"] + 1
        assert (await service.generate(session_id, user_id, regenerate=True, request_key=request_key))["id"] == second["id"]
        # Retrying an old successful automatic key returns history, never reactivates it.
        old = await service.generate(session_id, user_id)
        assert old["id"] == first["id"] and not old["is_current"]
        detail = await service.session_detail(session_id, user_id)
        assert detail["current_report"]["id"] == second["id"] and len(detail["history"]) == 2
        assert old["coach_analysis"] == first["coach_analysis"] and old["statistics"] == first["statistics"]
        async with factory() as db:
            with pytest.raises(DBAPIError):
                await db.execute(delete(InterviewSession).where(InterviewSession.id == session_id))
            await db.rollback()
            with pytest.raises(DBAPIError):
                await db.execute(delete(InterviewEvaluation).where(InterviewEvaluation.id == evaluations[0]["id"]))
            await db.rollback()
            with pytest.raises(DBAPIError):
                await db.execute(delete(FinalInterviewReport).where(FinalInterviewReport.id == first["id"]))
            await db.rollback()
    asyncio.run(run_with_database(database_url, scenario))


def test_statistics_ignore_incomplete_question_and_session_summary(database_url):
    async def scenario(factory):
        _, user_id, session_id, evaluations = await completed_interview(factory)
        async with factory() as db:
            session = await db.get(InterviewSession, session_id)
            assert session.completion_json["evaluated_count"] == 6
            # An inconsistent/imported incomplete Question is not valid performance evidence.
            await db.execute(update(InterviewSessionQuestion).where(
                InterviewSessionQuestion.id == evaluations[0]["session_question_id"]).values(status="SKIPPED"))
            await db.commit()
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: ReplayModelProvider([])}))
        view = await service.session_detail(session_id, user_id)
        assert view["statistics"]["evaluated_count"] == 5 and view["statistics"]["skipped_count"] == 1
        assert evaluations[0]["id"] not in {e["id"] for e in view["evaluations"]}
        assert view["statistics"]["overall_average"] != 3.1667
        async with factory() as db:
            assert (await db.get(InterviewSession, session_id)).completion_json["evaluated_count"] == 6
    asyncio.run(run_with_database(database_url, scenario))
