"""R129: off-topic answers are corrected before scoring; dialogue prompt v2.9.0.

Zero model HTTP: the provider is scripted; all state transitions, gates and
Controller transactions are the production ones.
"""
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.interview.answer_relevance import classify
from interview_api.modules.interview.adaptive_prompts import (
    MAIN_INTERVIEWER_V9, freeze_prompts)
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation, InterviewSession
from interview_api.modules.interview.schemas import CreateSessionRequest
from r125_support import ScriptedEvaluation, ScriptedInterviewer, seed_job, seed_persona
from test_r125_conversation import RAG_FACTS, RAG_RESUME

PASTE = """第五章下一轮：ASPCF双视角候选路由，保留A＋Trajectory
## 一、正式研究决定
当前J0/J1已完成Dev验证。J1两域退化，正式停止固定先验参与训练的方向。不运行J1 Test，不调tau或lambda。
## 二、实施方案
保留A＋Trajectory作为第五章主要候选，不修改原Trajectory状态更新；后续实验结论与待办事项另行同步。
""" + "技术计划细节：" + "参数与状态说明；" * 30

JD = ["熟悉 RAG 检索增强与向量检索", "能说明 Agent 工具调用与上下文组织"]

PLAN = [
    dict(strategy="NEXT", topic="rag_retrieval", source="CANDIDATE_PROFILE",
         text="先聊聊你那个智能客服知识库，检索这块你主要负责哪一部分？"),
    dict(strategy="NEXT", topic="agent", source="JOB_REQUIREMENT",
         text="换一个方向：你在 Python 项目里怎么组织 Agent 的工具调用上下文？"),
]

RULES = [dict(match="我负责检索链路", state="PARTIALLY_DEMONSTRATED", score=3,
              evaluation="讲清了职责，但机制仍需展开。", covered=["职责"],
              missing=["上下文如何组装"], missing_mechanisms=["上下文字段来源"])]


def _with_db(url, fn):
    async def run():
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await fn(factory)
        finally:
            await engine.dispose()
    return asyncio.run(run())


def test_relevance_classes_are_conservative():
    off = classify(PASTE, "你好，我们先简单认识一下。请做一个自我介绍", job_title="AI应用开发")
    assert off.kind == "OFF_TOPIC_SUSPECTED" and off.reason == "DOCUMENT_PASTE"
    assert classify("我不知道", "说说 RAG 的召回").kind == "UNKNOWN"
    assert classify("就是 json 上下文信息传给他", "提示里带哪些信息").kind == "VALID"
    assert classify("换个题吧", "当前题目").kind == "USER_CONTROL"
    assert classify("能提前结束面试吗", "当前题目").reason == "END"


def test_prompt_v29_is_a_new_core_not_an_appendix():
    assert MAIN_INTERVIEWER_V9.version == "2.9.0"
    system = MAIN_INTERVIEWER_V9.system
    for marker in ("IDENTITY.", "ASKING.", "HANDLING THE ANSWER.", "CONVERSATION."):
        assert marker in system
    assert "not an online" in system and "one main question" in system
    assert "missing_points" in system and "Self-introduction is only a server-authored option" in system
    frozen = freeze_prompts(context_projection=True, gap_scope=True, evaluation_boundaries=True)
    # R130 froze 3.0.0 for new sessions; the 2.9.0 body stays available as a
    # historical prompt and keeps the same core sections.
    assert frozen["interview_main"]["version"] == "3.0.0"
    assert frozen["answer_evaluation"]["version"] == "2.3.4"


