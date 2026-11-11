"""Technical pacing is reproducible from pinned sources and persisted questions."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.artifacts import MainArtifact
from interview_api.modules.interview.context import question_policy, validate_question
from interview_api.modules.interview.controller import SessionController
from interview_api.modules.interview.topics import classify, coverage, validate_topic
from test_m4_interview import seed, queue_start, queue_answer, run_with_database


def row(index, text, *, parent=None, category=None):
    refs = {} if category is None else {'topic_policy': {**classify(text), 'category': category}}
    return SimpleNamespace(id=index + 1, question_index=index, question=text, candidate_refs_json=refs,
        parent_question_id=parent, source_question_id=None, job_requirement_id=None,
        question_kind='FOLLOW_UP' if parent else 'NEXT')


def context():
    return dict(budgets=dict(question_count=12, max_follow_ups=2, max_rounds=40,
                            max_model_tokens=200000, max_duration_seconds=7200),
        public_questions=[dict(id=1, question='请介绍你的学历'), dict(id=2, question='为什么选择这个岗位'),
                          dict(id=3, question='Kafka 如何保证消息可靠性？'), dict(id=4, question='Redis 缓存失效如何处理？')],
        requirements=[dict(id=1, statement='博士学历，计算机相关专业'), dict(id=2, statement='掌握 Agent 工具调用')],
        candidate_facts=[dict(id=1, field_path='projects.0', statement='使用 Kafka 异步下单', evidence_ids=['e1']),
                         dict(id=2, field_path='education.0', statement='硕士学历', evidence_ids=['e2'])],
        candidate_evidence=[dict(key='e1'), dict(key='e2')])


@pytest.mark.parametrize('text', ['你的研究生专业是什么？', '你在哪所大学就读？', '介绍下教育背景', '本科在哪所学校？'])
def test_education_rephrasing_has_one_cooldown(text):
    prior = [row(0, '介绍你的学校和学历')]
    policy = question_policy(context(), prior)
    with pytest.raises(AppError) as error:
        validate_topic(SimpleNamespace(question=text), policy)
    assert error.value.code == 'INTERVIEW_BACKGROUND_LIMIT'
    assert 'education' in policy['topic_coverage']['blocked_topics']


def test_background_limit_first_five_and_overall():
    prior = [row(0, '为什么选择这个岗位')]
    for text in ['RAG 检索怎么实现', 'Kafka 如何重试', 'Redis 缓存如何设计']:
        prior.append(row(len(prior), text))
    assert coverage(prior, 20)['background_limit'] == 1
    assert not coverage(prior, 20)['background_allowed']
    prior.append(row(4, 'Java 对象分配'))
    assert coverage(prior, 20)['background_limit'] == 2
    prior.append(row(5, '请介绍团队合作经验'))
    assert not coverage(prior, 20)['background_allowed']


@pytest.mark.parametrize(('text', 'topic'), [
    ('我们先聊 RAG 检索：你在检索助手里把 BM25 和向量召回按排名融合，具体融合步骤是怎样的，为什么选择按排名融合，而不是直接合并两边的原始分数？', 'rag_retrieval'),
    ('为什么选择RRF？', 'rag_retrieval'),
    ('为什么选择Redis？', 'redis'),
])
def test_technical_choice_keeps_its_topic_even_after_background_quota_is_used(text, topic):
    prior = [row(0, '为什么选择这个岗位')]
    policy = dict(target_category='TECHNICAL', topic_coverage=coverage(prior, 20))
    validate_topic(SimpleNamespace(question=text), policy)
    assert classify(text)['topic'] == topic
    assert coverage([*prior, row(1, text)], 20)['background_count'] == 1


@pytest.mark.parametrize('text', [
    '为什么选择我们公司？',
    '为什么选择这个 Redis 岗位？',
    '为什么选择我们公司做 Redis 项目？',
    '你提到做过 RAG，为什么选择加入我们公司？',
    'RAG 项目之后，为什么选择我们公司？顺便谈一下 Redis。',
    '为什么选择这个？',
])
def test_career_or_unclear_choice_cannot_bypass_background_quota(text):
    prior = [row(0, '请介绍一下自己')]
    policy = dict(target_category='BACKGROUND', topic_coverage=coverage(prior, 20))
    with pytest.raises(AppError) as error:
        validate_topic(SimpleNamespace(question=text), policy)
    assert error.value.code == 'INTERVIEW_BACKGROUND_LIMIT'


def test_three_technical_turns_force_switch_even_if_agent_requests_follow_up():
    c = context()
    prior = [row(0, 'RAG 检索怎么设计'), row(1, 'RRF 如何融合排名', parent=1), row(2, 'BM25 如何处理冲突', parent=1)]
    assert SessionController.choose_action(c, prior, prior[-1], 'DEEP_DIVE', rounds=3,
        tokens=0, started_at=datetime.now(timezone.utc)) == 'NEXT'
    policy = question_policy(c, prior)
    assert policy['target_topic'] != 'rag_retrieval'
    with pytest.raises(AppError) as error:
        validate_topic(SimpleNamespace(question='再介绍 RAG 的检索流程'), policy)
    assert error.value.code == 'INTERVIEW_TOPIC_COOLDOWN'


def test_archived_agent_question_after_rag_signoff_can_switch_topics():
    from interview_api.modules.interview.topics import mentioned_topics
    text = ('检索这块先聊到这。回到你简历里的工具工作流：模型给出调用意图后，服务端要落库任务状态再真正执行工具。'
            '这两步的先后和事务边界你会怎么安排？比如先写“执行中”再调工具，如果写库成功但调用前进程挂了，'
            '或者工具已经执行但结果没回写，分别怎么收敛？')
    prior = [row(i, 'RAG 检索和 RRF 如何实现') for i in range(3)]
    policy = dict(target_category='TECHNICAL', target_topic='agent', source_type='CANDIDATE_PROFILE',
                  topic_coverage=coverage(prior, 20))
    assert 'agent' in mentioned_topics(text)
    assert 'rag_retrieval' not in mentioned_topics(text)
    validate_topic(SimpleNamespace(question=text), policy)
    assert classify(text)['topic'] == 'agent'
    for other in ('检索这块先聊到这。RRF 的参数怎么调？', '检索这块先聊到这吗？然后讲一下工具调用。'):
        with pytest.raises(AppError) as error:
            validate_topic(SimpleNamespace(question=other), policy)
        assert error.value.code == 'INTERVIEW_TOPIC_COOLDOWN'


def test_eligibility_is_not_an_interview_question_source():
    c = context()
    # No technical public pool: a technical requirement must beat the degree.
    c['public_questions'] = []
    selected = question_policy(c, [])
    assert selected['source_id'] != 1 or selected['source_type'] != 'JOB_REQUIREMENT'
    assert selected['target_category'] != 'BACKGROUND'
    with pytest.raises(AppError):
        validate_topic(SimpleNamespace(question='请说一下你是否满足博士学历要求'), selected)


def test_same_wording_is_rejected_without_semantic_similarity():
    c = context(); prior = [row(0, 'Kafka 如何保证消息可靠性？')]
    with pytest.raises(AppError) as error:
        validate_topic(SimpleNamespace(question='Kafka 如何保证消息可靠性!'), question_policy(c, prior))
    assert error.value.code == 'INTERVIEW_REPEATED_QUESTION'


def test_deduplicating_skill_project_text_preserves_project_provenance():
    c = context()
    c['public_questions'] = []
    c['requirements'] = []
    c['candidate_facts'].insert(0, dict(id=3, field_path='skills.0',
        statement='使用 Kafka 异步下单', evidence_ids=['e1']))
    selected = question_policy(c, [row(0, 'RAG 检索链路'), row(1, 'Redis 缓存边界')])
    assert selected['source_type'] == 'CANDIDATE_PROFILE'
    assert selected['source_id'] == 1
    assert selected['target_category'] == 'PROJECT'


def test_completed_project_chain_prefers_other_real_project_and_uncovered_technology():
    c = context()
    c['candidate_facts'].append(dict(id=3, field_path='projects.1',
        statement='Java Redis Kafka MySQL 实现异步下单', evidence_ids=['e1']))
    c['candidate_facts'][0]['statement'] = 'RAG BM25 RRF 混合检索'
    prior = [row(0, '请介绍 RAG 项目', category='PROJECT'), row(1, 'RRF 如何融合排名', parent=1),
             row(2, 'RRF 参数如何验证', parent=1)]
    prior[0].candidate_refs_json['fact_ids'] = [1]
    before = deepcopy(c)
    policy = question_policy(c, prior)
    assert policy['source_type'] == 'CANDIDATE_PROFILE' and policy['source_id'] == 3
    assert policy['target_topic'] == 'kafka_reliability'
    assert c == before


def test_selected_candidate_technical_topic_cannot_be_replaced_by_another_introduction():
    c = context(); c['public_questions'] = []; c['requirements'] = []
    policy = question_policy(c, [])
    with pytest.raises(AppError) as error:
        validate_topic(SimpleNamespace(question='介绍一下这个项目的完整技术栈'), policy)
    assert error.value.code == 'INTERVIEW_TOPIC_REQUIRED'


def test_technical_name_next_to_chinese_is_not_a_missing_topic():
    from interview_api.modules.interview.topics import mentioned_topics
    assert 'kafka_reliability' in mentioned_topics('Kafka的消息可靠性怎么保证？')
    assert classify('RRF是怎么融合两路排序的？')['topic'] == 'rag_retrieval'
    assert 'java' not in mentioned_topics('JavaScript的异步回调')
    c = context(); c['public_questions'] = []; c['requirements'] = []
    validate_topic(SimpleNamespace(question='Kafka的重复消费如何处理？'), question_policy(c, []))


def test_parent_bound_follow_up_keeps_topic_without_repeating_technical_name():
    prior = [row(0, 'Kafka的订单消费如何处理？'),
             row(1, '刚才的退避间隔怎么设定？', parent=1),
             row(2, '消费线程的sleep如何避免阻塞？', parent=2)]
    before = deepcopy(prior[1].candidate_refs_json)
    counts = coverage(prior, 8)
    assert counts['topic_counts'] == {'kafka_reliability': 3}
    assert counts['recent_topic'] == 'kafka_reliability'
    assert counts['blocked_topics'] == ['kafka_reliability']
    assert prior[1].candidate_refs_json == before


def test_follow_up_payload_pins_actual_answer_and_evaluation_gap():
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview.agents import InterviewPromptRegistry
    # The prompt cannot substitute an invented Kafka claim for the actual RRF answer.
    prompt = InterviewPromptRegistry().get('interview_main')
    assert 'FOLLOW_UP stays on ONE concrete gap in the latest answer' in prompt.system
    assert 'Never recalculate its counts' in prompt.system
    assert InterviewPromptRegistry().get('interview_main', '1.0.11').version == '1.0.11'


def test_five_main_questions_and_refresh_preserve_provenance_and_topic_counts(database_url):
    async def scenario(factory):
        core, main, evaluator, uid, detail = await seed(factory, budget=6)
        sid = detail['id']; pinned = deepcopy(detail['context_snapshot'])
        await queue_start(core, main, uid, sid)
        for _ in range(4):
            request = await queue_answer(core, main, evaluator, uid, sid, 'NEXT')
            await core.answer(sid, uid, request.question_id, request.idempotency_key, request.content)
        refreshed = await core.detail(sid, uid)
        assert refreshed['topic_coverage']['main_question_count'] == 5
        assert refreshed['topic_coverage']['background_count'] <= 1
        assert refreshed['context_snapshot'] == pinned
        assert refreshed['questions'][0]['source_fingerprint']
        _, _, rows, active, _ = await core.read_state(sid, uid)
        assert refreshed['topic_coverage'] == coverage(rows, 6)
        assert all(q.candidate_refs_json.get('topic_policy') for q in rows)
        payload = core.payload(pinned, rows, active, 'FOLLOW_UP', question_policy(pinned, rows, 'FOLLOW_UP'),
            answer='Replay: RAG 使用 BM25 Dense RRF', evaluation={'missing_points': ['RRF 的排名融合']})
        assert payload['answer'].endswith('BM25 Dense RRF')
        assert payload['evaluation']['missing_points'] == ['RRF 的排名融合']
        assert payload['topic_coverage'] == refreshed['topic_coverage']
    asyncio.run(run_with_database(database_url, scenario))
