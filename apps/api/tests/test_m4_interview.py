"""Offline replay integration tests against actual PostgreSQL transactions."""
import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func, update, delete
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import interview_api.modules.models  # noqa

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
from interview_api.modules.candidate_profile.service import CandidateProfileService
from interview_api.modules.candidate_profile.schemas import CorrectionRequest
from interview_api.modules.candidate_profile.models import CandidateProfile, CandidateProfileVersion
from interview_api.modules.interview.models import InterviewSession, InterviewContextSnapshot, InterviewSessionQuestion, InterviewAnswer, InterviewEvaluation, InterviewMessage, InterviewAgentCall
from interview_api.modules.interview.artifacts import DIMENSIONS, EvaluationArtifact
from interview_api.modules.interview.controller import SessionController
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.context import question_policy, validate_question
from interview_api.modules.interview.schemas import CreateSessionRequest, SendMessageRequest
from interview_api.modules.experience.models import InterviewQuestion, InterviewExperience, ExperienceAgentStepRun, ExperienceAgentRun
from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion, JobRequirement
from interview_api.modules.memory.models import UserMemoryItem, UserSkillProfile
from test_m3_candidate_profile import seed as seed_resume, response, model_router


async def seed(factory, *, public=True, budget=6):
    user_id, resume_id = await seed_resume(factory)
    router, _ = model_router([response(resume_id)])
    profile = await ResumeProfileAgent(factory, router).run(resume_id)
    assert profile["status"] == "COMPLETED", profile
    async with factory() as db:
        company = Company(canonical_name="M4 Replay Fixture " + uuid4().hex)
        db.add(company)
        await db.flush()
        job = Job(company_id=company.id, canonical_title="Backend Demo")
        db.add(job)
        await db.flush()
        jd = JobDescriptionVersion(job_id=job.id, version_number=1, raw_text="Replay JD: Python Redis 缓存", publication_status="PUBLISHED")
        db.add(jd)
        await db.flush()
        db.add(JobRequirement(job_description_version_id=jd.id, statement="Python Redis 缓存", requirement_type="SKILL"))
        if public:
            experience = InterviewExperience(company_id=company.id, job_id=job.id, review_status="APPROVED", summary="Replay fixture public experience", content_text="Replay fixture content")
            db.add(experience)
            await db.flush()
            db.add(InterviewQuestion(company_id=company.id, job_id=job.id, experience_id=experience.id,
                question="请解释 Redis 缓存一致性", standard_answer="失效策略\n并发更新", review_status="APPROVED", category="缓存", difficulty="MEDIUM"))
            db.add(InterviewQuestion(company_id=company.id, job_id=job.id, question="UNREVIEWED secret", review_status="WAITING_REVIEW"))
        await db.commit()
        request = CreateSessionRequest(company_id=company.id, job_id=job.id, question_count=budget)
    main, evaluator = ReplayModelProvider([]), ReplayModelProvider([])
    core = MockInterviewService(factory, ModelRouter({INTERVIEW_MAIN_ROUTE: main, ANSWER_EVALUATION_ROUTE: evaluator}))
    detail = await core.create(user_id, request)
    return core, main, evaluator, user_id, detail


def evaluation(action="NEXT", score=4):
    return dict(**{key: score for key in DIMENSIONS}, evaluation="Replay: 本轮回答评价",
        covered_points=["缓存"], missing_points=["并发更新"], factual_or_reasoning_issues=[],
        follow_up_focus=["并发更新"], next_action_recommendation=action)


def question(context, policy, action="NEXT", difficulty="MEDIUM"):
    body = dict(question="Replay: 请说明具体技术实现和取舍", expected_points=["原理", "实践", "边界"],
                dimension="缓存", difficulty=difficulty, source_type=policy["source_type"])
    if body["source_type"] == "PUBLIC_QUESTION":
        public = next(q for q in context["public_questions"] if q["id"] == policy["source_id"])
        body.update(question=public["question"], source_question_id=public["id"])
    if body["source_type"] == "JOB_REQUIREMENT": body["job_requirement_id"] = policy["source_id"]
    if body["source_type"] == "CANDIDATE_PROFILE":
        fact = next(f for f in context["candidate_facts"] if f["id"] == policy["source_id"])
        body.update(candidate_fact_ids=[fact["id"]], candidate_evidence_ids=fact["evidence_ids"])
    return dict(recommendation=action, question=body)


