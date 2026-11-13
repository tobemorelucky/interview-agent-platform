"""Round 19: a job title must not outrank the requirements it contradicts.

Six control settings (A-F) change one variable at a time: title, requirements,
their agreement, their conflict, word containment, and a title with nothing else.
"""
import json
from types import SimpleNamespace

from interview_api.modules.interview.skill_recommendation import (
    MIN_CONTENT, POLICY_VERSION, contains, project_target_recommendation, recommend,
)

AI_TITLE = "AI应用工程师"
JAVA_TITLE = "Java后端工程师"
BANK_TITLE = "科技菁英"
JAVA_REQUIREMENTS = ["熟练掌握 Java 与 Spring Boot", "熟悉 Kafka 与 Redis 缓存"]
AI_REQUIREMENTS = ["熟悉 RAG 检索增强与智能体开发", "具备大模型应用开发能力"]


def job(title, requirements=(), responsibilities=(), job_family=None, tags=()):
    return dict(job_id=1, jd_id=2, title=title, job_family=job_family,
                requirements=[dict(id=index + 1, statement=text) for index, text in enumerate(requirements)],
                responsibilities=list(responsibilities), tags=list(tags))


def by_id(result):
    return {item["skill_id"]: item for item in result["ranking"]}


def test_a_same_title_with_different_requirements_changes_the_answer():
    ai = recommend(job(AI_TITLE, AI_REQUIREMENTS))
    java = recommend(job(AI_TITLE, JAVA_REQUIREMENTS))
    assert ai["recommended_skill_id"] == "ai_agent_application"
    assert java["recommended_skill_id"] == "java_backend"
    # The title alone gave the AI skill 8 points in both runs; it was not enough.
    assert by_id(java)["ai_agent_application"]["title_points"] == 8
    assert by_id(java)["ai_agent_application"]["content_points"] < MIN_CONTENT


def test_b_same_requirements_with_different_titles_keep_the_same_answer():
    answers = {recommend(job(title, JAVA_REQUIREMENTS))["recommended_skill_id"]
               for title in (AI_TITLE, JAVA_TITLE, BANK_TITLE)}
    assert answers == {"java_backend"}


def test_c_title_and_requirements_agree():
    result = recommend(job(JAVA_TITLE, JAVA_REQUIREMENTS))
    java = by_id(result)["java_backend"]
    assert result["status"] == "RECOMMENDED" and result["auto_select"]
    assert java["title_points"] == 8 and java["content_points"] == 15
    assert java["ranking_points"] == 23


def test_d_title_conflicting_with_requirements_is_not_auto_selected():
    conflicting = [
        "熟练掌握 Java，熟悉 Spring Boot 与常见服务端框架",
        "熟悉 Kafka 消息队列与 Redis 缓存的使用与问题排查",
        "了解大模型应用的基本概念（如 RAG、向量检索），有调用大模型接口的经验者优先",
    ]
    result = recommend(job(AI_TITLE, conflicting))
    assert result["status"] == "AMBIGUOUS" and not result["auto_select"]
    assert result["recommended_skill_id"] is None
    assert result["decision"] == dict(min_score=9, min_content=MIN_CONTENT, min_margin=3,
                                      leader_by_points="ai_agent_application",
                                      leader_by_content="java_backend",
                                      ambiguity_reason="TITLE_CONFLICTS_WITH_REQUIREMENTS")
    assert "标题不能代替要求文本" in result["reason"]
    ranked = by_id(result)
    assert ranked["ai_agent_application"]["content_points"] < ranked["java_backend"]["content_points"]


def test_e_a_longer_registered_term_is_not_credited_to_another_skill():
    """'向量数据库' is the AI skill's term; the bank rules must not match '数据库'."""
    result = recommend(job("研发工程师", ["熟悉向量数据库与检索链路"]))
    bank, ai = by_id(result)["bank_it"], by_id(result)["ai_agent_application"]
    assert bank["ranking_points"] == 0 and bank["evidence"] == []
    assert any(hit["term"] == "向量数据库" for hit in ai["evidence"])
    # The same Skill's own hypernym still counts: 大模型 inside 大模型应用.
    hypernym = recommend(job("研发工程师", ["具备大模型应用开发能力"]))
    assert by_id(hypernym)["ai_agent_application"]["content_points"] == 9


