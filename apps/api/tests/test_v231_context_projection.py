"""Public Agent/model and interview service seams; real PostgreSQL, Replay only."""
import asyncio
import json
from copy import deepcopy
from uuid import uuid4
import pytest

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from v21_support import AdaptiveReplayProvider
from v22_support import kafka_seed


def test_scoring_context_is_a_new_frozen_evaluation_contract_not_a_main_change():
    from test_v211_director_contract import packet
    from interview_api.modules.interview.adaptive_prompts import freeze_prompts
    from interview_api.modules.interview.context_projection import model_payload
    from interview_api.modules.interview.skill_engine import agent_payload
    p = packet()
    policy = p['snapshot']['adaptive_policy']
    policy.update(context_projection='ROLE_CONTEXT_V1', evaluation_context='FOLLOWUP_SCORING_V1',
        prompts=freeze_prompts(context_projection=True, evaluation_context=True))
    kid = p['snapshot']['adaptive_blueprint']['targets'][0]['knowledge_id']
    p['adaptive']['active_knowledge_id'] = kid
    p['scoring_context'] = dict(version='FOLLOWUP_SCORING_V1', question_kind='NEXT', parent_question_id=None,
        parent_status='NOT_APPLICABLE', parent=None, assistance_status='UNKNOWN', open_gaps=[])
    actual = model_payload(agent_payload(p, 'answer_evaluation'), 'answer_evaluation')
    assert actual['scoring_context'] == p['scoring_context']
    assert policy['prompts']['answer_evaluation']['version'] == '2.3.2'
    assert policy['prompts']['interview_main']['version'] == '2.3.1'
    assert 'scoring_context' not in model_payload(agent_payload(p, 'interview_main'), 'interview_main')
    old = deepcopy(p)
    old['snapshot']['adaptive_policy'].pop('evaluation_context')
    old['snapshot']['adaptive_policy']['prompts'] = freeze_prompts(context_projection=True)
    old.pop('scoring_context')
    assert 'scoring_context' not in model_payload(agent_payload(old, 'answer_evaluation'), 'answer_evaluation')
    for bad in (None, {**p['scoring_context'], 'assistance_status': 'NONE'},
                {**p['scoring_context'], 'open_gaps': [{'id': 'invented', 'text': 'wrong gap'}]}):
        from interview_api.core.exceptions import AppError
        damaged = deepcopy(p)
        damaged['scoring_context'] = bad
        with pytest.raises(AppError) as error:
            model_payload(agent_payload(damaged, 'answer_evaluation'), 'answer_evaluation')
        assert error.value.code == 'SCORING_CONTEXT_REQUIRED'


def test_new_scoring_context_links_one_valid_parent_before_reuse_and_keeps_assistance_unknown(database_url):
    async def run():
        class Boundary(AdaptiveReplayProvider):
            received = []

            async def generate(self, messages, *, schema):
                if schema['title'] == 'AdaptiveEvaluation':
                    self.received.append(json.loads(messages[-1]['content'])['input'])
                    sc = self.received[-1]['scoring_context']
                    assert sc['version'] == 'FOLLOWUP_SCORING_V1'
                    assert sc['assistance_status'] == 'UNKNOWN'
                return await super().generate(messages, schema=schema)

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = Boundary()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in ('interview_main', 'answer_evaluation')}))
            created = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'], job_id=ctx['job_id'],
                interviewer_skill_id='java_backend', adaptive_interview=True, question_count=3))
            first = await core.start(created['id'], uid)
            text = 'Kafka 成功就 ACK，尚未处理重复执行。'
            events = await core.answer(created['id'], uid, first['question_id'], uuid4().hex, text)
            assert provider.received[0]['scoring_context']['parent_status'] == 'NOT_APPLICABLE'
            assert provider.received[0]['scoring_context']['parent'] is None
            aid = next(e[1]['answer_id'] for e in events if e[0] == 'evaluation')
            before = await core.detail(created['id'], uid)
            second = before['current_question']
            key = uuid4().hex
            raw = '业务唯一键和事务提交成功再 ACK，重复投递返回原结果。'
            events = await core.answer(created['id'], uid, second['question_id'], key, raw)
            p = provider.received[1]
            sc = p['scoring_context']
            assert sc['parent_status'] == 'AVAILABLE' and sc['parent_question_id'] == first['question_id']
            assert sc['parent']['question']['id'] == first['question_id']
            assert sc['parent']['answer']['id'] == aid and sc['parent']['answer']['content'] == text
            assert sc['parent']['question']['content'] == first['question']
            assert p['answer'] == raw and sc['open_gaps']
            assert 'memory_guided_plan' not in p['snapshot']
            assert not {'choices', 'candidates', 'topic_coverage'} & p['adaptive'].keys()
            assert await core.answer(created['id'], uid, second['question_id'], key, raw) == events
            assert len(provider.received) == 2
            assert (await core.detail(created['id'], uid))['context_snapshot'] == created['context_snapshot']
        finally:
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize('damage', ['no_link', 'missing_answer', 'unsubmitted', 'cross_session', 'cross_user',
    'self_cycle', 'invalid_role', 'evaluation_mismatch', 'wrong_owner', 'long_parent'])