async def queue_start(core, main, user_id, session_id):
    _, context, questions, _, _ = await core.read_state(session_id, user_id)
    main.responses.append(question(context, question_policy(context, questions)))
    return await core.start(session_id, user_id)


async def queue_answer(core, main, evaluator, user_id, session_id, action="NEXT", score=4):
    session, context, questions, active, tokens = await core.read_state(session_id, user_id)
    chosen = SessionController.choose_action(context, questions, active, action, rounds=session.turn_count + 1, tokens=tokens, started_at=session.started_at)
    evaluator.responses.append(evaluation(action, score))
    if chosen != "COMPLETE":
        difficulty = SessionController.adjusted_difficulty(active, evaluation(action, score)) if chosen == "ADJUST_DIFFICULTY" else "MEDIUM"
        main.responses.append(question(context, question_policy(context, questions, chosen), chosen, difficulty))
    return SendMessageRequest(content="Replay fixture answer: 缓存失效与一致性", question_id=active.id, idempotency_key=uuid4().hex)


async def run_with_database(url, fn):
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await fn(factory)
    finally:
        await engine.dispose()


async def full_replay(factory):
    core, main, evaluator, user_id, detail = await seed(factory)
    session_id = detail["id"]
    pinned = deepcopy(detail["context_snapshot"])
    first = await queue_start(core, main, user_id, session_id)
    assert first["source"] == "PUBLIC_QUESTION"
    assert "UNREVIEWED" not in json.dumps(pinned)
    # Real new profile/JD and mutable public edits must not change this session.
    async with factory() as db:
        await db.execute(update(InterviewQuestion).where(InterviewQuestion.id == first["source_question_id"]).values(question="ADMIN EDIT AFTER START"))
        db.add(JobDescriptionVersion(job_id=pinned["job_id"], version_number=2, raw_text="New JD must not drift", publication_status="PUBLISHED"))
        await CandidateProfileService(db).correct(user_id, CorrectionRequest(base_version_id=pinned["candidate_profile_version_id"], field_path="skills.0.label", new_value="Redis 缓存", reason="Replay fixture correction"))
        await db.commit()
    events_all = []
    for index, (action, score) in enumerate((("FOLLOW_UP", 2), ("NEXT", 3), ("DEEP_DIVE", 4), ("ADJUST_DIFFICULTY", 5), ("NEXT", 4), ("COMPLETE", 1))):
        request = await queue_answer(core, main, evaluator, user_id, session_id, action, score)
        events = await core.answer(session_id, user_id, request.question_id, request.idempotency_key, request.content)
        assert events[0][0] == "evaluation"
        assert events[0][1]["question_id"] == request.question_id
        assert events[-1][0] == "done"
        if index == 0: assert events[1][0] == "follow_up" and events[1][1]["source"] == "FOLLOW_UP"
        if index == 1: assert events[1][1]["source"] == "JOB_REQUIREMENT"
        if index == 2: assert events[1][1]["question_kind"] == "DEEP_DIVE" and events[1][1]["is_dynamic"]
        if index == 3:
            assert events[1][1]["difficulty"] == "HARD" and events[1][1]["source"] == "CANDIDATE_PROFILE"
            assert events[1][1]["evidence"]["project_ids"] and events[1][1]["evidence"]["evidence_ids"]
        assert events == await core.answer(session_id, user_id, request.question_id, request.idempotency_key, request.content)
        events_all.extend(event for event, _ in events)
        assert (await core.detail(session_id, user_id))["context_snapshot"] == pinned
    final = await core.detail(session_id, user_id)
    assert final["status"] == "COMPLETED" and final["active_question_id"] is None
    assert all(q["status"] == "COMPLETED" for q in final["questions"])
    assert final["completion_json"]["overall_average"] == 3.1667
    assert final["completion_json"]["answered_count"] == final["completion_json"]["evaluated_count"] == 6
    assert final["completion_json"]["follow_up_count"] == final["completion_json"]["dynamic_question_count"] == 1
    assert final["completion_json"]["mode"] == "replay"
    async with factory() as db:
        answers = list((await db.scalars(select(InterviewAnswer).where(InterviewAnswer.session_id == session_id))).all())
        evaluations = list((await db.scalars(select(InterviewEvaluation).where(InterviewEvaluation.session_id == session_id))).all())
        assert len(answers) == len(evaluations) == 6
        assert all(e.session_question_id == next(a.session_question_id for a in answers if a.id == e.answer_id) for e in evaluations)
        assert all(e.provider == "replay" for e in evaluations)
        runs = (await db.scalars(select(ExperienceAgentRun).join(InterviewAgentCall, InterviewAgentCall.agent_run_id == ExperienceAgentRun.id).where(InterviewAgentCall.session_id == session_id))).all()
        assert {r.agent_role for r in runs} == {"InterviewMainAgent", "AnswerEvaluationAgent"}
        assert not await db.scalar(select(func.count(UserMemoryItem.id)).where(UserMemoryItem.user_id == user_id))
        assert not await db.scalar(select(func.count(UserSkillProfile.id)).where(UserSkillProfile.user_id == user_id))
        assert await db.scalar(select(func.count(CandidateProfileVersion.id)).join(CandidateProfile, CandidateProfile.id == CandidateProfileVersion.candidate_profile_id).where(CandidateProfile.user_id == user_id)) == 2
    return dict(session_id=session_id, user_id=user_id, status=final["status"], snapshot_fingerprint=final["snapshot_fingerprint"], events=events_all,
                summary=final["completion_json"], main_calls=main.calls, evaluation_calls=evaluator.calls, mode="replay / fixture / demo")


