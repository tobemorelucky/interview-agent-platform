"""Round 26 P1: the quote-cap repair, replayed offline against the captured answer.

The two extra HTTP calls of the previous round were not a model error: the model
returned five verbatim quotes while `KnowledgeObservation.answer_quotes` allows
three. The fix is on the prompt side (state the cap), and the repair stays as the
safety net. This file proves three things without any network:

1. five legal quotes still trip the schema cap and the repair keeps three of them,
   changing nothing else;
2. a quote that is not verbatim is not "repaired" — it is refused, so no conclusion
   can lose its only evidence silently;
3. the repair cannot rewrite scores, evaluation text or missing points.
"""
import json

import pytest
from pydantic import ValidationError

from interview_api.modules.interview.adaptive import AdaptiveEvaluation
from interview_api.modules.interview.evaluation_repair import EvaluationQuotesRepair

# Verbatim from the captured KF-A1 answer of round 25 (still in the sandbox DB).
ANSWER = (
    "Kafka 只保证至少一次，所以去重必须落在业务侧。用订单号加事件类型做幂等键，"
    "在同一个数据库事务里先写去重表（唯一约束），冲突就说明这次是重复投递，直接跳过；"
    "没冲突就更新订单状态，两者同一事务提交。处理成功后手动提交 offset，关掉自动提交，"
    "避免业务没提交但 offset 已提交导致丢消息。还要注意 offset 提交与业务事务之间的窗口："
    "业务提交成功、offset 未提交时重启会重放，这时靠幂等键兜住。"
    "重试多次仍失败的进死信队列并告警，人工补偿。")

LEGAL_QUOTES = [
    "Kafka 只保证至少一次，所以去重必须落在业务侧",
    "用订单号加事件类型做幂等键",
    "在同一个数据库事务里先写去重表（唯一约束）",
    "处理成功后手动提交 offset，关掉自动提交",
    "业务提交成功、offset 未提交时重启会重放，这时靠幂等键兜住",
]


def payload(answer=ANSWER):
    """The minimum envelope `resolve_observations` needs, matching the real one."""
    return {"answer": answer,
            "adaptive": {"active_knowledge_id": "java_backend:kafka_reliability",
                         "knowledge_state": {"items": {"java_backend:kafka_reliability": {"open_gaps": []}}}}}


def artifact(quotes, **overrides):
    value = dict(
        technical_correctness=5, coverage=5, depth=4, relevance=5, communication=5,
        evaluation="回答覆盖了至少一次语义、业务幂等键与同事务去重、手动提交 offset 与重放窗口。",
        covered_points=["至少一次语义", "幂等键与唯一约束", "同事务提交", "手动提交 offset"],
        missing_points=["并发冲突处理路径"],
        factual_or_reasoning_issues=[],
        follow_up_focus=["并发重复投递的处理"],
        next_action_recommendation="DEEP_DIVE",
        reference_answer=None,
        knowledge_observations=[dict(
            knowledge_id="java_backend:kafka_reliability", state="DEMONSTRATED_IN_SESSION",
            answer_quotes=list(quotes), missing_mechanisms=[], resolved_gap_ids=[])])
    value.update(overrides)
    return value


def too_long_error(value):
    with pytest.raises(ValidationError) as caught:
        AdaptiveEvaluation.model_validate(value)
    return caught.value


def test_five_legal_quotes_trip_the_cap_and_are_trimmed_without_side_effects():
    value = artifact(LEGAL_QUOTES)
    error = too_long_error(value)
    assert error.errors()[0]["loc"] == ("knowledge_observations", 0, "answer_quotes")
    assert error.errors()[0]["type"] == "too_long"

    policy = EvaluationQuotesRepair(payload())
    plan = policy.prepare(value, error)
    assert plan and plan["policy"] == "EVALUATION_QUOTE_SUBSET_V1"
    assert plan["selectable_quotes"] == LEGAL_QUOTES
    assert plan["max_items"] == 3

    repaired = artifact(LEGAL_QUOTES[:3])
    counts = policy.validate(value, repaired)
    assert counts == {"policy": "EVALUATION_QUOTE_SUBSET_V1", "original_count": 5, "retained_count": 3}
    # The persisted result is exactly the original artifact minus two quotes.
    before = json.loads(json.dumps(value, ensure_ascii=False))
    after = json.loads(json.dumps(repaired, ensure_ascii=False))
    before["knowledge_observations"][0].pop("answer_quotes")
    after["knowledge_observations"][0].pop("answer_quotes")
    assert before == after


def test_a_paraphrased_quote_is_refused_instead_of_trimmed_away():
    paraphrased = LEGAL_QUOTES[:4] + ["Kafka 消费端默认只消费一次"]
    value = artifact(paraphrased)
    error = too_long_error(value)
    assert EvaluationQuotesRepair(payload()).prepare(value, error) is None


def test_repair_cannot_rewrite_the_evaluation_itself():
    value = artifact(LEGAL_QUOTES)
    error = too_long_error(value)
    policy = EvaluationQuotesRepair(payload())
    rewritten = artifact(LEGAL_QUOTES[:3], technical_correctness=4)
    with pytest.raises(ValueError):
        policy.validate(value, rewritten)


def test_repair_rejects_a_quote_that_was_not_in_the_original_selection():
    value = artifact(LEGAL_QUOTES)
    error = too_long_error(value)
    policy = EvaluationQuotesRepair(payload())
    invented = artifact(LEGAL_QUOTES[:2] + ["Kafka 侧信道保证幂等"])
    with pytest.raises(ValueError):
        policy.validate(value, invented)


def test_three_quotes_need_no_repair_at_all():
    AdaptiveEvaluation.model_validate(artifact(LEGAL_QUOTES[:3]))