def test_scoring_parent_ownership_validity_and_bounded_context(database_url, damage):
    from sqlalchemy import select
    from interview_api.core.exceptions import AppError
    from interview_api.modules.interview.models import InterviewSessionQuestion, InterviewAnswer, InterviewMessage, InterviewEvaluation
    from interview_api.modules.interview.adaptive import planning_payload

    async def run():
        class Boundary(AdaptiveReplayProvider):
            wire = None

            async def generate(self, messages, *, schema):
                if schema['title'] == 'AdaptiveEvaluation':
                    self.wire = json.loads(messages[-1]['content'])['input']
                return await super().generate(messages, schema=schema)

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = Boundary()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in ('interview_main', 'answer_evaluation')}))
            async def new(owner, source):
                s = await core.create(owner, CreateSessionRequest(company_id=source['company_id'], job_id=source['job_id'],
                    interviewer_skill_id='java_backend', adaptive_interview=True, question_count=3))
                q = await core.start(s['id'], owner)
                await core.answer(s['id'], owner, q['question_id'], uuid4().hex, 'Kafka 成功后 ACK，PARENT_PRIVATE')
                return s, q
            s, root = await new(uid, ctx)
            child_id = (await core.detail(s['id'], uid))['current_question']['question_id']
            state, context, questions, active, _ = await core.read_state(s['id'], uid)
            p = planning_payload(context, questions, active, state.interview_plan_json, answer='使用事务提交与业务唯一键')
            foreign = None
            if damage in ('cross_session', 'cross_user'):
                other_uid, other_ctx = (await kafka_seed(factory)) if damage == 'cross_user' else (uid, ctx)
                _, foreign = await new(other_uid, other_ctx)
            # Intentionally corrupt only freshly seeded isolated test records.
            # No Controller/DB mocks; access checks execute on real persisted links.
            async with factory() as db:
                child = await db.get(InterviewSessionQuestion, child_id)
                parent = await db.get(InterviewSessionQuestion, root['question_id'])
                answer = await db.scalar(select(InterviewAnswer).where(InterviewAnswer.session_question_id == parent.id))
                if damage == 'no_link': child.parent_question_id = None
                elif damage == 'missing_answer':
                    phantom = InterviewSessionQuestion(session_id=s['id'], question_index=-1, question='未回答的父题',
                        question_kind='NEXT', status='COMPLETED')
                    db.add(phantom)
                    await db.flush()
                    child.parent_question_id = phantom.id
                elif damage == 'unsubmitted': answer.status = 'PROCESSING'
                elif foreign: child.parent_question_id = foreign['question_id']
                elif damage == 'self_cycle': child.parent_question_id = child.id
                elif damage == 'invalid_role': (await db.get(InterviewMessage, answer.message_id)).role = 'ASSISTANT'
                elif damage == 'evaluation_mismatch':
                    (await db.scalar(select(InterviewEvaluation).where(InterviewEvaluation.answer_id == answer.id))).session_question_id = child.id
                elif damage == 'long_parent':
                    parent.question = 'Kafka' + '长问题' * 400
                    (await db.get(InterviewMessage, answer.message_id)).content = '前缀' * 1000 + 'PRIVATE_TAIL'
                await db.commit()
            if damage == 'wrong_owner':
                calls = list(provider.calls)
                with pytest.raises(AppError) as error:
                    await core.call_agent(core.evaluator.adaptive(), s['id'], uid + 999999, state.state_version, p)
                assert error.value.status_code == 404 and provider.calls == calls
                return
            await core.call_agent(core.evaluator.adaptive(), s['id'], uid, state.state_version, p)
            wire = provider.wire
            sc = wire['scoring_context']
            assert sc['assistance_status'] == 'UNKNOWN'
            assert wire['answer'] == p['answer'] and wire['rubric'] == p['rubric']
            if damage == 'long_parent':
                assert sc['parent_status'] == 'AVAILABLE'
                assert len(sc['parent']['question']['content']) == 512
                assert len(sc['parent']['answer']['content']) == 1024
                assert sc['parent']['answer']['truncated'] and sc['parent']['question']['truncated']
                assert 'PRIVATE_TAIL' not in json.dumps(wire)
            else:
                assert sc['parent_status'] == 'UNAVAILABLE' and sc['parent'] is None
                assert sc['parent_question_id'] is None and 'PARENT_PRIVATE' not in json.dumps(wire)
            final = await core.detail(s['id'], uid)
            assert final['state_version'] == state.state_version
            assert final['context_snapshot'] == s['context_snapshot']
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_evaluation_receives_current_scoring_evidence_not_next_question_space(database_url):
    async def run():
        class ScoringBoundary(AdaptiveReplayProvider):
            observed = False

            async def generate(self, messages, *, schema):
                if schema['title'] == 'AdaptiveEvaluation':
                    p = json.loads(messages[-1]['content'])['input']
                    assert 'choices' not in p['adaptive']
                    assert 'candidates' not in p['adaptive']
                    assert 'topic_coverage' not in p['adaptive']
                    assert p['answer'] == 'Kafka 处理完成后 ACK，还没有设计事务边界。'
                    assert p['current_question']['question'] and p['rubric']
                    assert p['adaptive']['active_knowledge_id'] == 'java_backend:kafka_reliability'
                    assert p['snapshot']['interviewer_evaluation']['evaluation_anchors']
                    assert 'memory_guided_plan' not in p['snapshot']
                    assert p['snapshot']['requirements']
                    self.observed = True
                return await super().generate(messages, schema=schema)

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = ScoringBoundary()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in ('interview_main', 'answer_evaluation')}))
            session = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'], job_id=ctx['job_id'],
                interviewer_skill_id='java_backend', adaptive_interview=True, question_count=3))
            q = await core.start(session['id'], uid)
            key = uuid4().hex
            answer = 'Kafka 处理完成后 ACK，还没有设计事务边界。'
            state, context, questions, active, _ = await core.read_state(session['id'], uid)
            before_plan = deepcopy(state.interview_plan_json)
            events = await core.answer(session['id'], uid, q['question_id'], key, answer)
            assert provider.observed
            assert (await core.detail(session['id'], uid))['context_snapshot'] == session['context_snapshot']
            # Rebuilt dict order does not invalidate an already verified phase.
            from interview_api.modules.interview.adaptive import planning_payload
            aid = next(event[1]['answer_id'] for event in events if event[0] == 'evaluation')
            active.status = 'ANSWERED'  # detached DTO: the phase after answer persistence, before evaluation commit
            packet = planning_payload(context, questions, active, before_plan, answer=answer, answer_id=aid)
            def reverse(value):
                if isinstance(value, dict):
                    return {k: reverse(v) for k, v in reversed(list(value.items()))}
                return [reverse(v) for v in value] if isinstance(value, list) else value
            detail = await core.detail(session['id'], uid)
            calls = list(provider.calls)
            reused = await core.call_agent(core.evaluator.adaptive(), session['id'], uid,
                detail['state_version'], reverse(packet), answer_id=aid)
            assert reused is not None and provider.calls == calls
            assert events == await core.answer(session['id'], uid, q['question_id'], key, answer)
            assert (await core.detail(session['id'], uid))['state_version'] == detail['state_version']
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_current_open_gaps_survive_projection_and_can_be_resolved_without_historical_scores(database_url):
    async def run():
        class GapBoundary(AdaptiveReplayProvider):
            seen = []

            async def generate(self, messages, *, schema):
                p = json.loads(messages[-1]['content'])['input']
                if schema['title'] == 'AdaptiveEvaluation':
                    current = p['adaptive']['knowledge_state']['items'][p['adaptive']['active_knowledge_id']]
                    assert set(current) == {'open_gaps'}
                    self.seen.append(deepcopy(current['open_gaps']))
                return await super().generate(messages, schema=schema)

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = GapBoundary()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in ('interview_main', 'answer_evaluation')}))
            session = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'], job_id=ctx['job_id'],
                interviewer_skill_id='java_backend', adaptive_interview=True, question_count=3))
            q = await core.start(session['id'], uid)
            await core.answer(session['id'], uid, q['question_id'], uuid4().hex, 'Kafka 完成就 ACK')
            detail = await core.detail(session['id'], uid)
            gap = detail['interview_plan_json']['adaptive_knowledge']['items']['java_backend:kafka_reliability']['open_gaps']
            await core.answer(session['id'], uid, detail['current_question']['question_id'], uuid4().hex,
                'Kafka 事务提交成功再 ACK，唯一键避免重复执行，失败恢复重新消费。')
            assert provider.seen == [[], gap]
            final = await core.detail(session['id'], uid)
            assert not final['interview_plan_json']['adaptive_knowledge']['items']['java_backend:kafka_reliability']['open_gaps']
        finally:
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize('invalid', ['unknown_version', 'prompt_mismatch'])
def test_incompatible_projection_fails_before_model_and_does_not_advance_session(database_url, invalid):
    async def run():
        from interview_api.core.exceptions import AppError
        from interview_api.modules.interview.adaptive import planning_payload
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = AdaptiveReplayProvider()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in ('interview_main', 'answer_evaluation')}))
            session = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'], job_id=ctx['job_id'],
                interviewer_skill_id='java_backend', adaptive_interview=True, question_count=3))
            packet = planning_payload(session['context_snapshot'], [], None, None)
            if invalid == 'unknown_version':
                packet['snapshot']['adaptive_policy']['context_projection'] = 'FUTURE_UNKNOWN'
            else:
                packet['snapshot']['adaptive_policy']['prompts']['interview_main']['version'] = '2.2.1'
            with pytest.raises(AppError) as error:
                await core.main.adaptive(packet).call(session['id'], packet)
            assert error.value.code == ('CONTEXT_PROJECTION_UNSUPPORTED' if invalid == 'unknown_version' else 'CONTEXT_PROJECTION_PROMPT_MISMATCH')
            assert not provider.calls
            detail = await core.detail(session['id'], uid)
            assert detail['state_version'] == session['state_version'] and not detail['questions']
            assert detail['context_snapshot'] == session['context_snapshot']
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_agent_tool_calling_question_keeps_schema_permissions_and_failure_rubric(database_url):
    async def run():
        from sqlalchemy import select
        from interview_api.modules.public_career.models import JobDescriptionVersion, JobRequirement

        class ToolAnswerBoundary(AdaptiveReplayProvider):
            observed = False

            async def generate(self, messages, *, schema):
                p = json.loads(messages[-1]['content'])['input']
                if schema['title'] == 'AdaptiveEvaluation':
                    assert p['adaptive']['active_knowledge_id'] == 'ai_agent_application:agent'
                    assert p['answer'] == answer
                    assert p['rubric'] and 'Agent' in p['current_question']['question']
                    assert any('Schema' in r['statement'] and '权限' in r['statement'] for r in p['snapshot']['requirements'])
                    self.observed = True
                return await super().generate(messages, schema=schema)

        answer = '工具不在白名单则拒绝，Schema 参数不合法返回结构化错误，不执行也不推进状态。失败恢复使用幂等键和有限重试。'
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            async with factory() as db:
                jd = JobDescriptionVersion(job_id=ctx['job_id'], version_number=3,
                    raw_text='DEMO fixture：Agent 工具调用 Schema、权限和失败恢复。', publication_status='PUBLISHED')
                db.add(jd)
                await db.flush()
                db.add(JobRequirement(job_description_version_id=jd.id,
                    statement='Agent 工具调用 Schema、权限和失败恢复', requirement_type='SKILL'))
                await db.commit()
            provider = ToolAnswerBoundary()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in ('interview_main', 'answer_evaluation')}))
            session = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'], job_id=ctx['job_id'],
                interviewer_skill_id='ai_agent_application', adaptive_interview=True, question_count=2))
            q = await core.start(session['id'], uid)
            events = await core.answer(session['id'], uid, q['question_id'], uuid4().hex, answer)
            assert provider.observed and any(event[0] == 'evaluation' for event in events)
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_projected_non_replay_admission_covers_wire_input_and_settles_without_stale_reservations(database_url):
    async def run():
        class OfflineMeteredBoundary(AdaptiveReplayProvider):
            # Exercise the non-zero admission branch without any network provider.
            provider_name = 'offline_metered_boundary_NOT_LIVE'
            admissions = []

            async def generate(self, messages, *, schema):
                detail = await self.core.detail(self.sid, self.uid)
                reserved = detail['interview_plan_json']['reserved_model_tokens']
                actual_wire = sum(len(m['content'].encode('utf-8')) for m in messages)
                assert reserved >= actual_wire
                if schema['title'] == 'AdaptiveEvaluation':
                    assert json.loads(messages[-1]['content'])['input']['scoring_context']['assistance_status'] == 'UNKNOWN'
                self.admissions.append(reserved)
                return await super().generate(messages, schema=schema)

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = OfflineMeteredBoundary()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in ('interview_main', 'answer_evaluation')}))
            session = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'], job_id=ctx['job_id'],
                interviewer_skill_id='java_backend', adaptive_interview=True, question_count=3))
            provider.core, provider.sid, provider.uid = core, session['id'], uid
            q = await core.start(session['id'], uid)
            await core.answer(session['id'], uid, q['question_id'], uuid4().hex, 'Kafka ACK')
            detail = await core.detail(session['id'], uid)
            assert len(provider.admissions) == 3
            assert detail['interview_plan_json']['reserved_model_tokens'] == 0
            assert not detail['interview_plan_json']['model_call_reservations']
            assert detail['interview_plan_json']['unreported_model_tokens'] == 0
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_main_shared_catalog_keeps_all_legal_pairs_and_memory_without_mutating_snapshot(database_url):
    async def run():
        class CatalogBoundary(AdaptiveReplayProvider):
            catalog = None

            async def generate(self, messages, *, schema):
                p = json.loads(messages[-1]['content'])['input']
                if schema['title'] == 'AdaptiveSelection':
                    a = p['adaptive']
                    assert 'targets' in a and 'sources' in a
                    assert all(c['target_knowledge_id'] in a['targets'] and c['source']['ref'] in a['sources']
                        for c in a['candidates'])
                    self.catalog = p
                return await super().generate(messages, schema=schema)

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = CatalogBoundary()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in ('interview_main', 'answer_evaluation')}))
            session = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'], job_id=ctx['job_id'],
                interviewer_skill_id='java_backend', adaptive_interview=True, question_count=4))
            original = deepcopy(session['context_snapshot'])
            from interview_api.modules.interview.adaptive import planning_payload
            legal = planning_payload(original, [], None, None)['adaptive']['candidates']
            await core.start(session['id'], uid)
            actual = provider.catalog
            assert actual['snapshot']['memory_guided_plan'] == original['memory_guided_plan']
            offered = actual['adaptive']['candidates']
            assert [(c['candidate_id'], c['target_knowledge_id'], c['source']['ref'], c['strategies']) for c in offered] == [
                (c['candidate_id'], c['target_knowledge_id'], c['source']['ref'], c['strategies']) for c in legal]
            assert (await core.detail(session['id'], uid))['context_snapshot'] == original
        finally:
            await engine.dispose()
    asyncio.run(run())
