"""R126 P0-1/P0-2: the user can end the interview, and an introduction opens it.

Zero model HTTP: wording is scripted, every decision still goes through the
real session state, the Controller and the frozen contracts.
"""
import asyncio

import pytest
from sqlalchemy import select

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.interview.artifacts import DIMENSIONS
from interview_api.modules.experience.models import ExperienceAgentRun
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.end_intent import end_intent, end_intent_detail
from interview_api.modules.interview.models import (
    InterviewAnswer, InterviewEvaluation, InterviewMessage, InterviewSession, InterviewSessionQuestion)
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview_report.statistics import deterministic_statistics, interview_records
from r125_support import ScriptedEvaluation, ScriptedInterviewer, seed_job, seed_persona

from test_r125_conversation import JAVA_ANSWERS, JAVA_FACTS, JAVA_JD, JAVA_PLAN, JAVA_RESUME, JAVA_RULES

END_TEXT = "能提前结束面试吗"


def test_end_intent_detector_is_narrow():
    for text in ("结束面试", "能提前结束面试吗", "我想结束", "不想继续了", "停止面试",
                 "面试就到这里吧", "可以结束了", "咱们结束吧"):
        assert end_intent(text), text
    for text in ("不知道", "这题不会", "我不会这题", "我不想继续这个话题了", "换个话题吧",
                 "项目最后因为时间原因结束了，后来我们做了复盘",
                 "Redis 的分布式锁是用 setnx 加过期时间实现的，超时之后锁会被释放",
                 "面试官您好，我叫小明，做过秒杀系统"):
        assert not end_intent(text), text
    detail = end_intent_detail(END_TEXT)
    assert detail["matched"] and detail["chars"] == len(END_TEXT)


async def _java_session(factory, question_count=6):
    user_id = await seed_persona(factory, JAVA_RESUME, JAVA_FACTS)
    job = await seed_job(factory, "Java 后端开发工程师", JAVA_JD)
    main, evaluator = ScriptedInterviewer(list(JAVA_PLAN)), ScriptedEvaluation(list(JAVA_RULES))
    core = MockInterviewService(factory, ModelRouter({"interview_main": main, "answer_evaluation": evaluator}))
    session = await core.create(user_id, CreateSessionRequest(company_id=job["company_id"],
        job_id=job["job_id"], interviewer_skill_id="java_backend", adaptive_interview=True,
        question_count=question_count))
    return core, user_id, session, main, evaluator


async def _reload(db, session_id):
    session_row = await db.get(InterviewSession, session_id)
    questions = list((await db.scalars(select(InterviewSessionQuestion).where(
        InterviewSessionQuestion.session_id == session_id).order_by(InterviewSessionQuestion.question_index))).all())
    evaluations = list((await db.scalars(select(InterviewEvaluation).where(
        InterviewEvaluation.session_id == session_id))).all())
    return session_row, questions, evaluations


def _with_db(database_url, fn):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await fn(factory)
        finally:
            await engine.dispose()
    return asyncio.run(run())


def test_introduction_opens_and_the_first_technical_question_follows(database_url):
    async def run(factory):
        core, user_id, session, main, evaluator = await _java_session(factory)
        first = await core.start(session["id"], user_id, opening="INTRODUCTION")
        assert first["question_kind"] == "INTRODUCTION"
        assert "自我介绍" in first["question"]
        async with factory() as db:
            _, questions, _ = await _reload(db, session["id"])
        assert questions[0].excluded_reason == "INTRODUCTION"
        assert not main.calls, "the scripted opening must not need the main agent"
        evaluator_calls = len(evaluator.calls)
        # The candidate introduces themselves; the interviewer opens from the project.
        events = dict(await core.answer(session["id"], user_id, first["question_id"], "intro-1",
                                        "我叫小明，主要做过一个订单秒杀系统，负责库存和链路设计。"))
        assert "question" in events, events
        assert len(evaluator.calls) == evaluator_calls, "an introduction must not be graded"
        # A project opening does not need the topic named literally.
        assert "秒杀" in events["question"]["question"]
        async with factory() as db:
            questions, evaluations = await interview_records(db, session["id"])
            stats = deterministic_statistics(questions, evaluations)
        assert stats["evaluated_count"] == 0
        assert stats["excluded_count"] == 1
        assert stats["excluded_reasons"] == ["INTRODUCTION"]
        assert questions[0].question_kind == "INTRODUCTION"
    _with_db(database_url, run)


