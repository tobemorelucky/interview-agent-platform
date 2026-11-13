"""Round 19: requirement strength must follow the pasted text, not a keyword list.

Covers the five situations the round-18 report could not tell apart: an explicit
obligation, a qualification merely listed under 任职要求, an explicit preference,
a duty that is not a qualification, and information the text never states.
"""
import pytest

from interview_api.modules.job_target.schemas import RequirementInput
from interview_api.modules.job_target.service import (
    BASIS_DUTY, BASIS_EXPLICIT_MANDATORY, BASIS_LISTED, BASIS_NOT_STATED, BASIS_PREFERRED,
    BASIS_USER, _rows, classify_requirement, jd_section_of, location_only, rule_extract,
)

JD = """岗位职责：
1. 负责 Python 服务开发与接口维护；
2. 参与检索服务的容量评估与稳定性治理。
任职要求：
1. 必须熟悉 Python；
2. Python 开发经验；
3. 熟悉 Python 优先；
4. 具备团队协作意识。

工作地点
深圳/上海/北京
"""


@pytest.mark.parametrize("statement,section,importance,basis", [
    ("必须熟悉 Python", "REQUIREMENT", "REQUIRED", BASIS_EXPLICIT_MANDATORY),
    ("Python 开发经验", "REQUIREMENT", "UNKNOWN", BASIS_LISTED),
    ("熟悉 Python 优先", "REQUIREMENT", "PREFERRED", BASIS_PREFERRED),
    ("负责 Python 服务开发", "DUTY", "UNKNOWN", BASIS_DUTY),
    ("具备团队协作意识", None, "UNKNOWN", BASIS_NOT_STATED),
    ("本科及以上学历", "REQUIREMENT", "REQUIRED", BASIS_EXPLICIT_MANDATORY),
])
def test_strength_reflects_what_the_text_says(statement, section, importance, basis):
    assert classify_requirement(statement, section) == (importance, basis)


def test_a_listed_qualification_is_not_promoted_to_a_hard_filter():
    """'Python 开发经验' sits under 任职要求 but states no minimum."""
    assert classify_requirement("Python 开发经验", "REQUIREMENT") == ("UNKNOWN", BASIS_LISTED)
    # ... and an explicit minimum in the same section still is one.
    assert classify_requirement("至少 3 年 Python 开发经验", "REQUIREMENT")[0] == "REQUIRED"


def test_section_lookup_uses_the_quoted_span_not_the_heading_order():
    assert jd_section_of(JD, "必须熟悉 Python") == "REQUIREMENT"
    assert jd_section_of(JD, "负责 Python 服务开发") == "DUTY"
    assert jd_section_of("没有小节的岗位描述，只有一句熟悉 Python。", "熟悉 Python") is None


@pytest.mark.parametrize("text", [
    "北京/西安/南京/深圳/上海/杭州",
    "工作地点：深圳/上海/北京",
    "工作城市：北京",
    "深圳/上海/北京",
])
def test_locations_and_city_lists_are_not_qualifications(text):
    assert location_only(text)


@pytest.mark.parametrize("text", [
    "人工智能、计算机、软件工程、电子信息、自动化等相关专业",
    "接受出差，工作地点北京/上海",
    "熟练掌握 Java，熟悉 Spring Boot 与常见服务端框架",
])
def test_a_major_list_or_a_travel_condition_is_not_a_city_list(text):
    assert not location_only(text)


def test_rule_extraction_keeps_qualifications_and_drops_the_city_block():
    result = rule_extract(JD, None, None)
    statements = [clean(row["statement"]) for row in result["requirements"]]
    assert statements == ["必须熟悉 Python", "Python 开发经验", "熟悉 Python 优先", "具备团队协作意识"]
    assert "深圳" not in json_text(result)
    importance = {clean(row["statement"]): (row["importance"], row["basis"]) for row in result["requirements"]}
    assert importance["必须熟悉 Python"] == ("REQUIRED", BASIS_EXPLICIT_MANDATORY)
    assert importance["Python 开发经验"] == ("UNKNOWN", BASIS_LISTED)
    assert importance["熟悉 Python 优先"] == ("PREFERRED", BASIS_PREFERRED)
    assert all(row["evidence"] for row in result["requirements"])


def clean(statement: str) -> str:
    return statement.rstrip("；;。.")


def json_text(value):
    import json
    return json.dumps(value, ensure_ascii=False)


def test_model_rows_are_reclassified_and_unquotable_rows_dropped():
    diagnostics = {}
    rows = _rows([
        {"statement": "必须熟悉 Python", "importance": "UNKNOWN", "evidence": "必须熟悉 Python"},
        {"statement": "Python 开发经验", "importance": "REQUIRED", "evidence": "Python 开发经验"},
        # The model invents a filter the text never states.
        {"statement": "本科及以上学历", "importance": "REQUIRED", "evidence": "本科及以上学历"},
        # A duty is not a qualification, but it is reported honestly if sent.
        {"statement": "负责 Python 服务开发", "importance": "REQUIRED", "evidence": "负责 Python 服务开发"},
    ], "LLM", JD, diagnostics=diagnostics)
    assert [row["statement"] for row in rows] == ["必须熟悉 Python", "Python 开发经验", "负责 Python 服务开发"]
    assert rows[0]["importance"] == "REQUIRED" and rows[0]["basis"] == BASIS_EXPLICIT_MANDATORY
    assert rows[1]["importance"] == "UNKNOWN" and rows[1]["basis"] == BASIS_LISTED
    assert rows[2]["importance"] == "UNKNOWN" and rows[2]["basis"] == BASIS_DUTY
    assert diagnostics["unquotable_rows_dropped"] == 1
    assert all(row["source"] == "LLM" and row["evidence"] for row in rows)


def test_model_cannot_store_a_city_list_as_a_requirement():
    diagnostics = {}
    rows = _rows([{"statement": "深圳/上海/北京", "importance": "REQUIRED", "evidence": "深圳/上海/北京"}],
                 "LLM", JD, diagnostics=diagnostics)
    assert rows == [] and diagnostics["location_rows_dropped"] == 1


def test_user_rows_keep_their_own_strength_and_are_marked_separately():
    rows = _rows([RequirementInput(statement="能接受轮岗", importance="PREFERRED")],
                 "USER", JD, require_verbatim=False, classify=False)
    assert rows[0]["importance"] == "PREFERRED" and rows[0]["basis"] == BASIS_USER
    assert rows[0]["source"] == "USER" and rows[0]["evidence"] is None


def test_rule_and_model_agree_on_the_same_text():
    """Both paths now derive strength from the same sentence and section."""
    rule = {clean(row["statement"]): row["importance"] for row in rule_extract(JD, None, None)["requirements"]}
    model = {clean(row["statement"]): row["importance"] for row in _rows(
        [{"statement": text, "importance": "REQUIRED", "evidence": text} for text in rule], "LLM", JD)}
    assert rule == model