def test_replay_full_interview_snapshot_dedupe_statistics(database_url):
    # A failing network sentinel proves this entire replay pipeline is offline.
    with patch("httpx.AsyncClient.request", side_effect=AssertionError("Replay must not use network")):
        result = asyncio.run(run_with_database(database_url, full_replay))
    assert result["main_calls"] == result["evaluation_calls"] == 6


@pytest.mark.parametrize("score", [0, 6, 3.1, "3", True])
def test_five_dimension_strict_scores(score):
    with pytest.raises(ValidationError): EvaluationArtifact.model_validate(evaluation(score=score))


def test_legacy_parser_has_no_default_score():
    from interview_api.modules.interview.service import InterviewChatService
    with pytest.raises(ValueError): InterviewChatService._parse_decision("invalid JSON")


def test_legacy_private_writers_and_background_are_disabled():
    from interview_api.modules.interview.service import InterviewChatService, _generate_questions_background

    async def run():
        # No DB/service dependencies: wrappers reject before reaching old writers.
        for call in (
            InterviewChatService._init_lightweight_plan(None, 1),
            InterviewChatService.generate_questions(None, 1),
            _generate_questions_background(1, None, None, None, None, None),
        ):
            with pytest.raises(AppError) as error:
                await call
            assert error.value.code == "LEGACY_READ_ONLY"
        events = [event async for event in InterviewChatService._handle_position_confirmation(None)]
        assert len(events) == 1 and '"code": "LEGACY_READ_ONLY"' in events[0]

    asyncio.run(run())


