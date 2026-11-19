"""R135 P0-1: a reviewed question must carry its legal candidate_id so the model can select it.

Zero model HTTP. The tool never invents a source: it only surfaces the mapping
between a frozen question ref and the candidate that already exists in this
session's candidate set for the current decision.
"""
from interview_api.modules.interview.main_harness import (
    MainHarness, SearchReviewedExperiences, ReviewedExperienceArguments)


def action(topics=()):
    return SearchReviewedExperiences(kind="tool", name="search_reviewed_interview_experiences",
        arguments=ReviewedExperienceArguments(topics=list(topics), max_items=3))


def payload():
    return {
        "snapshot": {
            "public_questions": [
                {"id": 2, "question": "agent 修复代码的效果是怎么评判的？", "dimension": "Agent 评测"},
                {"id": 3, "question": "项目里还有其它 agent 吗？", "dimension": "多 Agent 架构"},
            ],
            "reviewed_experience_scope": {"version": "REVIEWED_EXPERIENCE_SCOPE_V1", "items": [
                {"experience_ref": "experience:1", "round": "一面", "reviewed_at": "2026-11-18",
                 "reliability": 0.72, "source_url": "https://example.com/1", "topics": ["agent"],
                 "question_refs": ["public_question:2", "public_question:3"]}]},
        },
        "adaptive": {
            "candidates": [
                {"candidate_id": "C_AGENT_PQ2", "topic": "agent", "strategies": ["NEXT"],
                 "source": {"ref": "public_question:2", "source_type": "PUBLIC_QUESTION"}},
                {"candidate_id": "C_AGENT_SKILL", "topic": "agent", "strategies": ["NEXT"],
                 "source": {"ref": "skill:ai_agent_application@1.0.1", "source_type": "DYNAMIC_GENERATED"}},
                # A reviewed ref with no legal candidate must NOT get a fabricated id.
            ],
        },
    }


def test_reviewed_question_surfaces_its_legal_candidate_id():
    result = MainHarness._reviewed_experiences(None, action(), payload())
    assert result["status"] == "OK"
    rows = result["data"]["items"][0]["questions"]
    assert rows[0] == dict(ref="public_question:2", text="agent 修复代码的效果是怎么评判的？",
                           dimension="Agent 评测", candidate_id="C_AGENT_PQ2")
    # No legal candidate for public_question:3 -> no invented mapping.
    assert "candidate_id" not in rows[1]


def test_candidate_id_requires_next_strategy():
    p = payload()
    p["adaptive"]["candidates"][0]["strategies"] = ["FOLLOW_UP"]
    result = MainHarness._reviewed_experiences(None, action(), p)
    rows = result["data"]["items"][0]["questions"]
    assert "candidate_id" not in rows[0]
