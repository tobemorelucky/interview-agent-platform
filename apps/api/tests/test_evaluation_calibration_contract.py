"""Draft-label validation and real model/service boundary characterization, NOT accuracy eval."""
import json
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest


FIXTURE = Path(__file__).parent / 'fixtures' / 'evaluation_calibration_v1.json'
DIMENSIONS = ('technical_correctness', 'coverage', 'depth', 'relevance', 'communication')


def load_cases():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))['samples']


def test_calibration_labels_are_reviewable_drafts_not_claimed_ground_truth():
    cases = load_cases()
    assert len(cases) == 12
    assert len({c['id'] for c in cases}) == 12
    for c in cases:
        assert c['review_status'] == 'DRAFT_UNREVIEWED' and not c['reviewers']
        assert c['question'] and c['answer'] and c['rubric'] and c['target']
        assert c['provenance'] and c['acceptable_states']
        for d in DIMENSIONS:
            lo, hi = c['reference_scores'][d]['range']
            assert 1 <= lo <= hi <= 5
            assert c['reference_scores'][d]['reason']
        for k in ('demonstrated_knowledge', 'mentioned_not_demonstrated', 'critical_errors',
                  'context', 'independent_verification', 'review_questions'):
            assert k in c


def test_problem_recognition_is_not_a_mechanism_and_hint_success_is_not_independent():
    cases = {c['id']: c for c in load_cases()}
    assert not cases['E02']['demonstrated_knowledge']
    assert cases['E02']['mentioned_not_demonstrated']
    assert cases['E01']['demonstrated_knowledge']
    assert cases['E02']['reference_scores']['depth']['range'][1] < cases['E01']['reference_scores']['depth']['range'][0]
    assert cases['E07']['context']['substantive_hint']
    assert not cases['E07']['independent_verification']['eligible']
    assert cases['E10']['independent_verification']['eligible']
    assert cases['E10']['context']['historical_capability']['state'] == 'CONFLICTED'


def test_retained_live_ab_has_identical_scoring_evidence_but_different_observations():
    """Local retained audit is optional outside this workspace; never use it as a gold label."""
    artifact = Path(__file__).resolve().parents[3] / '.runtime/acceptance/v231_ab_live_20261027_b.json'
    if not artifact.exists():
        pytest.skip('optional ignored original Live A/B artifact not present')
    rows = json.loads(artifact.read_text(encoding='utf-8'))['http']
    old, new = [next(h for h in rows if h['phase'] == phase) for phase in ('A_evaluation_old', 'A_evaluation_new')]
    a, b = [json.loads(h['request_messages'][-1]['content']) for h in (old, new)]
    draft = next(c for c in load_cases() if c['id'] == 'E03')
    assert a['schema'] == b['schema']
    for key in ('current_question', 'answer', 'rubric', 'desired_difficulty'):
        assert a['input'][key] == b['input'][key]
    assert draft['question'] == a['input']['current_question']['question']
    assert draft['answer'] == a['input']['answer'] and draft['rubric'] == a['input']['rubric']
    assert a['input']['snapshot']['interviewer_evaluation'] == b['input']['snapshot']['interviewer_evaluation']
    assert new['request_messages'][0]['content'].startswith(old['request_messages'][0]['content'])
    kid = draft['target']
    assert a['input']['adaptive']['knowledge_state']['items'][kid]['open_gaps'] == draft['context']['prior_open_gaps']
    assert b['input']['adaptive']['knowledge_state']['items'][kid] == {'open_gaps': draft['context']['prior_open_gaps']}
    assert [old['business_output'][d] for d in DIMENSIONS] == [1, 1, 1, 3, 3]
    assert [new['business_output'][d] for d in DIMENSIONS] == [2, 2, 2, 4, 4]
    assert old['business_output']['knowledge_observations'][0]['state'] == 'NEEDS_CLARIFICATION'
    assert new['business_output']['knowledge_observations'][0]['state'] == 'PARTIALLY_DEMONSTRATED'


