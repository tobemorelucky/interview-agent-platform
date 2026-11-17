"""R124: the NEXT topic gate must be followable by the model, and diagnosable.

The user's session failed twice with ADAPTIVE_TOPIC_MISMATCH. The raw model
response is deliberately not persisted, so the exact wording is unknowable; what
*is* knowable is that the model was told to "explicitly name the topic" without
ever being told which literal words satisfy that check. These tests pin the two
contract halves:

* every published `mention_terms` entry really is accepted by `mentioned_topics`
  (a suggestion that the gate rejects would be worse than no suggestion);
* the deterministic REQUIREMENT opening produces wording the same gate accepts,
  so a blocked session can always be recovered without another model call.
"""
import pytest

from interview_api.modules.interview.adaptive import LABELS, build_blueprint
from interview_api.modules.interview.topics import (
    TOPICS, TOPIC_TERMS, mentioned_topics, requirement_opening_question, topic_diagnostic,
    topic_terms,
)


def test_published_terms_are_exactly_what_the_gate_accepts():
    for topic in TOPICS:
        for term in topic_terms(topic):
            assert topic in mentioned_topics(term), (topic, term)


def test_every_topic_publishes_terms_and_covers_its_label():
    for topic in TOPICS:
        assert topic_terms(topic), f"{topic} exposes no usable wording"
        assert topic in mentioned_topics(LABELS[topic]), LABELS[topic]


def test_blueprint_targets_publish_mention_terms():
    context = {
        "interviewer_config": {"skill": {
            "id": "ai_agent_application", "version": "1.0.0",
            "priority_topics": ["llm", "python", "agent"],
        }},
        "requirements": [], "public_questions": [], "candidate_facts": [],
        "interview_experiences": [], "approved_memory_context": {},
        "recent_performance_observations": {}, "budgets": {"question_count": 3},
    }
    blueprint = build_blueprint(context)
    assert blueprint["targets"]
    for target in blueprint["targets"]:
        assert target["mention_terms"] == topic_terms(target["topic"])
        assert target["topic"] in mentioned_topics(target["label"])


def test_requirement_opening_names_the_target_without_inventing_experience():
    statement = "了解Transformer、向量数据库、微调等大模型技术"
    question = requirement_opening_question(statement, LABELS["llm"])
    assert statement in question                    # frozen source stays verbatim
    assert "llm" in mentioned_topics(question)      # same gate the model faces
    assert "我" not in question or "你" in question  # asks the candidate, no claims


def test_requirement_opening_covers_every_blueprint_topic():
    for topic, label in LABELS.items():
        question = requirement_opening_question("示例要求", label)
        assert topic in mentioned_topics(question), (topic, label)


def test_topic_diagnostic_keeps_wording_but_not_the_transcript():
    detail = topic_diagnostic("请介绍一下你做过的一个 AI 应用项目", "llm")
    assert detail["target_topic"] == "llm"
    assert detail["matched_topics"] == []
    assert detail["expected_terms"] == topic_terms("llm")
    assert detail["question_excerpt"].startswith("请介绍")
    assert len(detail["question_sha256"]) == 64


def test_terms_do_not_leak_across_topics():
    # A term published for java must not silently satisfy an unrelated target.
    assert "java" in mentioned_topics("Java 线程池")
    assert "redis" not in mentioned_topics("Java 线程池")