def test_user_end_settles_idempotently_and_excludes_the_introduction(database_url):
    async def run(factory):
        core, user_id, session, main, evaluator = await _java_session(factory)
        first = await core.start(session["id"], user_id, opening="INTRODUCTION")
        events = dict(await core.answer(session["id"], user_id, first["question_id"], "intro-1",
                                        "我叫小明，主要做过一个订单秒杀系统，负责库存和链路设计。"))
        second_id = events["question"]["question_id"]
        await core.answer(session["id"], user_id, second_id, "a-1", JAVA_ANSWERS[0])
        result = await core.end(session["id"], user_id)
        completion = result["completion"]
        assert completion["completion_reason"] == "USER_ENDED"
        assert completion["ended_by"] == "USER"
        assert completion["early_terminated"] is True
        # One graded technical answer; the introduction stays out of the numbers.
        assert completion["answered_count"] == 1 and completion["evaluated_count"] == 1
        async with factory() as db:
            session_row, questions, _ = await _reload(db, session["id"])
        assert session_row.status == "COMPLETED" and session_row.active_question_id is None
        assert len(questions) == 3, "ending must not generate or invent questions"
        assert questions[-1].status == "SKIPPED", "the pending question is skipped, not answered"
        assert questions[0].excluded_reason == "INTRODUCTION"
        repeat = await core.end(session["id"], user_id)
        assert repeat["already_completed"] is True
        assert repeat["completion"]["answered_count"] == 1
        async with factory() as db:
            audits = (await db.scalars(select(InterviewMessage).where(
                InterviewMessage.session_id == session["id"]))).all()
            _, questions_after, _ = await _reload(db, session["id"])
        assert len(questions_after) == 3
        assert any("面试已结束" in row.content for row in audits)
    _with_db(database_url, run)


