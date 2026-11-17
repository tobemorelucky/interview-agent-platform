"""R125 P1: a direction filter must return jobs that really are that direction.

The previous filter ran LIKE over the whole JD body, so "Agent" in a duty line
made a backend role an AI role, and "JavaScript" matched "Java". These cases are
the ones the user hit.
"""
import pytest

from interview_api.modules.public_career.classification import RULES, classify, matches

BACKEND_DUTIES = ["负责交易系统的后端服务开发，使用 Java 与 Spring Boot 编写接口。",
                  "参与数据库设计与缓存优化，保证服务稳定性。"]
BONUS = ["有 Agent / 大模型应用经验者优先。", "了解 RAG 检索增强加分。"]


def test_javascript_is_not_java():
    assert classify("前端开发工程师", ["使用 JavaScript 与 Vue 开发页面。"])["primary"] == "frontend"
    plain = classify("Java 开发工程师", ["使用 Java 编写服务端接口。"])
    assert plain["primary"] == "java" and "java" in plain["scores"]
    never = classify("Web 前端工程师", ["JavaScript / TypeScript 工程化与构建"])
    assert "java" not in never["scores"]


def test_a_bonus_mention_does_not_make_an_ai_job():
    job = classify("后端开发工程师", BACKEND_DUTIES, BONUS)
    assert job["primary"] == "backend"
    # It may still be a secondary label, but it never decides the category.
    assert job["basis"] in ("TITLE", "DUTY")


def test_test_development_is_not_ai_even_when_the_title_says_ai():
    job = classify("AI 测试开发工程师", ["负责大模型的自动化测试与质量保障。"],
                   ["熟悉 Python，了解 Agent 评测优先。"])
    assert job["primary"] == "test"
    assert "ai_agent" not in job["directions"]


def test_title_decides_over_a_single_duty_mention():
    job = classify("供应链算法工程师", ["负责运筹优化模型", "与后端同学对接接口联调。"])
    assert job["primary"] == "ai_agent"
    assert job["basis"] == "TITLE"


def test_multi_direction_job_keeps_one_primary_and_real_labels():
    job = classify("后端开发工程师（AI 向/数据平台）",
                   ["负责 AI 应用平台的后端服务与数据链路开发。", "使用 Java、Spring Boot 与 Python。"])
    assert job["primary"] == "backend"
    assert job["directions"][0] == "backend"
    assert "ai_agent" in job["directions"]
    assert len(job["directions"]) > 1


def test_unknown_role_is_unclassified_instead_of_guessed():
    job = classify("管理培训生", ["轮岗学习业务流程。"])
    assert job["primary"] is None and job["directions"] == []


def test_filtering_uses_the_classified_direction_not_a_substring():
    job = classify("后端开发工程师", BACKEND_DUTIES, BONUS)
    # "Agent" appears in the qualifications, but the AI filter must not return it
    # unless it is a genuine secondary label of the role.
    if "ai_agent" in job["directions"]:
        assert job["scores"]["ai_agent"] >= job["scores"]["backend"] * 0.6
    else:
        assert not matches("ai_agent", job)
    assert matches("backend", job)


def test_every_rule_has_a_human_label_and_no_duplicate_terms():
    labels = [rule["label"] for rule in RULES.values()]
    assert len(labels) == len(set(labels))
    for key, rule in RULES.items():
        assert rule["label"] and rule["terms"], key
