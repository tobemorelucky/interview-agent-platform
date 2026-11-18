"""R132: the reviewed-experience tool reads only the frozen exact-job scope."""
from interview_api.modules.interview.main_harness import (
    MainHarness, SearchReviewedExperiences, ReviewedExperienceArguments,
)


def action(topics=(), max_items=3):
    return SearchReviewedExperiences(kind="tool", name="search_reviewed_interview_experiences",
        arguments=ReviewedExperienceArguments(topics=list(topics), max_items=max_items))


def payload(items=None):
    return {
        "snapshot": {
            "public_questions": [
                {"id": 52, "question": "Redis 库存扣减怎么防超卖？", "dimension": "缓存"},
                {"id": 53, "question": "RAG 的召回和重排怎么做的？", "dimension": "检索"},
            ],
            "reviewed_experience_scope": {"version": "REVIEWED_EXPERIENCE_SCOPE_V1",
                "items": items if items is not None else [
                    {"experience_ref": "experience:20", "round": "一面", "reviewed_at": "2026-10-05",
                     "reliability": 0.75, "source_url": "https://example.com/20",
                     "topics": ["redis"], "question_refs": ["public_question:52"]},
                    {"experience_ref": "experience:21", "round": "二面", "reviewed_at": "2026-10-06",
                     "reliability": 0.8, "source_url": "https://example.com/21",
                     "topics": ["rag_retrieval"], "question_refs": ["public_question:53"]},
                ]},
        },
        "adaptive": {"candidates": [{"topic": "redis"}, {"topic": "rag_retrieval"}]},
    }


def test_returns_only_matching_reviewed_items():
    result = MainHarness._reviewed_experiences(None, action(["redis"]), payload())
    assert result["status"] == "OK" and result["code"] == "REVIEWED_EXPERIENCES"
    assert [i["experience_ref"] for i in result["data"]["items"]] == ["experience:20"]
    assert result["source_refs"] == ["public_question:52", "experience:20"]
    assert result["trust"] == "UNTRUSTED_DATA"


def test_unknown_topic_is_refused():
    result = MainHarness._reviewed_experiences(None, action(["frontend"]), payload())
    assert result["status"] == "ERROR" and result["code"] == "TOPIC_OUT_OF_SCOPE"


def test_empty_scope_falls_back_without_error():
    result = MainHarness._reviewed_experiences(None, action(), payload([]))
    assert result["status"] == "EMPTY" and result["code"] == "NO_REVIEWED_EXPERIENCE_FOR_SCOPE"


def test_item_and_question_caps():
    items = [{"experience_ref": f"experience:{i}", "round": "一面", "reviewed_at": None,
              "reliability": 0.7, "source_url": "https://example.com",
              "topics": ["redis"], "question_refs": ["public_question:52"]} for i in range(5)]
    result = MainHarness._reviewed_experiences(None, action(max_items=3), payload(items))
    assert len(result["data"]["items"]) == 3
    assert len(result["data"]["items"][0]["questions"]) == 1


def test_non_listed_question_ref_is_not_returned():
    items = [{"experience_ref": "experience:20", "round": "一面", "reviewed_at": None,
              "reliability": 0.7, "source_url": "https://example.com",
              "topics": ["redis"], "question_refs": ["public_question:999"]}]
    result = MainHarness._reviewed_experiences(None, action(), payload(items))
    assert result["data"]["items"][0]["questions"] == []
