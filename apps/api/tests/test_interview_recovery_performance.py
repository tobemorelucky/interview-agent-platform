"""Offline cancellation/retry and exact frozen-input reuse probes; no live models."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from interview_api.core.exceptions import AppError
from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation, InterviewSession, InterviewAgentCall
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.interview.controller import SessionController
from interview_api.modules.interview.schemas import SendMessageRequest
from test_m4_interview import seed, queue_start, queue_answer, run_with_database


@pytest.mark.parametrize('phase', ['evaluation', 'next_question'])
def test_cancelled_request_has_failed_audit_and_same_answer_safe_retry(database_url, phase):
    async def scenario(factory):
        core, main, evaluator, uid, detail = await seed(factory, budget=4)
        sid = detail['id']
        await queue_start(core, main, uid, sid)
        req = await queue_answer(core, main, evaluator, uid, sid)
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        target = evaluator if phase == 'evaluation' else main
        with patch.object(target, 'generate', blocked):
            task = asyncio.create_task(core.answer(sid, uid, req.question_id, req.idempotency_key, req.content))
            await asyncio.wait_for(entered.wait(), 5)
            live = await core.detail(sid, uid)
            assert live['pending_answer']['retryable'] is False
            with pytest.raises(AppError) as exc:
                await core.answer(sid, uid, req.question_id, req.idempotency_key, req.content)
            assert exc.value.code == 'INTERVIEW_ANSWER_RUNNING'
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        failed = await core.detail(sid, uid)
        assert failed['pending_answer']['status'] == 'FAILED'
        assert failed['pending_answer']['retryable'] is True
        assert failed['active_question_id'] == req.question_id and failed['completion_json'] is None
        assert failed['interview_plan_json']['reserved_model_tokens'] == 0
        if phase == 'next_question':
            from test_m4_interview import evaluation
            evaluator.responses.append(evaluation())
        events = await core.answer(sid, uid, req.question_id, req.idempotency_key, req.content)
        assert events[0][0] == 'evaluation' and events[1][0] == 'question'
        assert events == await core.answer(sid, uid, req.question_id, req.idempotency_key, req.content)
        async with factory() as db:
            assert await db.scalar(select(func.count(InterviewAnswer.id)).where(InterviewAnswer.session_id == sid)) == 1
            assert await db.scalar(select(func.count(InterviewEvaluation.id)).where(InterviewEvaluation.session_id == sid)) == 1
            runs = (await db.scalars(select(ExperienceAgentRun).join(InterviewAgentCall,
                InterviewAgentCall.agent_run_id == ExperienceAgentRun.id).where(InterviewAgentCall.session_id == sid))).all()
            cancelled = [r for r in runs if r.error_message == 'interview_request_cancelled']
            assert len(cancelled) == 1 and cancelled[0].status == 'FAILED'
            step = await db.scalar(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == cancelled[0].id))
            assert step.status == 'FAILED'
        for _ in range(2):
            req = await queue_answer(core, main, evaluator, uid, sid)
            await core.answer(sid, uid, req.question_id, req.idempotency_key, req.content)
        assert (await core.detail(sid, uid))['status'] == 'IN_PROGRESS'
    asyncio.run(run_with_database(database_url, scenario))


def test_expired_processing_lease_fences_old_answer_without_duplication(database_url):
    async def scenario(factory):
        core, main, evaluator, uid, detail = await seed(factory, budget=4)
        sid = detail['id']; await queue_start(core, main, uid, sid)
        req = await queue_answer(core, main, evaluator, uid, sid)
        async with factory() as db:
            answer, old_version, _ = await SessionController(db).prepare_answer(sid, uid, req.question_id, req.idempotency_key, req.content)
            session = await db.get(InterviewSession, sid)
            session.interview_plan_json = {**session.interview_plan_json, 'model_call_reservations': {'vanished': 20}, 'reserved_model_tokens': 20,
                'answer_processing': {'answer_id': answer.id, 'expires_at': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}}
            await db.commit(); aid = answer.id
        assert (await core.detail(sid, uid))['pending_answer']['retryable']
        with pytest.raises(AppError) as refused:
            await core.answer(sid, uid, req.question_id, req.idempotency_key, req.content)
        assert refused.value.code == 'INTERVIEW_OUTCOME_UNKNOWN'
        # A vanished attempt keeps its conservative hold until the user accepts
        # one resend explicitly; only then is the previous reservation released.
        await core.answer(sid, uid, req.question_id, req.idempotency_key, req.content, allow_unknown_retry=True)
        final = await core.detail(sid, uid)
        assert final['pending_answer'] is None
        assert final['interview_plan_json']['unreported_model_tokens'] == 20
        async with factory() as db:
            await SessionController(db).failed_answer(sid, uid, aid, old_version, 'old result')
            await db.commit()
            assert (await db.get(InterviewAnswer, aid)).status == 'COMPLETED'
            assert await db.scalar(select(func.count(InterviewAnswer.id)).where(InterviewAnswer.session_id == sid)) == 1
    asyncio.run(run_with_database(database_url, scenario))


def test_sse_disconnect_cleans_child_task_before_return(database_url):
    async def scenario(factory):
        core, main, evaluator, uid, detail = await seed(factory)
        sid = detail['id']; await queue_start(core, main, uid, sid)
        req = await queue_answer(core, main, evaluator, uid, sid)
        async def blocked(*args, **kwargs): await asyncio.Event().wait()
        with patch.object(evaluator, 'generate', blocked):
            stream = core.stream(sid, uid, req)
            assert 'evaluating' in await anext(stream)
            assert 'status' in await anext(stream)
            await stream.aclose()
        final = await core.detail(sid, uid)
        assert final['pending_answer']['status'] == 'FAILED'
        assert final['status'] == 'IN_PROGRESS'
    asyncio.run(run_with_database(database_url, scenario))


def test_evaluation_reasoning_policy_is_route_only():
    from interview_api.infrastructure.models.router import build_interview_router, build_resume_profile_router, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, RESUME_PROFILE_ROUTE
    router = build_interview_router()
    assert router.primary(ANSWER_EVALUATION_ROUTE).thinking_type == 'disabled'
    assert router.primary(INTERVIEW_MAIN_ROUTE).thinking_type == 'disabled'
    assert build_resume_profile_router().primary(RESUME_PROFILE_ROUTE).thinking_type == 'disabled'


def test_single_focus_and_followup_deepdive_prompt_contract_keeps_history():
    from interview_api.modules.interview.agents import InterviewPromptRegistry
    from interview_api.infrastructure.models.router import INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
    registry = InterviewPromptRegistry()
    assert 'only ONE focal gap' in registry.get(INTERVIEW_MAIN_ROUTE).system
    assert 'those' in registry.get(ANSWER_EVALUATION_ROUTE).system.lower()
    assert 'recommend DEEP_DIVE' in registry.get(ANSWER_EVALUATION_ROUTE).system
    for version in ('1.0.0', '1.0.1', '1.0.2', '1.0.3', '1.0.4'):
        assert registry.get(INTERVIEW_MAIN_ROUTE, version).version == version
    for version in ('1.0.0', '1.0.1'):
        assert registry.get(ANSWER_EVALUATION_ROUTE, version).version == version


def test_matching_projection_preserves_all_evidence_and_exact_reuse_identity():
    from interview_api.modules.job_matching.context import model_context, reuse_identity
    value = dict(request={'request_key': 'one', 'filters': {}}, profile={'facts': [{'id': 1}],
        'evidence': [{'key': 'real', 'kind': 'RESUME', 'excerpt': 'actual', 'start_offset': 10}]},
        approved_preferences={'items': [{'id': 1, 'content': 'confirmed'}]},
        candidates=[{'jd_id': 2, 'status': 'ACTIVE',
                     'evidence_matrix': [{'id': 'alignment:3',
                                          'supports': [{'fact_ref': 'fact:1', 'evidence_ids': ['real']}]}]}])
    pinned = deepcopy(value); projected = model_context(value)
    assert value == pinned and projected['profile']['facts'] == value['profile']['facts']
    assert projected['profile']['evidence'][0]['excerpt'] == 'actual'
    other = deepcopy(value); other['request']['request_key'] = 'two'
    assert reuse_identity(value) == reuse_identity(other)
    other['request']['candidate_profile_version_id'] = 1
    assert reuse_identity(value) == reuse_identity(other)
    for field in ('jd_id', 'status'):
        altered = deepcopy(other); altered['candidates'][0][field] = 'changed'
        assert reuse_identity(value) != reuse_identity(altered)
    other['approved_preferences']['items'][0]['content'] = 'updated'
    assert reuse_identity(value) != reuse_identity(other)


def test_matching_reuses_only_identical_owned_frozen_inputs(database_url):
    from test_m6_job_matching import seed_matching, artifact
    from interview_api.modules.job_matching.schemas import MatchRequest
    async def scenario(factory):
        core, provider, uid, jobs, preference, req, created = await seed_matching(factory)
        provider.responses.append(artifact(created))
        first = await core.generate(uid, created['id']); calls = provider.calls
        second = await core.create(uid, MatchRequest(request_key=uuid4().hex, filters=req.filters))
        reused = await core.generate(uid, second['id'])
        assert reused['status'] == 'COMPLETED' and provider.calls == calls
        assert reused['coverage']['reused_from_run_id'] == first['id'] and reused['attempts'] == []
        assert [c['recommendation']['analysis'] for c in reused['candidates']] == [c['recommendation']['analysis'] for c in first['candidates']]
        third_req = req.model_copy(update={'request_key': uuid4().hex, 'filters': req.filters.model_copy(update={'tags': ['Python']})})
        third = await core.create(uid, third_req)
        provider.responses.append(artifact(third))
        final = await core.generate(uid, third['id'])
        assert final['status'] == 'COMPLETED' and provider.calls == calls + 1
    asyncio.run(run_with_database(database_url, scenario))
