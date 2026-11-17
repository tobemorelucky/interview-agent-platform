"""R125: the project opening is allowed, but only where it is provably a project.

Relaxing a gate is dangerous unless the replacement check is narrower than the
one it relaxes. These tests pin the exact boundary:

* only the FIRST question may use it;
* only a frozen CANDIDATE_PROFILE source on a real project/work fact;
* the question must actually refer to the candidate's own work;
* every later NEXT still has to name its technical topic.
"""
import pytest
from types import SimpleNamespace

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.adaptive import is_project_opening
from interview_api.modules.interview.topics import validate_topic


def payload(question=None, facts=None):
    return dict(current_question=question,
                snapshot=dict(candidate_facts=facts if facts is not None else [
                    dict(id=11, field_path="projects.0", statement="订单秒杀系统：用 Redis 做库存扣减")]))


def source(source_type="CANDIDATE_PROFILE", source_id=11):
    return dict(source_type=source_type, source_id=source_id)


def artifact(text, strategy="NEXT"):
    return SimpleNamespace(question=SimpleNamespace(question=text), strategy=strategy)


def question(text):
    """``validate_topic`` receives the resolved QuestionArtifact, not the decision."""
    return SimpleNamespace(question=text)


def test_opening_from_a_real_project_is_legal_without_naming_the_topic():
    assert is_project_opening(payload(), source(), artifact("先聊聊你简历里那个订单项目，你负责哪部分？"))


@pytest.mark.parametrize("case", ["second_question", "education_fact", "job_requirement_source",
                                  "no_project_reference", "follow_up", "unknown_fact"])
def test_everything_else_stays_rejected(case):
    text = "先聊聊你简历里那个订单项目，你负责哪部分？"
    args = dict(payload=payload(), source=source(), artifact=artifact(text))
    if case == "second_question":
        args["payload"] = payload(question=dict(question_id=1, question="上一题"))
    if case == "education_fact":
        args["payload"] = payload(facts=[dict(id=11, field_path="education.0", statement="示例大学 本科")])
    if case == "job_requirement_source":
        args["source"] = source("JOB_REQUIREMENT", 11)
    if case == "no_project_reference":
        args["artifact"] = artifact("Redis 的持久化机制有哪几种，分别适用什么场景？")
    if case == "follow_up":
        args["artifact"] = artifact(text, strategy="FOLLOW_UP")
    if case == "unknown_fact":
        args["source"] = source(source_id=999)
    assert is_project_opening(**args) is False


def test_topic_gate_still_requires_the_topic_unless_the_policy_says_project_opening():
    pacing = dict(background_allowed=True, blocked_topics=[], asked_wording_fingerprints=[],
                  topic_counts={})
    policy = dict(target_topic="redis", source_type="CANDIDATE_PROFILE", topic_coverage=pacing)
    with pytest.raises(AppError) as error:
        validate_topic(question("先聊聊你简历里那个订单项目，你负责哪部分？"), dict(policy))
    assert error.value.code == "INTERVIEW_TOPIC_REQUIRED"
    # The relaxed flag is only ever set by validate_decision for a proven project opening.
    validate_topic(question("先聊聊你简历里那个订单项目，你负责哪部分？"), dict(policy, project_opening=True))


def test_switching_topic_still_requires_naming_it_even_with_the_flag_set():
    pacing = dict(background_allowed=True, blocked_topics=[], asked_wording_fingerprints=[], topic_counts={})
    policy = dict(target_topic="redis", source_type="CANDIDATE_PROFILE", topic_coverage=pacing)
    # A next-topic question that says nothing about the target is still refused when
    # the opening flag is not set (i.e. any question after the first one).
    with pytest.raises(AppError):
        validate_topic(question("聊聊你之前做过的一个系统吧"), dict(policy))
