"""Offline model-boundary tests: no provider network, no manufactured accuracy claim."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import pytest

from interview_api.infrastructure.models.provider import ModelResponse, ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.infrastructure.models.structured import generate_structured, StructuredOutputError
from interview_api.modules.interview.adaptive import AdaptiveEvaluation, resolve_observations


def example():
    answer = '我按刚才提示，用订单ID唯一约束并在同一事务写订单及去重记录，提交成功再ACK。提交后ACK前崩溃则重复投递，由唯一约束阻止重复落库；提交前失败回滚重试。'
    quotes = ['用订单ID唯一约束并在同一事务写订单及去重记录', '提交成功再ACK',
              '提交后ACK前崩溃则重复投递，由唯一约束阻止重复落库', '提交前失败回滚重试']
    value = dict(technical_correctness=4, coverage=4, depth=4, relevance=5, communication=4,
        evaluation='离线引用契约样本，不作为评分准确性金标准。', covered_points=['事务与幂等'],
        missing_points=['具体实现'], factual_or_reasoning_issues=[], follow_up_focus=['具体实现'],
        next_action_recommendation='FOLLOW_UP', knowledge_observations=[dict(
            knowledge_id='java_backend:kafka_reliability', state='PARTIALLY_DEMONSTRATED',
            answer_quotes=quotes, missing_mechanisms=['具体实现'], resolved_gap_ids=['gap_original'])])
    payload = dict(answer=answer, adaptive=dict(active_knowledge_id='java_backend:kafka_reliability',
        knowledge_state=dict(items={'java_backend:kafka_reliability': dict(open_gaps=[dict(id='gap_original', text='事务边界')])})))
    return payload, value


def run(outputs, payload=None):
    from interview_api.modules.interview.evaluation_repair import EvaluationQuotesRepair
    payload = payload or example()[0]
    provider = ReplayModelProvider(outputs)
    return asyncio.run(generate_structured(ModelRouter({'answer_evaluation': provider}), 'answer_evaluation',
        system='Explicit offline Replay', payload=payload, response_model=AdaptiveEvaluation,
        repair_policy=EvaluationQuotesRepair(payload)))


def test_four_literal_quotes_can_be_repaired_only_by_selection():
    payload, original = example()
    repaired = deepcopy(original)
    repaired['knowledge_observations'][0]['answer_quotes'].pop(1)
    result = run([original, repaired], payload)
    assert result.parsed.model_dump(exclude_unset=True) == repaired
    assert result.structured_retry == 1
    assert resolve_observations(result.parsed, payload)[0]['resolved_gap_ids'] == ['gap_original']


@pytest.mark.parametrize('count', [0, 1, 3, 4, 7])
def test_quote_schema_boundaries_are_not_silently_truncated(count):
    payload, value = example()
    value['knowledge_observations'][0]['answer_quotes'] = ['提交成功再ACK'] * count
    if count <= 3:
        result = run([value], payload)
        assert result.structured_retry == 0
        if count == 0:
            with pytest.raises(Exception, match='正向表现观察'):
                resolve_observations(result.parsed, payload)
    else:
        with pytest.raises(StructuredOutputError):
            run([value, value], payload)


@pytest.mark.parametrize('change', ['score', 'score_type', 'state', 'gap', 'reason', 'rewrite', 'unknown', 'extra', 'empty'])
def test_quote_repair_cannot_change_other_semantics(change):
    payload, original = example()
    fixed = deepcopy(original)
    fixed['knowledge_observations'][0]['answer_quotes'].pop(1)
    if change == 'score': fixed['coverage'] = 5
    if change == 'score_type': fixed['coverage'] = 4.0
    if change == 'state': fixed['knowledge_observations'][0]['state'] = 'NEEDS_CLARIFICATION'
    if change == 'gap': fixed['knowledge_observations'][0]['resolved_gap_ids'] = []
    if change == 'reason': fixed['evaluation'] = 'changed'
    if change == 'rewrite': fixed['knowledge_observations'][0]['answer_quotes'][0] += '，提交成功再ACK'
    if change == 'unknown': original['knowledge_observations'][0]['answer_quotes'][-1] = '不存在的原文'
    if change == 'extra': fixed['extra'] = 'not allowed'
    if change == 'empty': fixed['knowledge_observations'][0]['answer_quotes'] = []
    with pytest.raises(StructuredOutputError): run([original, fixed], payload)


def test_oversized_array_cannot_hide_an_invalid_quote_item():
    payload, original = example()
    payload['answer'] += 'x' * 401
    original['knowledge_observations'][0]['answer_quotes'][1] = 'x' * 401
    fixed = deepcopy(original)
    fixed['knowledge_observations'][0]['answer_quotes'].pop(1)
    with pytest.raises(StructuredOutputError): run([original, fixed], payload)


def test_legal_count_does_not_make_a_fabricated_quote_supported():
    from interview_api.core.exceptions import AppError
    payload, original = example()
    original['knowledge_observations'][0]['answer_quotes'] = ['数据库自动替我完成原子提交']
    result = run([original], payload)
    assert result.structured_retry == 0
    with pytest.raises(AppError) as caught: resolve_observations(result.parsed, payload)
    assert caught.value.code == 'UNSUPPORTED_ANSWER_QUOTE'


def test_retained_live_e07_full_output_when_local_evidence_is_available():
    path = Path(__file__).resolve().parents[3] / '.runtime/acceptance/v232_live_20261028_a.json'
    if not path.exists(): pytest.skip('Ignored local Live evidence is not shipped in the repository')
    http = json.loads(path.read_text(encoding='utf-8'))['http']
    old, new = http[6]['business_output'], http[7]['business_output']
    assert old != new
    assert [old[k] for k in ('technical_correctness','coverage','depth','relevance','communication')] == [4,4,4,5,4]
    assert [new[k] for k in ('technical_correctness','coverage','depth','relevance','communication')] == [4,4,4,5,4]
    payload = json.loads(http[7]['request_messages'][1]['content'])['input']
    fixed = deepcopy(new)
    fixed['knowledge_observations'][0]['answer_quotes'].pop(1)  # Explicit MODEL Replay selection, never a business truncator.
    legacy = ReplayModelProvider([new, fixed])
    with pytest.raises(StructuredOutputError):
        asyncio.run(generate_structured(ModelRouter({'answer_evaluation':legacy}), 'answer_evaluation',
            system='Old generic fact-preserving repair contract',payload=payload,response_model=AdaptiveEvaluation))
    result = run([new, fixed], payload)
    assert result.parsed.model_dump(exclude_unset=True) == fixed
    assert result.invocations[-1]['reference_subset_repair']['original_count'] == 4


@pytest.mark.parametrize('interruption', ['preflight', 'unknown', 'cancelled'])
def test_post_response_interruption_keeps_known_usage_and_unknown_disposition(interruption):
    class Boundary(ReplayModelProvider):
        async def generate(self, messages, *, schema):
            if self.calls:
                error = asyncio.CancelledError() if interruption == 'cancelled' else RuntimeError('bounded stop')
                if interruption == 'preflight': error.request_sent = False
                raise error
            r = await super().generate(messages, schema=schema)
            return ModelResponse(r.text, dict(input_tokens=200, output_tokens=30, total_tokens=230))
    provider = Boundary([example()[1]])
    async def execute():
        with pytest.raises(BaseException) as caught:
            await generate_structured(ModelRouter({'answer_evaluation':provider}), 'answer_evaluation',
                system='offline', payload=example()[0], response_model=AdaptiveEvaluation)
        assert caught.value.usage['total_tokens'] == 230
        assert caught.value.usage_complete == (interruption == 'preflight')
        assert len(caught.value.invocations) == 2
    asyncio.run(execute())


@pytest.mark.parametrize('mode', ['repaired', 'still_invalid', 'tampered', 'unknown_repair', 'preflight_stop', 'cancelled'])
def test_real_postgres_quote_failure_usage_and_safe_retry(database_url, mode):
    """Real service/controller/PG/HTTP quota ledger; only external generation is Replay."""
    async def execute():
        from uuid import uuid4
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from interview_api.infrastructure.models.governance import governed_call
        from interview_api.infrastructure.models.governance_models import ModelAdmission
        from interview_api.modules.experience.models import ExperienceAgentStepRun
        from interview_api.modules.interview.models import InterviewAgentCall
        from interview_api.modules.interview.core_service import MockInterviewService
        from interview_api.modules.interview.schemas import CreateSessionRequest
        from v22_support import kafka_seed
        from v21_support import AdaptiveReplayProvider

        class MeteredReplay(AdaptiveReplayProvider):
            provider_name = 'offline_usage_replay_NOT_LIVE'
            governed, max_output_tokens, timeout = True, 3000, 30
            failed = False
            reservation = None
            async def generate(self, messages, *, schema):
                body = json.loads(messages[-1]['content'])
                repairing = 'invalid_output' in body
                if repairing and mode == 'preflight_stop':
                    self.failed = True
                    error = RuntimeError('offline preflight stop')
                    error.request_sent = False
                    raise error
                async def operation():
                    if repairing:
                        self.reservation = (await self.core.detail(self.sid, self.uid))['interview_plan_json']['reserved_model_tokens']
                        plan = body['reference_subset_repair']
                        assert plan['policy'] == 'EVALUATION_QUOTE_SUBSET_V1'
                        assert body['validation_errors'][0]['actual_count'] == 4
                        assert body['validation_errors'][0]['limit'] == 3
                        self.failed = True
                        if mode == 'unknown_repair': raise RuntimeError('offline transport outcome unknown')
                        if mode == 'cancelled': raise asyncio.CancelledError()
                        value = json.loads(body['invalid_output'])
                        if mode != 'still_invalid': value['knowledge_observations'][0]['answer_quotes'].pop(1)
                        if mode == 'tampered': value['technical_correctness'] = 1
                    else:
                        result = await super(MeteredReplay, self).generate(messages, schema=schema)
                        value = json.loads(result.text)
                        if schema['title'] == 'AdaptiveEvaluation' and not self.failed:
                            value['knowledge_observations'][0]['answer_quotes'] = ['事务提交成功再ACK', '订单ID唯一约束', '重复投递去重', '失败回滚重试']
                    return ModelResponse(json.dumps(value, ensure_ascii=False), dict(input_tokens=80, output_tokens=20, total_tokens=100))
                return await governed_call(self, messages, operation)

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = MeteredReplay()
            core = MockInterviewService(factory, ModelRouter({r:provider for r in ('interview_main','answer_evaluation')}))
            session = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'],job_id=ctx['job_id'],
                interviewer_skill_id='java_backend', adaptive_interview=True, question_count=4))
            provider.core, provider.sid, provider.uid = core, session['id'], uid
            q = await core.start(session['id'], uid)
            key = uuid4().hex
            args = (session['id'],uid,q['question_id'],key,'Kafka 事务提交成功再ACK，订单ID唯一约束，重复投递去重，失败回滚重试。')
            if mode != 'repaired':
                with pytest.raises(BaseException) as caught: await core.answer(*args)
                assert isinstance(caught.value, (StructuredOutputError, RuntimeError, asyncio.CancelledError))
                detail = await core.detail(session['id'], uid)
                assert len(detail['questions']) == 1 and detail['status'] == 'IN_PROGRESS'
                assert detail['pending_answer']['idempotency_key'] == key
                plan = detail['interview_plan_json']
                assert not plan['model_call_reservations'] and plan['reserved_model_tokens'] == 0
                async with factory() as db:
                    step = await db.scalar(select(ExperienceAgentStepRun).join(InterviewAgentCall,
                        InterviewAgentCall.agent_run_id == ExperienceAgentStepRun.agent_run_id).where(
                        InterviewAgentCall.session_id == session['id'],ExperienceAgentStepRun.step_name == 'answer_evaluation'))
                    assert step.status == 'FAILED'
                    assert step.token_usage_json['total_tokens'] == (200 if mode in ('still_invalid','tampered') else 100)
                    rows = list((await db.scalars(select(ModelAdmission).where(ModelAdmission.owner_key == f'user:{uid}'))).all())
                    assert all(r.status != 'RESERVED' for r in rows)
                    assert sum(r.actual_tokens or 0 for r in rows) == (300 if mode in ('still_invalid','tampered') else 200)
                    if mode in ('unknown_repair','cancelled'):
                        assert sum(r.status == 'UNKNOWN' for r in rows) == 1
                        assert plan['unreported_model_tokens'] == provider.reservation - 100
                    else:
                        assert all(r.status == 'SETTLED' for r in rows)
                        assert plan['unreported_model_tokens'] == 0
            events = await core.answer(*args)
            detail = await core.detail(session['id'],uid)
            assert len(detail['questions']) == 2 and detail['pending_answer'] is None
            count = len(provider.calls)
            assert await core.answer(*args) == events
            assert len(provider.calls) == count
            assert (await core.detail(session['id'],uid))['context_snapshot'] == session['context_snapshot']
            async with factory() as db:
                rows = list((await db.scalars(select(ModelAdmission).where(ModelAdmission.owner_key == f'user:{uid}'))).all())
                assert all(r.status != 'RESERVED' for r in rows)
                settled = [r for r in rows if r.status == 'SETTLED']
                assert all(r.actual_tokens == r.charged_tokens == 100 for r in settled)
        finally:
            await engine.dispose()
    asyncio.run(execute())


def test_http_success_with_invalid_envelope_keeps_verifiable_usage():
    import httpx
    from interview_api.infrastructure.models.provider import CompatibleModelProvider, ModelProviderError
    async def execute():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}, 'choices': []}))
        async with httpx.AsyncClient(transport=transport) as client:
            p = CompatibleModelProvider(base_url='https://offline.invalid',api_key='not-a-secret',model='offline',
                timeout_seconds=1,max_output_tokens=10,client=client)
            with pytest.raises(ModelProviderError) as caught: await p.generate([],schema={})
            assert caught.value.usage['total_tokens'] == 15
    asyncio.run(execute())


@pytest.mark.parametrize('kind', ['http_failure', 'schema_failure', 'unknown_usage', 'preflight_rejection'])
def test_request_usage_is_not_invented_or_lost(kind):
    from interview_api.infrastructure.models.provider import ModelProviderError
    class Boundary(ReplayModelProvider):
        async def generate(self, messages, *, schema):
            if kind in ('http_failure', 'preflight_rejection'):
                error = ModelProviderError('model_request_failed', 'Sanitized offline error')
                if kind == 'preflight_rejection': error.request_sent = False
                raise error
            self.calls += 1
            return ModelResponse('{}', {'total_tokens':70} if kind == 'schema_failure' else {})
    provider = Boundary([])
    async def execute():
        with pytest.raises(ModelProviderError) as caught:
            await generate_structured(ModelRouter({'answer_evaluation':provider}), 'answer_evaluation',
                system='offline', payload={}, response_model=AdaptiveEvaluation)
        error = caught.value
        if kind == 'schema_failure':
            assert error.usage['total_tokens'] == 140 and error.usage_complete
        elif kind == 'preflight_rejection':
            assert error.usage == {'total_tokens':0} and error.usage_complete
        else:
            assert error.usage == {} and not error.usage_complete
    asyncio.run(execute())


@pytest.mark.parametrize('kind', ['invalid_envelope', 'schema_and_repair_failure', 'http_failure'])
def test_governed_http_usage_survives_invalid_response(database_url, kind):
    import httpx
    from uuid import uuid4
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from interview_api.infrastructure.models.governance import ModelGovernance
    from interview_api.infrastructure.models.scope import model_scope_for
    from interview_api.infrastructure.models.governance_models import ModelAdmission
    from interview_api.infrastructure.models.provider import CompatibleModelProvider, ModelProviderError
    async def execute():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        owner = 'offline-http-test:' + uuid4().hex
        def handler(request):
            if kind == 'http_failure': return httpx.Response(503, json={'error':{'code':'unavailable'}})
            return httpx.Response(200, json={'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15},
                'choices':[] if kind == 'invalid_envelope' else [{'message':{'content':'{}'},'finish_reason':'stop'}]})
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                provider = CompatibleModelProvider(base_url='https://offline.invalid',api_key='not-a-secret',model='offline',
                    timeout_seconds=2,max_output_tokens=20,client=client,governed=True)
                with model_scope_for(factory, owner=owner, route='answer_evaluation'):
                    with pytest.raises(ModelProviderError) as caught:
                        await generate_structured(ModelRouter({'answer_evaluation':provider}), 'answer_evaluation',
                            system='offline HTTP boundary',payload={},response_model=AdaptiveEvaluation)
            async with factory() as db:
                rows = list((await db.scalars(select(ModelAdmission).where(ModelAdmission.owner_key == owner))).all())
                if kind == 'http_failure':
                    assert len(rows) == 1 and rows[0].status == 'UNKNOWN'
                    assert rows[0].charged_tokens == rows[0].reserved_tokens
                    assert not caught.value.usage_complete
                else:
                    assert len(rows) == (2 if kind == 'schema_and_repair_failure' else 1)
                    assert all(r.status == 'SETTLED' and r.actual_tokens == r.charged_tokens == 15 for r in rows)
                    assert caught.value.usage['total_tokens'] == 15 * len(rows)
                    assert caught.value.usage_complete
                keys = [r.call_key for r in rows]
            for key in keys: await ModelGovernance(factory).settle(key, {'total_tokens':0})
            async with factory() as db:
                again = list((await db.scalars(select(ModelAdmission).where(ModelAdmission.owner_key == owner))).all())
                assert [(r.call_key,r.status,r.charged_tokens) for r in again] == [(r.call_key,r.status,r.charged_tokens) for r in rows]
        finally: await engine.dispose()
    asyncio.run(execute())
