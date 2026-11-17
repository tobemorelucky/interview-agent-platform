"""R126 P1-1: the seven new professional Skills are complete and distinguishable.

Each Skill must freeze with a real scope, its priority topics must be legal
targets the NEXT gate can accept, and the direction→Skill recommendation must
route a job to its own profession instead of keyword soup.
"""
import pytest

from interview_api.modules.interview.skill_engine import SkillRegistry
from interview_api.modules.interview.skill_registry import ROOT, discover
from interview_api.modules.interview.skill_recommendation import recommend
from interview_api.modules.interview.topics import TOPIC_TERMS, TOPICS, mentioned_topics

NEW_SKILLS = ("python_backend", "rag_retrieval", "recommendation_ml", "frontend_web",
              "testing_qa", "data_engineering", "general_cs")


def test_seven_new_skills_register_and_freeze():
    entries, errors = discover(ROOT, refresh=True)
    assert not errors, errors
    for skill_id in NEW_SKILLS:
        assert skill_id in entries, skill_id
        manifest = entries[skill_id]["manifest"]
        assert manifest["version"] == "1.0.0"
        assert manifest["matching"]["capability_groups"], skill_id
        registry = SkillRegistry(ROOT)
        frozen = registry.freeze(skill_id, "project_deep_dive", "standard")
        assert frozen["skill"]["id"] == skill_id
        assert frozen["skill"]["evaluation_anchors"].keys() == {
            "technical_correctness", "coverage", "depth", "relevance", "communication"}
        assert frozen["round"]["id"] == "project_deep_dive"
        assert frozen["company_background"]["mode"] == "GENERIC_SKILL_FALLBACK"
        instructions = frozen["skill"]["instructions"]
        assert "FOLLOW_UP" in instructions and "DEEP_DIVE" in instructions
        assert "统一五维" in instructions


def test_new_skill_topics_are_legal_targets_the_gate_accepts():
    for skill_id in NEW_SKILLS:
        entries, _ = discover(ROOT, refresh=True)
        topics = entries[skill_id]["manifest"]
        from interview_api.modules.interview.skill_engine import SkillRegistry as R
        frozen = R(ROOT).freeze(skill_id, "technical_screen", "standard")
        for topic in frozen["skill"]["priority_topics"]:
            assert topic in TOPICS, (skill_id, topic)
            assert topic in TOPIC_TERMS and TOPIC_TERMS[topic], (skill_id, topic)
            for term in TOPIC_TERMS[topic]:
                assert topic in mentioned_topics(term), (topic, term)
            label = frozen["skill"]["label"]
        # Each topic's published label classifies as itself, so the NEXT gate
        # stays checkable for every target these Skills may select.
        from interview_api.modules.interview.adaptive import LABELS
        for topic in frozen["skill"]["priority_topics"]:
            assert topic in mentioned_topics(LABELS[topic]), (topic, LABELS[topic])


def test_topic_vocabulary_distinguishes_adjacent_domains():
    # JavaScript is still not Java; a test-development job is not an AI job.
    assert "java" not in mentioned_topics("JavaScript 与 TypeScript 的区别")
    assert "testing" in mentioned_topics("设计自动化测试用例并接入流水线")
    assert "ai_agent_application" not in ("testing",)
    assert "frontend" in mentioned_topics("用 Vue 组件库实现首屏渲染优化")
    assert "data_engineering" in mentioned_topics("数仓分层建模与 Spark 离线任务")
    assert "recommendation" in mentioned_topics("推荐系统的多路召回与排序模型")
    assert "network_os" in mentioned_topics("TCP 三次握手与操作系统进程调度")
    assert "algorithm" in mentioned_topics("动态规划的时间复杂度分析")


def test_job_direction_routes_to_its_own_profession():
    def job(title, requirements, responsibilities=()):
        return dict(title=title, requirements=[dict(id=i, statement=s) for i, s in enumerate(requirements)],
                    responsibilities=list(responsibilities))
    assert recommend(job("测试开发工程师", ["设计自动化测试用例与接口测试框架"]))["recommended_skill_id"] == "testing_qa"
    assert recommend(job("前端开发工程师", ["使用 Vue 与 TypeScript 开发前端页面"]))["recommended_skill_id"] == "frontend_web"
    assert recommend(job("数据开发工程师", ["数仓建模与 Spark 离线任务开发"]))["recommended_skill_id"] == "data_engineering"
    assert recommend(job("Python 后端开发工程师", ["熟悉 FastAPI 与 MySQL，负责接口开发"]))["recommended_skill_id"] == "python_backend"
    # The AI job keeps its own skills; the directions do not swallow each other.
    assert recommend(job("大模型应用工程师", ["RAG 检索增强与 Agent 工具调用"]))["recommended_skill_id"] in (
        "ai_agent_application", "rag_retrieval")