@pytest.mark.parametrize("case", ["repair", "hard_fail", "unknown_public", "unknown_evidence", "wrong_source", "duplicate", "rollback", "snapshot_immutable", "stale_start", "skip", "no_public", "legacy", "cross_user"])
def test_controller_and_model_edges(database_url, case):
    async def scenario(factory):
        core, main, evaluator, user_id, detail = await seed(factory, public=case != "no_public", budget=2)
        session_id = detail["id"]
        if case == "cross_user":
            with pytest.raises(AppError) as exc: await core.start(session_id, user_id + 999999)
            assert exc.value.status_code == 404
            return
        if case == "legacy":
            async with factory() as db:
                legacy = InterviewSession(user_id=user_id, workflow_mode="legacy")
                db.add(legacy); await db.commit()
                legacy_id = legacy.id
            assert (await core.detail(legacy_id, user_id))["legacy"]
            with pytest.raises(AppError) as exc: await core.start(legacy_id, user_id)
            assert exc.value.code == "LEGACY_READ_ONLY"
            return
        first = await queue_start(core, main, user_id, session_id)
        if case == "no_public":
            assert detail["context_snapshot"]["coverage_insufficient"] and first["source"] == "JOB_REQUIREMENT"
            return
        if case == "snapshot_immutable":
            async with factory() as db:
                with pytest.raises(DBAPIError):
                    await db.execute(update(InterviewContextSnapshot).where(InterviewContextSnapshot.id == detail["context_snapshot_id"]).values(context_json={}))
                await db.rollback()
            return
        if case == "stale_start":
            _, ctx, _, _, _ = await core.read_state(session_id, user_id)
            from interview_api.modules.interview.agents import AgentResult
            from interview_api.modules.interview.artifacts import MainArtifact
            artifact = MainArtifact.model_validate(question(ctx, question_policy(ctx, [])))
            async with factory() as db:
                with pytest.raises(AppError):
                    await SessionController(db).start(session_id, user_id, 0, AgentResult(artifact, 1, "replay", "fixture", "1", 0, {}), question_policy(ctx, []))
            return
        if case == "skip":
            _, ctx, qs, _, _ = await core.read_state(session_id, user_id)
            main.responses.append(question(ctx, question_policy(ctx, qs)))
            next_q = (await core.skip(session_id, user_id, first["question_id"]))["next_question"]
            await core.skip(session_id, user_id, next_q["question_id"])
            final = await core.detail(session_id, user_id)
            assert final["completion_json"]["skipped_count"] == 2 and final["completion_json"]["overall_average"] is None
            return
        request = await queue_answer(core, main, evaluator, user_id, session_id)
        if case == "repair":
            value = evaluator.responses.pop()
            evaluator.responses.extend([json.dumps(value, ensure_ascii=False)[:-1] + ",", value])
        if case == "hard_fail": evaluator.responses[:] = ["bad JSON", "still bad"]
        if case in ("unknown_public", "unknown_evidence", "wrong_source"):
            if case == "unknown_public": main.responses[-1]["question"]["source_question_id"] = 99999999
            if case == "unknown_evidence": main.responses[-1]["question"]["candidate_evidence_ids"] = ["fake-evidence"]
            if case == "wrong_source": main.responses[-1]["question"]["source_type"] = "DYNAMIC_GENERATED"
        if case == "rollback":
            original = SessionController._new_question
            async def fail(*args, **kwargs):
                await original(*args, **kwargs)
                raise RuntimeError("Injected failure after creating next question")
            with patch.object(SessionController, "_new_question", fail), pytest.raises(RuntimeError):
                await core.answer(session_id, user_id, request.question_id, request.idempotency_key, request.content)
        elif case in ("hard_fail", "unknown_public", "unknown_evidence", "wrong_source"):
            with pytest.raises(Exception): await core.answer(session_id, user_id, request.question_id, request.idempotency_key, request.content)
        else:
            events = await core.answer(session_id, user_id, request.question_id, request.idempotency_key, request.content)
            if case == "repair":
                async with factory() as db:
                    ev = await db.scalar(select(InterviewEvaluation).where(InterviewEvaluation.session_id == session_id))
                    assert ev.structured_retry == 1
            if case == "duplicate":
                assert events == await core.answer(session_id, user_id, request.question_id, request.idempotency_key, request.content)
                with pytest.raises(AppError): await core.answer(session_id, user_id, request.question_id, uuid4().hex, request.content)
            return
        async with factory() as db:
            assert await db.scalar(select(func.count(InterviewEvaluation.id)).where(InterviewEvaluation.session_id == session_id)) == 0
            answer = await db.scalar(select(InterviewAnswer).where(InterviewAnswer.session_id == session_id))
            assert answer.status == "FAILED"
            current = await db.get(InterviewSessionQuestion, request.question_id)
            assert current.status == "ANSWERED"
            assert await db.scalar(select(func.count(InterviewSessionQuestion.id)).where(InterviewSessionQuestion.session_id == session_id)) == 1
            if case == "hard_fail":
                step = await db.scalar(select(ExperienceAgentStepRun).join(InterviewAgentCall, InterviewAgentCall.agent_run_id == ExperienceAgentStepRun.agent_run_id)
                    .where(InterviewAgentCall.session_id == session_id, ExperienceAgentStepRun.status == "FAILED"))
                assert step.structured_retry == 1 and step.error_message == "structured_output_failed"
        # Explicit retry reuses the same persisted answer and question.
        restored = await core.detail(session_id, user_id)
        assert restored["pending_answer"]["idempotency_key"] == request.idempotency_key
        assert restored["pending_answer"]["content"] == request.content
        evaluator.responses[:] = [evaluation()]
        _, ctx, qs, _, _ = await core.read_state(session_id, user_id)
        main.responses[:] = [question(ctx, question_policy(ctx, qs))]
        await core.answer(session_id, user_id, request.question_id, request.idempotency_key, request.content)
        async with factory() as db:
            assert await db.scalar(select(func.count(InterviewAnswer.id)).where(InterviewAnswer.session_id == session_id)) == 1
    asyncio.run(run_with_database(database_url, scenario))


