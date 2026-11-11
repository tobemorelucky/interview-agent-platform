"""Coach number guard: idioms are not statistics, and statistics must be the server's.

The fixture is the real hosted-model Coach output that the round-4 live batch
rejected, so the regression is proven on the exact text that produced the bug.
"""
import json
from pathlib import Path

import pytest

from interview_api.core.exceptions import AppError
from interview_api.modules.interview_report.resolver import (_qualitative, resolve_coach_artifact,
    supported_statistics)
from interview_api.modules.interview_report.schemas import CoachArtifact

FIXTURE = Path(__file__).parent / "fixtures" / "coach_live_18.json"


def case():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def live():
    return case()["input"]


def artifact():
    return CoachArtifact.model_validate(case()["artifact"])


def numbers():
    data = live()
    return supported_statistics(data["statistics"], data["evaluations"])


def test_the_real_rejected_coach_output_now_resolves():
    data = live()
    resolved = resolve_coach_artifact(artifact(), data["evaluations"], data["statistics"])
    assert resolved["overall_summary"]
    assert len(resolved["strengths"]) == 5 and len(resolved["weaknesses"]) == 5
    assert len(resolved["recurring_issues"]) == 3 and len(resolved["recommended_actions"]) == 5
    # Every cited evaluation is one of the persisted ones.
    cited = {i for item in resolved["strengths"] + resolved["weaknesses"] for i in item["supporting_evaluation_ids"]}
    assert cited <= {e["id"] for e in data["evaluations"]}


@pytest.mark.parametrize("text", [
    "派发使用至少一次投递，重复由消费端幂等消除",
    "一次调用里先写意图再发请求",
    "如果第一次投递失败会退避重试",
    "这是一次性的补偿任务",
    "把同步受理与异步履约讲成一条可解释链路",
])
def test_technical_idioms_are_not_statistics(text):
    assert _qualitative(text, "probe", numbers()) == text


@pytest.mark.parametrize("text", [
    "本场共六轮回答",
    "本场总分 5分",
    "整体正确率 85%",
    "追问了 7 次才补齐",
    "本场共 9 道题",
    "score is 9",
])
def test_fabricated_statistics_are_still_rejected(text):
    with pytest.raises(AppError) as error:
        _qualitative(text, "overall_summary", numbers())
    assert error.value.code == "COACH_COMPUTED_NUMBER"
    assert error.value.diagnostics["rule"] == "FABRICATED_STATISTIC"
    assert error.value.diagnostics["field_path"] == "overall_summary"


@pytest.mark.parametrize("text", [
    "本场共 18 道题",
    "本场 18 轮回答里 12 次是追问",
    "技术正确性平均 3.89 分",
])
def test_statistics_the_server_computed_are_accepted(text):
    assert _qualitative(text, "overall_summary", numbers()) == text


def test_without_statistics_every_count_is_rejected():
    with pytest.raises(AppError):
        _qualitative("本场三轮回答表现不一致", "overall_summary", dict(scores=set(), counts=set()))


def test_the_guard_is_still_applied_through_the_resolver():
    data = live()
    value = json.loads(json.dumps(case()["artifact"]))
    value["overall_summary"] = "本场共六轮回答，整体偏弱"
    with pytest.raises(AppError) as error:
        resolve_coach_artifact(CoachArtifact.model_validate(value), data["evaluations"], data["statistics"])
    assert error.value.code == "COACH_COMPUTED_NUMBER"
    assert error.value.diagnostics["matched_number"] == "六轮回答"
