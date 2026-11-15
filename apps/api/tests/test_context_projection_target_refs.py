"""Frozen-blueprint reference contract for the interview role projections.

A persisted question names its target in ``candidate_refs_json``. The projection
must resolve exactly that target inside the session's frozen blueprint. When it
cannot, the failure is a *business* contract mismatch: it must carry a code, must
not be turned into ``RuntimeError`` by PEP 479, must not be replaced by another
target, and must not trigger (or invite) a second model call.
"""
import asyncio
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.interview.adaptive import planning_payload
from interview_api.modules.interview.context_projection import (
    TERMINAL_CODES, frozen_by_id, frozen_target, model_payload,
)
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.skill_engine import agent_payload
from v21_support import AdaptiveReplayProvider
from v22_support import kafka_seed


def _evaluation_packet():
    from test_v211_director_contract import packet
    from interview_api.modules.interview.adaptive_prompts import freeze_prompts
    p = packet()
    policy = p['snapshot']['adaptive_policy']
    policy.update(context_projection='ROLE_CONTEXT_V1', evaluation_context='FOLLOWUP_SCORING_V1',
        prompts=freeze_prompts(context_projection=True, evaluation_context=True))
    kid = p['snapshot']['adaptive_blueprint']['targets'][0]['knowledge_id']
    p['adaptive']['active_knowledge_id'] = kid
    p['scoring_context'] = dict(version='FOLLOWUP_SCORING_V1', question_kind='NEXT', parent_question_id=None,
        parent_status='NOT_APPLICABLE', parent=None, assistance_status='UNKNOWN', open_gaps=[])
    p['current_question'] = dict(question_id=1, question='Replay 题干')
    p['answer'] = 'Replay 回答'
    return p, kid


def test_missing_active_target_is_a_business_error_not_a_runtime_error():
    p, _ = _evaluation_packet()
    p['adaptive']['active_knowledge_id'] = 'ghost:topic'
    with pytest.raises(AppError) as error:
        model_payload(agent_payload(p, 'answer_evaluation'), 'answer_evaluation')
    assert error.value.code == 'CONTEXT_TARGET_NOT_FOUND'
    assert error.value.status_code == 409
    assert 'ghost:topic' in error.value.message
    assert error.value.code in TERMINAL_CODES
    # The old behaviour surfaced as RuntimeError("coroutine raised StopIteration"),
    # which no client can classify and every client reads as retryable.
    assert type(error.value) is AppError
    assert not isinstance(error.value, RuntimeError)


def test_valid_target_is_projected_unchanged_and_never_substituted():
    p, kid = _evaluation_packet()
    out = model_payload(agent_payload(p, 'answer_evaluation'), 'answer_evaluation')
    assert out['adaptive']['target']['knowledge_id'] == kid
    # A missing target must not fall back to any other declared target.
    p['adaptive']['active_knowledge_id'] = 'ghost:topic'
    with pytest.raises(AppError):
        model_payload(agent_payload(p, 'answer_evaluation'), 'answer_evaluation')


def test_absent_active_id_is_refused_before_any_lookup():
    p, _ = _evaluation_packet()
    p['adaptive']['active_knowledge_id'] = None
    with pytest.raises(AppError) as error:
        model_payload(agent_payload(p, 'answer_evaluation'), 'answer_evaluation')
    assert error.value.code == 'CONTEXT_ACTIVE_TARGET_REQUIRED'
    assert error.value.code in TERMINAL_CODES


def test_target_without_sources_is_refused_instead_of_silently_empty():
    p, kid = _evaluation_packet()
    for target in p['snapshot']['adaptive_blueprint']['targets']:
        if target['knowledge_id'] == kid:
            target.pop('sources')
    with pytest.raises(AppError) as error:
        model_payload(agent_payload(p, 'answer_evaluation'), 'answer_evaluation')
    assert error.value.code == 'CONTEXT_TARGET_SOURCES_MISSING'