@pytest.mark.parametrize("limit", ["followup", "rounds", "time", "tokens", "agent_complete"])
def test_deterministic_budget_boundaries(limit):
    q = SimpleNamespace(id=1, parent_question_id=None, question_kind="NEXT")
    child = SimpleNamespace(id=2, parent_question_id=1, question_kind="FOLLOW_UP")
    context = {"budgets": dict(question_count=10, max_follow_ups=1, max_rounds=10, max_duration_seconds=100, max_model_tokens=100)}
    action = SessionController.choose_action(context, [q, child] if limit == "followup" else [q], child if limit == "followup" else q,
        "FOLLOW_UP" if limit == "followup" else "COMPLETE", rounds=10 if limit == "rounds" else 1,
        tokens=100 if limit == "tokens" else 0, started_at=datetime.now(timezone.utc) - timedelta(seconds=200 if limit == "time" else 0))
    assert action == ("COMPLETE" if limit in ("rounds", "time", "tokens") else "NEXT")


@pytest.mark.parametrize("budget", ["time", "tokens"])
def test_pre_call_budget_stops_without_fake_evaluation(database_url, budget):
    async def scenario(factory):
        core, main, evaluator, uid, detail = await seed(factory)
        sid = detail["id"]
        first = await queue_start(core, main, uid, sid)
        async with factory() as db:
            session = await db.get(InterviewSession, sid)
            if budget == "time":
                session.started_at = datetime.now(timezone.utc) - timedelta(seconds=10000)
            else:
                # Conservative reservations bound failed/retried live calls too.
                await SessionController(db).reserve_model_call(sid, uid, session.state_version,
                    detail["context_snapshot"]["budgets"]["max_model_tokens"])
            await db.commit()
        events = await core.answer(sid, uid, first["question_id"], uuid4().hex, "Replay answer after budget expiry")
        assert events[0][0] == "complete" and evaluator.calls == 0
        final = await core.detail(sid, uid)
        assert final["status"] == "COMPLETED" and final["completion_json"]["evaluated_count"] == 0
        assert final["completion_json"]["overall_average"] is None
        assert all(q["status"] == "SKIPPED" for q in final["questions"])
    asyncio.run(run_with_database(database_url, scenario))