def test_off_topic_answer_is_not_scored_and_reanswer_is_clean(database_url):
    async def run(factory):
        uid = await seed_persona(factory, RAG_RESUME, RAG_FACTS)
        job = await seed_job(factory, "AI 应用开发工程师", JD)
        main, evaluator = ScriptedInterviewer(list(PLAN)), ScriptedEvaluation(list(RULES))
        core = MockInterviewService(factory, ModelRouter(
            {"interview_main": main, "answer_evaluation": evaluator}))
        session = await core.create(uid, CreateSessionRequest(
            company_id=job["company_id"], job_id=job["job_id"],
            interviewer_skill_id="ai_agent_application", adaptive_interview=True,
            question_count=4))
        sid = session["id"]
        first = await core.start(sid, uid)
        assert first["question_kind"] != "INTRODUCTION"  # direct from resume/job
        main_before = len(main.calls)
        with pytest.raises(AppError) as refused:
            await core.answer(sid, uid, first["question_id"], uuid4().hex, PASTE)
        assert refused.value.code == "OFF_TOPIC_CONFIRMATION_REQUIRED"
        assert not evaluator.calls and len(main.calls) == main_before  # no model call for the paste
        async with factory() as db:
            assert not (await db.scalars(select(InterviewEvaluation).where(
                InterviewEvaluation.session_id == sid))).all()
            answer_ids = {str(a.id) for a in (await db.scalars(select(InterviewAnswer).where(
                InterviewAnswer.session_id == sid))).all()}
            logs = (await db.scalars(select(AuditLog).where(
                AuditLog.action == "OFF_TOPIC_CONFIRMATION_REQUIRED",
                AuditLog.resource_id.in_(answer_ids)))).all()
            assert len(logs) == 1
        # New answer for the same question, with an explicit restart flag.
        key = uuid4().hex
        events = dict(await core.answer(sid, uid, first["question_id"], key,
                                   "我负责检索链路，用户提问后把片段交给模型生成。",
                                   restart_after_off_topic=True))
        assert "evaluation" in events and "question" in events
        async with factory() as db:
            answers = (await db.scalars(select(InterviewAnswer).where(
                InterviewAnswer.session_id == sid).order_by(InterviewAnswer.id))).all()
            assert len(answers) == 1
            assert answers[0].idempotency_key == key and answers[0].status == "COMPLETED"
            # The rejected paste remains as a USER message and an AuditLog row.
            from interview_api.modules.interview.models import InterviewMessage
            user_messages = (await db.scalars(select(InterviewMessage).where(
                InterviewMessage.session_id == sid, InterviewMessage.role == "USER"))).all()
            assert any(m.content == PASTE for m in user_messages)
            assert len((await db.scalars(select(InterviewEvaluation).where(
                InterviewEvaluation.session_id == sid))).all()) == 1
        assert evaluator.calls.count("AdaptiveEvaluation") == 1
        assert main.calls.count("AdaptiveSelection") == 2  # opening + re-answer, not the paste
    _with_db(database_url, run)


def test_user_control_intent_is_intercepted_without_scoring(database_url):
    async def run(factory):
        uid = await seed_persona(factory, RAG_RESUME, RAG_FACTS)
        job = await seed_job(factory, "AI 应用开发工程师", JD)
        main, evaluator = ScriptedInterviewer(list(PLAN)), ScriptedEvaluation(list(RULES))
        core = MockInterviewService(factory, ModelRouter(
            {"interview_main": main, "answer_evaluation": evaluator}))
        session = await core.create(uid, CreateSessionRequest(
            company_id=job["company_id"], job_id=job["job_id"],
            interviewer_skill_id="ai_agent_application", adaptive_interview=True,
            question_count=4))
        first = await core.start(session["id"], uid)
        main_before = len(main.calls)
        with pytest.raises(AppError) as refused:
            await core.answer(session["id"], uid, first["question_id"], uuid4().hex, "换个题吧")
        assert refused.value.code == "INTERVIEW_TOPIC_CHANGE"
        async with factory() as db:
            assert not (await db.scalars(select(InterviewAnswer).where(
                InterviewAnswer.session_id == session["id"]))).all()
        assert not evaluator.calls and len(main.calls) == main_before
    _with_db(database_url, run)
