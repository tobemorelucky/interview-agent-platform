"""R137: a technical '二次/三次' phrase in qualitative prose is not a fabricated statistic.

Root cause of report #19 (session 641): strengths[2].statement contained "二次"
(reliability 0.72, counts {3,1,0}); _unsupported only exempted the "一次" idiom,
so "二次" -> 2.0 not in counts -> FABRICATED_STATISTIC -> COACH_COMPUTED_NUMBER.
The model call itself succeeded (3 attempts, ~7k tokens each); rejection was post-hoc.

The fix widens the Chinese-count idiom to cover larger Chinese numerals as technical
terms (二次确认/三次握手/二次重试...), but a count that is *scoped to this session*
("本场回答二次"/"累计二次"/"只答了二次") is still strictly checked against the
server-computed counts. Score units, percentages, Arabic counts and English claims
are untouched.

Zero model HTTP. Rebuilt from the real persisted statistics of report #19.
"""
import json
from pathlib import Path

import pytest

from interview_api.core.exceptions import AppError
from interview_api.modules.interview_report.resolver import (
    _qualitative, resolve_coach_artifact, supported_statistics)
from interview_api.modules.interview_report.schemas import CoachArtifact

FIXTURE = Path(__file__).parent / "fixtures" / "coach_live_18.json"


def numbers():
    # The real statistics_json persisted on report #19 (session 641): counts {3,1,0}.
    statistics = {
        "answered_count": 3, "evaluated_count": 3, "follow_up_count": 1,
        "deep_dive_count": 1, "dynamic_question_count": 1, "total_questions": 3,
        "dimension_averages": {"technical_correctness": 3.0, "coverage": 2.0,
                               "depth": 2.0, "relevance": 3.3333, "communication": 3.0},
        "overall_average": 2.6667,
    }
    return supported_statistics(statistics, [])


# B-class: legitimate technical idioms must no longer fail the report.
@pytest.mark.parametrize("text", [
    "熟悉二次确认与幂等设计，能避免重复扣款",
    "三次握手建立连接后再传数据",
    "失败后做二次重试，并由消费端幂等兜底",
    "具备对开源工具做二次开发的能力",
    "二次校验保证了引用证据的真实性",
    "这个链路涉及二次消费与一次确认",
])
def test_technical_larger_chinese_count_idioms_pass(text):
    assert _qualitative(text, "strengths.2.statement", numbers()) == text


# A/C-class: real session statistics must still match the server counts.
@pytest.mark.parametrize("text", [
    "本场回答了二次",
    "本场共二次追问才补齐",
    "累计二次作答",
    "只答了二次就结束",
])
def test_session_scoped_larger_chinese_count_still_rejected(text):
    with pytest.raises(AppError) as error:
        _qualitative(text, "overall_summary", numbers())
    assert error.value.code == "COACH_COMPUTED_NUMBER"
    assert error.value.diagnostics["rule"] == "FABRICATED_STATISTIC"
    assert error.value.diagnostics["unit"] == "次"
    assert error.value.diagnostics["value"] == 2.0


# A-class: fabricated scores / percentages / Arabic counts are unchanged.
@pytest.mark.parametrize("text", [
    "本场总分 4分",
    "整体正确率 85%",
    "本场回答 7 次",
    "score is 9",
])
def test_fabricated_scores_and_counts_still_rejected(text):
    with pytest.raises(AppError) as error:
        _qualitative(text, "overall_summary", numbers())
    assert error.value.code == "COACH_COMPUTED_NUMBER"


# The server-computed count is still accepted for a scoped Chinese claim.
def test_server_count_scoped_claim_still_accepted():
    assert _qualitative("本场回答三次，均有评价", "overall_summary", numbers())


def test_diagnostic_carries_a_sanitized_context_hint():
    with pytest.raises(AppError) as error:
        _qualitative("本场回答了二次就结束", "overall_summary", numbers())
    diag = error.value.diagnostics
    # Structured fields preserved for the real failure signature.
    assert diag["matched_number"] == "二次"
    assert diag["unit"] == "次" and diag["value"] == 2.0
    # New: a bounded, de-identified context hint for future root-cause location.
    assert "context_hint" in diag
    assert len(diag["context_hint"]) <= 24


def test_resolver_integration_a_strength_with_technical_er_ci_resolves():
    """The exact field_path from report #19 now resolves through the resolver."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    artifact = data["artifact"]
    artifact["strengths"][2]["statement"] = "熟悉二次确认与幂等设计，能避免消息重复处理"
    resolved = resolve_coach_artifact(CoachArtifact.model_validate(artifact),
                                      data["input"]["evaluations"], data["input"]["statistics"])
    assert "二次确认" in resolved["strengths"][2]["statement"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