def test_same_answer_concurrent_retry_optimistic_conflict(database_url):
    async def scenario(factory):
        core, main, evaluator, uid, detail = await seed(factory, budget=2)
        sid = detail["id"]
        await queue_start(core, main, uid, sid)
        request = await queue_answer(core, main, evaluator, uid, sid)
        evaluator.responses.append(evaluation())
        entered, release = asyncio.Event(), asyncio.Event()
        original = core.evaluator.call
        first = True
        async def delayed(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                entered.set()
                await release.wait()
            return await original(*args, **kwargs)
        core.evaluator.call = delayed
        args = (sid, uid, request.question_id, request.idempotency_key, request.content)
        task = asyncio.create_task(core.answer(*args))
        await asyncio.wait_for(entered.wait(), 3)
        # A second request can read/commit while the first model is waiting.
        events = await asyncio.wait_for(core.answer(*args), 5)
        assert events[0][0] == "evaluation"
        release.set()
        with pytest.raises(AppError) as exc: await task
        assert exc.value.code == "INTERVIEW_CONFLICT"
        async with factory() as db:
            assert await db.scalar(select(func.count(InterviewAnswer.id)).where(InterviewAnswer.session_id == sid)) == 1
            assert await db.scalar(select(func.count(InterviewEvaluation.id)).where(InterviewEvaluation.session_id == sid)) == 1
    asyncio.run(run_with_database(database_url, scenario))


def test_sse_emits_only_committed_results_and_failure(database_url):
    async def scenario(factory):
        core, main, evaluator, uid, detail = await seed(factory, budget=2)
        sid = detail["id"]
        await queue_start(core, main, uid, sid)
        request = await queue_answer(core, main, evaluator, uid, sid)
        stream = core.stream(sid, uid, request)
        assert (await anext(stream)).startswith("event: status")
        assert (await anext(stream)).startswith("event: evaluation")
        async with factory() as db:
            assert await db.scalar(select(func.count(InterviewEvaluation.id)).where(InterviewEvaluation.session_id == sid)) == 1
            answer = await db.scalar(select(InterviewAnswer).where(InterviewAnswer.session_id == sid))
            assert answer.status == "COMPLETED" and answer.result_events
            assert (await db.get(InterviewSessionQuestion, request.question_id)).status == "COMPLETED"
        rest = [event async for event in stream]
        assert rest[-1].startswith("event: done")
        request = await queue_answer(core, main, evaluator, uid, sid)
        evaluator.responses[:] = ["bad", "bad"]
        failed = [event async for event in core.stream(sid, uid, request)]
        assert [event.splitlines()[0] for event in failed] == ["event: status", "event: error"]
        async with factory() as db:
            assert (await db.get(InterviewSessionQuestion, request.question_id)).status == "ANSWERED"
            assert (await db.get(InterviewSession, sid)).status == "IN_PROGRESS"
    asyncio.run(run_with_database(database_url, scenario))


def test_api_sse_permissions_and_legacy_compatibility(database_url):
    async def scenario(factory):
        from interview_api.main import app
        from interview_api.api.deps import get_current_user
        from interview_api.infrastructure.db.session import get_db
        from interview_api.core.rate_limit import interview_chat_limit
        from interview_api.modules.interview import router as module
        from interview_api.modules.users.models import User
        core, main, evaluator, uid, detail = await seed(factory, budget=2)
        sid, actor = detail["id"], uid
        async def db_override():
            async with factory() as db:
                yield db
        async def user_override():
            async with factory() as db: return await db.get(User, actor)
        async def no_limit(): pass
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[get_current_user] = user_override
        app.dependency_overrides[interview_chat_limit] = no_limit
        try:
            with patch.object(module, "build_core", return_value=core):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    assert (await client.post("/api/v1/interview/sessions", json={})).status_code == 422
                    assert (await client.get("/api/v1/interview/targets")).status_code == 200
                    await queue_start(core, main, uid, sid)
                    assert (await client.post(f"/api/v1/interview/sessions/{sid}/start")).status_code == 200
                    request = await queue_answer(core, main, evaluator, uid, sid, "FOLLOW_UP")
                    response = await client.post(f"/api/v1/interview/sessions/{sid}/messages/stream", json=request.model_dump())
                    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
                    assert [line for line in response.text.splitlines() if line.startswith("event:")] == ["event: status", "event: evaluation", "event: follow_up", "event: done"]
                    assert (await client.post(f"/api/v1/interview/sessions/{sid}/messages/stream", json=request.model_dump())).text == response.text
                    request = await queue_answer(core, main, evaluator, uid, sid)
                    response = await client.post(f"/api/v1/interview/sessions/{sid}/messages/stream", json=request.model_dump())
                    assert "event: complete" in response.text
                    restored = (await client.get(f"/api/v1/interview/sessions/{sid}")).json()["data"]
                    assert restored["status"] == "COMPLETED" and restored["completion_json"]["mode"] == "replay"
                    assert (await client.post(f"/api/v1/interview/sessions/{sid}/resume", json={"resume_id": 1})).status_code == 409
                    other, _ = await seed_resume(factory)
                    actor = other
                    assert (await client.get(f"/api/v1/interview/sessions/{sid}")).status_code == 404
                    actor = uid
                    # Session deletion can cascade its private records without changing Profile/JD.
                    removed = await client.delete(f"/api/v1/interview/sessions/{sid}")
                    assert removed.status_code == 200, removed.text
        finally:
            app.dependency_overrides.clear()
    asyncio.run(run_with_database(database_url, scenario))


@pytest.mark.parametrize("case", ["company_job", "foreign_profile", "foreign_jd", "no_requirements", "inactive_company"])
def test_creation_rejects_unreliable_context(database_url, case):
    async def scenario(factory):
        core, _, _, uid, detail = await seed(factory)
        ctx = detail["context_snapshot"]
        request = CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"], candidate_profile_version_id=ctx["candidate_profile_version_id"])
        if case == "company_job":
            async with factory() as db:
                other = Company(canonical_name="Other fixture " + uuid4().hex)
                db.add(other); await db.commit()
                request.company_id = other.id
        elif case == "foreign_profile":
            _, _, _, _, foreign = await seed(factory)
            request.candidate_profile_version_id = foreign["context_snapshot"]["candidate_profile_version_id"]
        elif case == "foreign_jd":
            _, _, _, _, foreign = await seed(factory)
            request.job_description_version_id = foreign["context_snapshot"]["job_description_version_id"]
        elif case == "no_requirements":
            async with factory() as db:
                db.add(JobDescriptionVersion(job_id=ctx["job_id"], version_number=3, raw_text="New published fixture JD without Requirements", publication_status="PUBLISHED"))
                await db.commit()
        elif case == "inactive_company":
            async with factory() as db:
                await db.execute(update(Company).where(Company.id == ctx["company_id"]).values(status="INACTIVE"))
                await db.commit()
        async with factory() as db:
            before = await db.scalar(select(func.count(InterviewSession.id)).where(InterviewSession.user_id == uid))
        with pytest.raises(AppError): await core.create(uid, request)
        async with factory() as db:
            assert await db.scalar(select(func.count(InterviewSession.id)).where(InterviewSession.user_id == uid)) == before
    asyncio.run(run_with_database(database_url, scenario))


@pytest.mark.parametrize("entity", [InterviewQuestion, InterviewExperience, JobRequirement])
def test_snapshot_protects_selected_public_refs_before_first_question(database_url, entity):
    async def scenario(factory):
        _, _, _, _, detail = await seed(factory)
        key = {InterviewQuestion: "public_questions", InterviewExperience: "interview_experiences", JobRequirement: "requirements"}[entity]
        entity_id = detail["context_snapshot"][key][0]["id"]
        async with factory() as db:
            with pytest.raises(DBAPIError):
                await db.execute(delete(entity).where(entity.id == entity_id))
            await db.rollback()
    asyncio.run(run_with_database(database_url, scenario))
