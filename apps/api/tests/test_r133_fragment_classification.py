"""R133: source fragment classification for real 面经 formatting.

The Ctrip sample lists questions as bare topic lines; the Kuaishou sample puts
each question in a numbered markdown heading. Both were previously classified
CONTEXT, so an honest extraction could not cite them, while answers and page
chrome had to stay out of the question pool.
"""
import pytest

from interview_api.modules.public_career.models import SourceSnapshot
from interview_api.modules.public_career.research.evidence import EvidenceValidationError
from interview_api.modules.public_career.research.fragments import InterviewSourceFragments
from interview_api.modules.public_career.research.interview import resolve_fragment_extraction
from interview_api.modules.public_career.research.schemas import (
    FragmentInterviewExtraction, FragmentInterviewQuestion)

CTRIP = """# 面试复盘 | 携程提前批Java后端开发一二hr面（已意向）

8.20 一面

项目介绍

spring的启动流程

spring和springboot的区别

线程和进程的区别

分库分表

9.9 收到意向书

全部评论(12)
"""

CAMPUS = """# 快手 AI应用开发 二面

这个是5.11发的offer了,HR面和二面隔了10多天

## 1. 自我介绍

## 2. RAG 有没有解决大模型胡说八道的问题

答案：

RAG 只能缓解幻觉，不能彻底解决幻觉。如果召回错了，模型仍然会基于错误证据生成答案。

## 3. 长期记忆怎么存储，具体存什么东西

剩余60%内容，订阅专栏后可继续查看
"""


def kinds(text):
    catalog = InterviewSourceFragments([SourceSnapshot(id=901, source_id=8, content_text=text)])
    return {catalog.display[key]: catalog.kinds[key] for key in catalog.spans}


def test_bare_topic_lines_in_a_round_are_questions():
    result = kinds(CTRIP)
    for text in ("项目介绍", "spring的启动流程", "分库分表", "线程和进程的区别"):
        assert result[text] == "QUESTION", (text, result[text])


def test_round_headers_status_and_comments_stay_context():
    result = kinds(CTRIP)
    assert result["20 一面"] == "CONTEXT"  # the date prefix is stripped as a list marker
    assert result["9 收到意向书"] == "CONTEXT"
    assert not any("全部评论" in text for text in result)


def test_numbered_markdown_headings_are_questions_but_the_title_is_not():
    result = kinds(CAMPUS)
    assert result["1. 自我介绍"] == "QUESTION"
    assert result["2. RAG 有没有解决大模型胡说八道的问题"] == "QUESTION"
    assert result["3. 长期记忆怎么存储,具体存什么东西"] == "QUESTION"
    assert result["快手 AI应用开发 二面"] == "CONTEXT"


def test_answer_prose_is_never_a_question():
    result = kinds(CAMPUS)
    answer_lines = [text for text in result if text.startswith("RAG 只能缓解幻觉")]
    assert answer_lines and all(result[text] == "ANSWER" for text in answer_lines)
    assert result["答案:"] == "ANSWER"


def test_context_and_answer_cannot_be_cited_as_questions():
    catalog = InterviewSourceFragments([SourceSnapshot(id=902, source_id=8, content_text=CAMPUS)])
    context_key = next(key for key in catalog.spans if catalog.display[key].startswith("RAG 只能缓解"))
    answer_key = next(key for key in catalog.spans if catalog.kinds[key] == "ANSWER")
    for key in (context_key, answer_key):
        with pytest.raises(EvidenceValidationError, match="not an exact reported question"):
            catalog.question_span(key, "QUESTION")


def selected(fragments, *items):
    # Identity is out of scope here: the company/position spans need their own
    # identity evidence, which this fragment-level case does not exercise.
    return FragmentInterviewExtraction(is_interview_experience=True, company=None,
        position=None, round_name="一面", questions=list(items),
        evidence_ids=[items[0].fragment_id], extraction_confidence=0.6)


def test_unsupported_item_is_dropped_and_reported_not_rewritten():
    catalog = InterviewSourceFragments([SourceSnapshot(id=903, source_id=8, content_text=CTRIP)])
    good = next(key for key in catalog.spans if catalog.display[key] == "spring的启动流程")
    bad = next(key for key in catalog.spans if catalog.display[key] == "20 一面")
    extraction, anchors = resolve_fragment_extraction(selected(catalog,
        FragmentInterviewQuestion(fragment_id=bad, kind="QUESTION", normalized_question="一面考了什么？",
                                  question_type="OTHER", confidence=0.4),
        FragmentInterviewQuestion(fragment_id=good, kind="QUESTION", normalized_question="Spring 的启动流程？",
                                  question_type="TECHNICAL", confidence=0.8)), catalog)
    assert [q.question for q in extraction.questions] == ["Spring 的启动流程？"]
    assert len(anchors["rejected_questions"]) == 1
    assert anchors["rejected_questions"][0]["reason"].startswith("Fragment is not an exact reported question")


def test_page_with_no_supported_question_still_fails_loudly():
    catalog = InterviewSourceFragments([SourceSnapshot(id=904, source_id=8, content_text=CTRIP)])
    bad = next(key for key in catalog.spans if catalog.display[key] == "20 一面")
    with pytest.raises(EvidenceValidationError, match="No question is supported"):
        resolve_fragment_extraction(selected(catalog, FragmentInterviewQuestion(
            fragment_id=bad, kind="QUESTION", normalized_question="一面考了什么？",
            question_type="OTHER", confidence=0.4)), catalog)
