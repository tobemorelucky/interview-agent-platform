"""Product presentation stays separate from facts, grading and pinned provenance."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from interview_api.modules.interview.artifacts import EvaluationArtifact, ReferenceAnswer
from interview_api.modules.interview.context import model_context, question_policy
from interview_api.modules.interview_report.statistics import interview_records
from test_m4_interview import seed, queue_start, evaluation, question, run_with_database


def reference():
    return dict(answer_framework=['目标', '实现', '异常处理'],
        interview_answer='这是供练习的工程方案，不冒充真实经历。' * 12,
        bonus_points=['解释一致性取舍'], possible_follow_ups=['失败如何重试？', '如何验证效果？'])


def test_reference_schema_never_replaces_score_or_facts():
    artifact = EvaluationArtifact.model_validate(dict(evaluation(), reference_answer=reference()))
    assert artifact.technical_correctness == 4
    assert artifact.reference_answer.answer_framework == ['目标', '实现', '异常处理']
    with pytest.raises(Exception):
        ReferenceAnswer.model_validate({**reference(), 'candidate_fact_ids': [99]})


def test_reference_persistence_hidden_before_evaluation_and_report_reuses_it(database_url):
    from interview_api.modules.interview.router import get_question_detail
    async def scenario(factory):
        core, main, evaluator, uid, detail = await seed(factory, budget=4)
        sid=detail['id']; first=await queue_start(core, main, uid, sid)
        async with factory() as db:
            with pytest.raises(HTTPException) as exc:
                await get_question_detail(sid, first['question_id'], SimpleNamespace(id=uid), db)
            assert exc.value.status_code == 409
        session, context, questions, active, _ = await core.read_state(sid,uid)
        pinned=deepcopy(context)
        evaluator.responses.append(dict(evaluation(),reference_answer=reference()))
        main.responses.append(question(context,question_policy(context,questions)))
        await core.answer(sid,uid,active.id,uuid4().hex,'Replay fixture: 缓存更新与失效')
        async with factory() as db:
            data=await get_question_detail(sid,first['question_id'],SimpleNamespace(id=uid),db)
            assert data['data']['reference_answer'] == reference()
            _, records=await interview_records(db,sid)
            assert records[0]['reference_answer'] == reference()
        assert (await core.detail(sid,uid))['context_snapshot'] == pinned
    asyncio.run(run_with_database(database_url,scenario))


def test_model_projection_removes_duplicate_prose_but_preserves_provenance(database_url):
    async def scenario(factory):
        core, main, evaluator, uid, detail=await seed(factory)
        context=detail['context_snapshot']; before=deepcopy(context)
        policy=question_policy(context,[])
        projected=model_context(context,policy)
        assert [e['key'] for e in projected['candidate_evidence']] == [e['key'] for e in context['candidate_evidence']]
        assert [e['excerpt'] for e in projected['candidate_evidence']] == [e['excerpt'] for e in context['candidate_evidence']]
        assert all('statement' not in f for f in projected['candidate_facts'])
        assert [f['evidence_ids'] for f in projected['candidate_facts']] == [f['evidence_ids'] for f in context['candidate_facts']]
        assert len(projected['public_questions']) == 1
        assert context == before
    asyncio.run(run_with_database(database_url,scenario))


def test_live_routes_inherit_unified_model_and_low_latency_policy(monkeypatch):
    from interview_api.core.config import settings
    from interview_api.infrastructure.models import router
    monkeypatch.setattr(settings,'llm_model','doubao-seed-2-1-turbo-260628')
    for field in ('public_research_extract_model','public_research_quality_model','resume_profile_model',
                  'interview_main_model','answer_evaluation_model','interview_coach_model','job_matching_model'):
        monkeypatch.setattr(settings,field,'')
    for builder in (router.build_model_router,router.build_resume_profile_router,router.build_interview_router,
                    router.build_interview_coach_router,router.build_job_matching_router):
        for provider in builder().routes.values():
            assert provider.model_name == 'doubao-seed-2-1-turbo-260628'
            assert provider.thinking_type == 'disabled'


def test_main_controller_override_is_rejected_and_audited(database_url):
    from sqlalchemy import select
    from interview_api.core.exceptions import AppError
    from interview_api.modules.experience.models import ExperienceAgentRun
    from interview_api.modules.interview.agents import InterviewPromptRegistry
    async def scenario(factory):
        core, provider, evaluator, uid, detail = await seed(factory)
        payload = core.payload(detail['context_snapshot'], [], None, 'NEXT',
                               question_policy(detail['context_snapshot'], []))
        provider.responses.append({'recommendation': 'COMPLETE', 'question': None})
        with pytest.raises(AppError) as exc:
            await core.main.call(detail['id'], payload)
        assert exc.value.code == 'INVALID_MAIN_ARTIFACT'
        async with factory() as db:
            run = await db.scalar(select(ExperienceAgentRun).where(
                ExperienceAgentRun.agent_role == 'InterviewMainAgent').order_by(ExperienceAgentRun.id.desc()))
            assert run.status == 'FAILED'
            assert run.output_json['validation'] == {
                'requested_action': 'NEXT', 'received_recommendation': 'COMPLETE', 'question_present': False}
        assert (await core.detail(detail['id'], uid))['status'] != 'COMPLETED'
        invalid_source = question(detail['context_snapshot'], payload['preferred_source'])
        invalid_source['question']['source_type'] = 'FOLLOW_UP'
        provider.responses.append(invalid_source)
        with pytest.raises(AppError) as source_exc:
            await core.main.call(detail['id'], payload)
        assert source_exc.value.code == 'QUESTION_SOURCE_POLICY'
        async with factory() as db:
            source_run = await db.scalar(select(ExperienceAgentRun).where(
                ExperienceAgentRun.agent_role == 'InterviewMainAgent').order_by(ExperienceAgentRun.id.desc()))
            assert source_run.output_json['validation'] == {
                'requested_action': 'NEXT', 'expected_source_type': 'PUBLIC_QUESTION',
                'received_source_type': 'FOLLOW_UP'}
    assert 'exact copy of top-level requested_action' in InterviewPromptRegistry().get('interview_main').system
    assert 'DEEP_DIVE uses DYNAMIC_GENERATED, NOT FOLLOW_UP' in InterviewPromptRegistry().get('interview_main').system
    asyncio.run(run_with_database(database_url, scenario))


def test_answer_follow_up_inherits_parent_provenance_without_model_reselection(database_url):
    from interview_api.core.exceptions import AppError
    from interview_api.modules.interview.agents import InterviewPromptRegistry
    from interview_api.modules.interview.artifacts import MainArtifact
    async def scenario(factory):
        core, main, evaluator, uid, detail = await seed(factory, budget=3)
        sid = detail['id']; context = detail['context_snapshot']
        fact = context['candidate_facts'][0]
        first_artifact = question(context, question_policy(context, []))
        first_artifact['question'].update(candidate_fact_ids=[fact['id']], candidate_evidence_ids=fact['evidence_ids'])
        main.responses.append(first_artifact)
        await core.start(sid, uid)
        _, context, questions, active, _ = await core.read_state(sid, uid)
        parent_refs = deepcopy(active.candidate_refs_json)
        policy = question_policy(context, questions, 'FOLLOW_UP')
        payload = core.payload(context, questions, active, 'FOLLOW_UP', policy, answer='Replay fixture answer')
        invalid = question(context, policy, 'FOLLOW_UP')
        invalid['question'].update(candidate_fact_ids=[fact['id']], candidate_evidence_ids=[])
        with pytest.raises(AppError) as exc:
            core.main.validate_artifact(MainArtifact.model_validate(invalid), payload)
        assert exc.value.code == 'UNSUPPORTED_CANDIDATE_QUESTION'
        evaluator.responses.append(evaluation('FOLLOW_UP'))
        main.responses.append(question(context, policy, 'FOLLOW_UP'))
        await core.answer(sid, uid, active.id, uuid4().hex, 'Replay fixture answer')
        _, _, _, child, _ = await core.read_state(sid, uid)
        assert child.parent_question_id == active.id
        assert child.candidate_refs_json['fact_ids'] == parent_refs['fact_ids']
        assert child.candidate_refs_json['evidence_ids'] == parent_refs['evidence_ids']
    registry = InterviewPromptRegistry()
    assert 'candidate_fact_ids and candidate_evidence_ids MUST be empty arrays' in registry.get('interview_main').system
    assert registry.get('interview_main', '1.0.10').version == '1.0.10'
    asyncio.run(run_with_database(database_url, scenario))