def test_end_request_is_excluded_not_scored(database_url):
    """The #625 shape: an end request already stored and evaluated, then settled."""
    from datetime import datetime, timezone
    async def run(factory):
        core, user_id, session, main, evaluator = await _java_session(factory)
        first = await core.start(session["id"], user_id, opening="INTRODUCTION")
        events = dict(await core.answer(session["id"], user_id, first["question_id"], "intro-1",
                                        "我叫小明，做过一个订单秒杀系统。"))
        first_next = events["question"]["question_id"]
        events = dict(await core.answer(session["id"], user_id, first_next, "a-1", JAVA_ANSWERS[0]))
        second_next = events["question"]["question_id"]
        assert second_next != first_next
        events = dict(await core.answer(session["id"], user_id, second_next, "a-2", JAVA_ANSWERS[1]))
        active = (events.get("follow_up") or events["question"])["question_id"]
        async with factory() as db:
            run_row = ExperienceAgentRun(agent_role="ScriptedEvaluation", graph_name="answer_evaluation",
                                         graph_version="1.0.0", status="FAILED",
                                         started_at=datetime.now(timezone.utc),
                                         finished_at=datetime.now(timezone.utc))
            db.add(run_row)
            await db.flush()
            now = datetime.now(timezone.utc)
            message = InterviewMessage(session_id=session["id"], role="USER", content=END_TEXT,
                                       metadata_json={"question_id": active}, turn_index=9)
            db.add(message)
            await db.flush()
            answer = InterviewAnswer(session_id=session["id"], session_question_id=active,
                                     message_id=message.id, idempotency_key="legacy-end", status="COMPLETED")
            db.add(answer)
            await db.flush()
            artifact = dict(evaluation="完全离题", covered_points=[], missing_points=[],
                            factual_or_reasoning_issues=[], follow_up_focus=[])
            db.add(InterviewEvaluation(session_id=session["id"], session_question_id=active,
                                       answer_id=answer.id, artifact_json=artifact,
                                       **{key: 1 for key in DIMENSIONS},
                                       provider="modelscope", model="legacy", prompt_version="2.3.4",
                                       structured_retry=0, token_usage_json={}, agent_run_id=run_row.id))
            await db.commit()
        result = await core.end(session["id"], user_id)
        assert any(row["reason"] == "END_REQUEST" for row in result["excluded"])
        async with factory() as db:
            session_row, questions, evaluations = await _reload(db, session["id"])
            messages = list((await db.scalars(select(InterviewMessage).where(
                InterviewMessage.session_id == session["id"]))).all())
        excluded_question = next(q for q in questions if q.id == active)
        assert excluded_question.excluded_reason == "END_REQUEST"
        excluded_evaluation = next(e for e in evaluations if e.answer_id == answer.id)
        assert excluded_evaluation.excluded_reason == "END_REQUEST"
        # The transcript is intact: nothing was deleted or rewritten.
        assert END_TEXT in [row.content for row in messages]
        scores = [(e.technical_correctness, e.coverage, e.depth, e.relevance, e.communication)
                  for e in evaluations if e.id == excluded_evaluation.id]
        assert scores == [(1, 1, 1, 1, 1)], "the stored evaluation is preserved, not rewritten"
        async with factory() as db:
            questions_records, evaluations_records = await interview_records(db, session["id"])
        stats = deterministic_statistics(questions_records, evaluations_records)
        assert all(e["session_question_id"] != active for e in evaluations_records)
        assert stats["excluded_count"] == 2  # introduction + end request
        assert stats["excluded_reasons"] == ["END_REQUEST", "INTRODUCTION"]
        completion = session_row.completion_json or {}
        assert completion["completion_reason"] == "USER_ENDED" and completion["early_terminated"] is True
        assert completion["answered_count"] == 2, "the two real technical answers are still counted"
    _with_db(database_url, run)


def test_end_intent_answer_never_reaches_evaluation(database_url):
    async def run(factory):
        core, user_id, session, main, evaluator = await _java_session(factory)
        first = await core.start(session["id"], user_id, opening="INTRODUCTION")
        events = dict(await core.answer(session["id"], user_id, first["question_id"], "intro-1",
                                        "我叫小明，做过一个订单秒杀系统。"))
        active = events["question"]["question_id"]
        calls_before = len(main.calls) + len(evaluator.calls)
        with pytest.raises(AppError) as error:
            await core.answer(session["id"], user_id, active, "end-1", END_TEXT)
        assert error.value.code == "INTERVIEW_END_INTENT"
        async with factory() as db:
            answers = list((await db.scalars(select(InterviewAnswer).where(
                InterviewAnswer.session_question_id == active))).all())
        assert answers == [], "an end request must not become an answer row"
        assert len(main.calls) + len(evaluator.calls) == calls_before
        # The user can still answer normally afterwards.
        events = dict(await core.answer(session["id"], user_id, active, "real-1", JAVA_ANSWERS[2]))
        assert "evaluation" in events
    _with_db(database_url, run)


def test_end_conflicts_with_an_answer_still_in_flight(database_url):
    from datetime import datetime, timedelta, timezone
    async def run(factory):
        core, user_id, session, main, evaluator = await _java_session(factory)
        await core.start(session["id"], user_id, opening="INTRODUCTION")
        async with factory() as db:
            row = await db.get(InterviewSession, session["id"])
            row.interview_plan_json = {**(row.interview_plan_json or {}), "answer_processing": dict(
                answer_id=999999, stage="evaluating",
                expires_at=(datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat())}
            await db.commit()
        with pytest.raises(AppError) as error:
            await core.end(session["id"], user_id)
        assert error.value.code == "INTERVIEW_ANSWER_RUNNING"
        async with factory() as db:
            row = await db.get(InterviewSession, session["id"])
        assert row.status == "IN_PROGRESS", "a refused end must not change any state"
    _with_db(database_url, run)