@pytest.mark.parametrize('projected', [False, True], ids=['legacy-2.1.0', 'role-2.3.1'])
def test_draft_cases_reach_public_evaluation_boundary_without_changing_scoring_inputs(database_url, projected):
    """Synthetic public Agent inputs; returned scores prove no semantic accuracy."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from interview_api.infrastructure.models.router import ModelRouter
    from interview_api.infrastructure.models.provider import ModelResponse
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.modules.interview.adaptive import planning_payload
    from interview_api.modules.interview.adaptive_prompts import freeze_prompts
    from v21_support import AdaptiveReplayProvider
    from v22_support import kafka_seed

    async def run():
        class Boundary(AdaptiveReplayProvider):
            case = None
            seen = []

            async def generate(self, messages, *, schema):
                if schema['title'] != 'AdaptiveEvaluation':
                    return await super().generate(messages, schema=schema)
                c = self.case
                p = json.loads(messages[-1]['content'])['input']
                assert p['answer'] == c['answer']
                assert p['current_question']['question'] == c['question']
                assert p['rubric'] == c['rubric']
                assert p['snapshot']['interviewer_evaluation'] == self.anchors
                assert self.anchors['skill_id'] == c['target'].split(':')[0]
                assert 'memory_guided_plan' not in p['snapshot']
                assert 'approved_memory_context' not in p['snapshot']
                assert 'recent_performance_observations' not in p['snapshot']
                assert 'CONFLICTED_HISTORY_SENTINEL' not in json.dumps(p)
                kid = p['adaptive']['active_knowledge_id']
                assert kid == c['target']
                assert p['adaptive']['knowledge_state']['items'][kid]['open_gaps'] == c['context']['prior_open_gaps']
                if projected:
                    assert not {'candidates', 'choices', 'topic_coverage'} & p['adaptive'].keys()
                    assert p['adaptive']['target']['knowledge_id'] == c['target']
                    assert p['snapshot']['requirements'][0]['statement'] == c['scoring_sources'][0]['text']
                # These annotations are NOT sent: expose the gap, don't invent a supported field.
                assert not {'parent_answer', 'parent_question', 'substantive_hint', 'assisted'} & p.keys()
                assert 'interviewer_guidance' not in p['current_question']
                self.seen.append(c['id'])
                value = dict(technical_correctness=2, coverage=2, depth=2, relevance=4, communication=4,
                    evaluation='固定Replay仅用于契约检验，不是本样本真实评分。', covered_points=[],
                    missing_points=['待人工复核'], factual_or_reasoning_issues=[], follow_up_focus=[],
                    next_action_recommendation='FOLLOW_UP', knowledge_observations=[dict(knowledge_id=kid,
                        state='INSUFFICIENT_EVIDENCE', answer_quotes=[], missing_mechanisms=['待人工复核'], resolved_gap_ids=[])])
                return ModelResponse(json.dumps(value, ensure_ascii=False), dict(input_tokens=0, output_tokens=0, total_tokens=0))

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = Boundary()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in ('interview_main', 'answer_evaluation')}))
            sessions = {}
            for skill in ('java_backend', 'ai_agent_application'):
                created = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'], job_id=ctx['job_id'],
                    interviewer_skill_id=skill, adaptive_interview=True, question_count=3))
                await core.start(created['id'], uid)
                state, context, questions, active, _ = await core.read_state(created['id'], uid)
                before = await core.detail(created['id'], uid)
                sessions[skill] = (created, state, context, questions, active, before)
            # Like the documented A/B public Agent arm: input counterfactual only;
            # never edit the stored Session's frozen contract or Questions.
            for c in load_cases():
                created, state, context, questions, active, before = sessions[c['target'].split(':')[0]]
                p = planning_payload(context, questions, active, state.interview_plan_json, answer=c['answer'])
                # Keep the already audited 2.1.0/2.3.1 counterfactual arms frozen;
                # current service Sessions now use 2.3.2, tested separately below.
                p['snapshot']['adaptive_policy'].pop('evaluation_context', None)
                if projected:
                    p['snapshot']['adaptive_policy']['prompts'] = freeze_prompts(context_projection=True)
                if not projected:
                    p['snapshot']['adaptive_policy'].pop('context_projection', None)
                    p['snapshot']['adaptive_policy']['prompts'] = freeze_prompts(contract='TARGET_SOURCE_PAIR_V1', memory_guided=True)
                p['current_question'] = dict(question_id=active.id, question=c['question'])
                p['rubric'] = c['rubric']
                p['adaptive']['active_knowledge_id'] = c['target']
                p['adaptive']['knowledge_state']['items'] = {c['target']: dict(open_gaps=c['context']['prior_open_gaps'])}
                # Explicit synthetic frozen input target/source for this boundary case.
                p['snapshot']['adaptive_blueprint']['targets'] = [dict(knowledge_id=c['target'], topic=c['target'].split(':')[1],
                    label=c['kind'], purpose='本题评分', scope='THIS_INTERVIEW_NOT_STABLE_SKILL', sources=[dict(ref='requirement:1')])]
                p['snapshot']['requirements'] = [dict(id=1, statement=c['scoring_sources'][0]['text'])]
                p['snapshot']['approved_memory_context'] = dict(items=['CONFLICTED_HISTORY_SENTINEL'])
                p['snapshot']['recent_performance_observations'] = dict(items=['CONFLICTED_HISTORY_SENTINEL'])
                from interview_api.modules.interview.skill_engine import evaluation_contract
                provider.anchors = evaluation_contract(p['snapshot']['interviewer_config'])
                provider.case = c
                result = await core.evaluator.adaptive().call(created['id'], p)
                assert result.prompt_version == ('2.3.1' if projected else '2.1.0')
            assert provider.seen == [c['id'] for c in load_cases()]
            for created, _, _, _, _, before in sessions.values():
                after = await core.detail(created['id'], uid)
                assert after['context_snapshot'] == before['context_snapshot']
                assert after['state_version'] == before['state_version']
                assert after['questions'] == before['questions']
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_real_followup_now_keeps_parent_answer_and_unknown_hint_status_separate_from_memory(database_url):
    """Current service flow has bounded parent context, NOT trustworthy technical hint tracing."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from interview_api.infrastructure.models.router import ModelRouter
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.modules.memory.capability_service import CapabilityMemoryService
    from v21_support import AdaptiveReplayProvider
    from v22_support import kafka_seed

    async def run():
        class Boundary(AdaptiveReplayProvider):
            received = []

            async def generate(self, messages, *, schema):
                if schema['title'] == 'AdaptiveEvaluation':
                    self.received.append(json.loads(messages[-1]['content'])['input'])
                return await super().generate(messages, schema=schema)

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = Boundary()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in ('interview_main', 'answer_evaluation')}))
            created = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'], job_id=ctx['job_id'],
                interviewer_skill_id='java_backend', adaptive_interview=True, question_count=2, interviewer_style='gentle'))
            first = await core.start(created['id'], uid)
            parent_answer = 'Kafka 成功就 ACK，父回答追踪标记 PARENT_7f8c'
            await core.answer(created['id'], uid, first['question_id'], uuid4().hex, parent_answer)
            detail = await core.detail(created['id'], uid)
            question = detail['current_question']
            raw = '订单ID唯一约束与事务原子提交后 ACK；失败重试，重复请求返回原结果。'
            key = uuid4().hex
            events = await core.answer(created['id'], uid, question['question_id'], key, raw)
            wire = provider.received[-1]
            assert wire['answer'] == raw and wire['rubric']
            assert 'PARENT_7f8c' in wire['scoring_context']['parent']['answer']['content']
            assert wire['scoring_context']['assistance_status'] == 'UNKNOWN'
            assert set(wire['current_question']) == {'question_id', 'question'}
            kid = wire['adaptive']['active_knowledge_id']
            assert wire['adaptive']['knowledge_state']['items'][kid]['open_gaps']
            assert not {'parent_question', 'parent_answer', 'substantive_hint', 'assisted', 'history'} & wire.keys()
            calls = list(provider.calls)
            assert await core.answer(created['id'], uid, question['question_id'], key, raw) == events
            assert provider.calls == calls
            final = await core.detail(created['id'], uid)
            assert final['status'] == 'COMPLETED' and len(final['questions']) == 2
            assert final['context_snapshot'] == created['context_snapshot']
            # The fixed Replay produces high current-answer scores, but an assisted
            # question remains non-independent in the REAL capability service.
            async with factory() as db:
                memory = next(m for m in await CapabilityMemoryService(db).list(uid) if m['topic'] == 'kafka_reliability')
                assert memory['state'] == 'PRACTICING' and memory['independent_sessions'] == 0
        finally:
            await engine.dispose()
    asyncio.run(run())