def test_f_a_title_alone_is_never_enough():
    empty = recommend(job(AI_TITLE))
    assert empty["status"] == "INSUFFICIENT_DATA" and not empty["auto_select"]
    duties_only = recommend(job(AI_TITLE, responsibilities=["参与团队协作与需求沟通"]))
    assert duties_only["status"] == "NO_APPLICABLE_SKILL" and not duties_only["auto_select"]
    assert duties_only["decision"]["ambiguity_reason"] == "TITLE_ONLY_LEADER"
    assert "不按标题自动选择" in duties_only["reason"]


def test_scores_are_integer_rule_points_not_confidence():
    result = recommend(job(AI_TITLE, AI_REQUIREMENTS))
    for item in result["ranking"]:
        assert isinstance(item["ranking_points"], int)
        assert item["ranking_points"] == item["content_points"] + item["title_points"]
        assert 0 <= item["ranking_points"] <= 40
    assert "confidence" not in json.dumps(result).lower()
    assert result["policy_version"] == POLICY_VERSION


def test_only_registered_skills_can_be_recommended():
    result = recommend(job(AI_TITLE, AI_REQUIREMENTS))
    assert {item["skill_id"] for item in result["ranking"]} == {
        "ai_agent_application", "bank_it", "java_backend"}
    assert all(item["version"] for item in result["ranking"])


def test_longer_terms_only_suppress_a_different_skills_short_term():
    assert contains("熟悉向量数据库", "数据库") is True          # raw text match still exists
    result = recommend(job("研发工程师", ["熟悉向量数据库"]))
    assert by_id(result)["bank_it"]["ranking_points"] == 0      # but it is not credited
    assert contains("熟悉 Java 与 JVM", "Java") is True
    assert contains("JavaScript 不是后端语言", "Java") is False  # ASCII boundary unchanged


def _target(requirements, *, title=AI_TITLE, duties=()):
    return SimpleNamespace(
        id=7, version=3, parse_status="CONFIRMED", confirmed_at=None,
        content_fingerprint="content", job_title=title,
        structured_json={"responsibilities": list(duties)},
        requirements_json=[dict(id=index + 1, statement=text, evidence=text, topic=None, source="LLM")
                           for index, text in enumerate(requirements)])


def test_conflict_projection_asks_for_a_manual_choice_and_separates_reasons_from_quotes():
    target = _target([
        "熟练掌握 Java，熟悉 Spring Boot 与常见服务端框架",
        "熟悉 Kafka 消息队列与 Redis 缓存的使用与问题排查",
        "了解大模型应用的基本概念（如 RAG、向量检索）",
    ])
    view = project_target_recommendation(target, recommend(dict(
        job_id=None, title=target.job_title, job_family=None, jd_id=None, jd_fingerprint="content",
        responsibilities=[], requirements=[dict(id=r["id"], statement=r["statement"])
                                          for r in target.requirements_json], tags=[])))
    assert view["status"] == "AMBIGUOUS" and view["auto_select"] is False
    assert view["recommended_skill_id"] is None and view["needs_manual_confirmation"] is True
    assert view["ambiguity_reason"] == "TITLE_CONFLICTS_WITH_REQUIREMENTS"
    assert view["content_threshold"] == MIN_CONTENT
    assert "不自动选择" in view["message"]
    for candidate in view["candidates"]:
        assert candidate["reason_kind"] == "RULE_MATCH_EXPLANATION"
        assert candidate["ranking_points"] == candidate["content_points"] + candidate["title_points"]
        for evidence in candidate["evidence"]:
            # A rule explanation is never presented as a JD quote.
            assert evidence["evidence_status"] != "RULE_MATCH_EXPLANATION"
            assert evidence["matched_term"]