def test_candidate_target_outside_the_frozen_blueprint_is_refused():
    p, _ = _evaluation_packet()
    p['adaptive']['candidates'][0]['target_knowledge_id'] = 'ghost:candidate'
    with pytest.raises(AppError) as error:
        model_payload(agent_payload(p, 'interview_main'), 'interview_main')
    assert error.value.code == 'CONTEXT_CANDIDATE_TARGET_NOT_FOUND'
    assert error.value.code in TERMINAL_CODES


def test_frozen_lookup_declares_only_the_blueprint_targets():
    p, kid = _evaluation_packet()
    blueprint = p['snapshot']['adaptive_blueprint']
    assert set(frozen_by_id(blueprint)) == {t['knowledge_id'] for t in blueprint['targets']}
    assert frozen_target(blueprint, kid)['knowledge_id'] == kid
    with pytest.raises(AppError):
        frozen_target(blueprint, 'ghost:topic')


def test_missing_candidate_target_sends_no_model_request_and_does_not_advance(database_url):
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = AdaptiveReplayProvider()
            core = MockInterviewService(factory, ModelRouter(
                {r: provider for r in ('interview_main', 'answer_evaluation')}))
            session = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'],
                job_id=ctx['job_id'], interviewer_skill_id='java_backend',
                adaptive_interview=True, question_count=3))
            packet = planning_payload(session['context_snapshot'], [], None, None)
            packet['adaptive']['candidates'][0]['target_knowledge_id'] = 'ghost:candidate'
            with pytest.raises(AppError) as error:
                await core.main.adaptive(packet).call(session['id'], packet)
            assert error.value.code == 'CONTEXT_CANDIDATE_TARGET_NOT_FOUND'
            assert not provider.calls
            detail = await core.detail(session['id'], uid)
            assert detail['state_version'] == session['state_version'] and not detail['questions']
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_tampered_active_target_sends_no_model_request_for_evaluation(database_url):
    """The exact regression: question 1 of session 2 pointed at another session's target."""
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = AdaptiveReplayProvider()
            core = MockInterviewService(factory, ModelRouter(
                {r: provider for r in ('interview_main', 'answer_evaluation')}))
            session = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'],
                job_id=ctx['job_id'], interviewer_skill_id='java_backend',
                adaptive_interview=True, question_count=3))
            first = await core.start(session['id'], uid)
            # Only the orchestrator's own first question may have called the model.
            assert len(provider.calls) == 1
            question_id = first['question_id']
            from interview_api.modules.interview.models import InterviewSessionQuestion
            async with factory() as db:
                row = await db.get(InterviewSessionQuestion, question_id)
                refs = dict(row.candidate_refs_json)
                refs['adaptive_decision'] = dict(refs['adaptive_decision'], target_knowledge_id='ghost:topic')
                row.candidate_refs_json = refs
                await db.commit()
            _, context, questions, active, _ = await core.read_state(session['id'], uid)
            assert active.candidate_refs_json['adaptive_decision']['target_knowledge_id'] == 'ghost:topic'
            payload = planning_payload(context, questions, active, None,
                answer='Replay 回答', answer_id=None)
            async with factory() as db:
                from interview_api.modules.interview.controller import SessionController
                answer, expected, reused = await SessionController(db).prepare_answer(
                    session['id'], uid, question_id, 'k-ghost-target', 'Replay 回答')
                await db.commit()
                answer_id = answer.id
            payload = planning_payload(context, questions, active, None,
                answer='Replay 回答', answer_id=answer_id)
            calls_before = len(provider.calls)
            with pytest.raises(AppError) as error:
                await core.call_agent(core.evaluator.adaptive(), session['id'], uid, expected,
                    payload, answer_id=answer_id)
            assert error.value.code == 'CONTEXT_TARGET_NOT_FOUND'
            assert len(provider.calls) == calls_before
            async with factory() as db:
                assert await db.scalar(text(
                    "SELECT count(*) FROM interview_evaluations WHERE session_id = :s"),
                    {'s': session['id']}) == 0
            detail = await core.detail(session['id'], uid)
            assert detail['state_version'] == expected
        finally:
            await engine.dispose()
    asyncio.run(run())
