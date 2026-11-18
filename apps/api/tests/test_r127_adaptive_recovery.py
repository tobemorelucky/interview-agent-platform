"""R127: source-grounded NEXT wording, zero-charge recovery, natural opening.

Zero model HTTP: every model text is scripted, but decisions, gates and the
Controller are the production ones. The old bug paid for Evaluation, lost the
artifact when Main's NEXT was rejected, and charged both calls again on retry.
"""
import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.interview.adaptive import (
    AdaptiveSelection, ScopedAdaptiveSelection, fallback_selection, planning_payload,
    resolve_selection, validate_decision)
from interview_api.modules.interview.adaptive_prompts import (
    MAIN_CONVERSATIONAL_V2, MAIN_CONVERSATIONAL_V3, MAIN_CONVERSATIONAL_V4, freeze_prompts)
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.models import (
    InterviewAgentCall, InterviewAnswer, InterviewEvaluation, InterviewMessage,
    InterviewSession, InterviewSessionQuestion)
from interview_api.modules.interview.schemas import CreateSessionRequest
from r125_support import ScriptedEvaluation, ScriptedInterviewer, seed_job, seed_persona
from test_r125_conversation import RAG_FACTS, RAG_RESUME

JD = [
    "熟悉Java/Python/Go之一，了解Spring Boot或FastAPI",
    "熟悉 RAG 检索增强与向量检索",
    "能围绕真实项目说明技术取舍",
]

# The first question is *natural* Chinese bound to the frozen Python
# requirement: no literal Python/GIL/协程 token, no other technical topic.
NATIVE_NEXT = "你之前做过后端接口吗？讲讲一次请求从进来到返回大概怎么流转，先不用报参数名。"
# Natural, source-bound wording for the *next* legal target (RAG).
NATIVE_RAG_NEXT = "你们做知识库问答时，一般怎么判断回答有没有依据？"
# A genuinely off-target question: it names Python while claiming RAG.
DRIFTED_NEXT = "换个话题，Python 的 GIL 和协程在实际项目里怎么配合？"

RULES = [dict(match="", state="DEMONSTRATED_IN_SESSION", score=4,
              evaluation="讲清了自己负责的部分和整体流向。",
              covered=["整体流向"], missing=[], missing_mechanisms=[])]

PLAN = [
    dict(strategy="NEXT", topic="python", source="JOB_REQUIREMENT", text=NATIVE_NEXT),
    dict(strategy="NEXT", topic="rag_retrieval", source="JOB_REQUIREMENT", text=DRIFTED_NEXT),
    dict(strategy="NEXT", topic="agent", text="再聊聊 Agent 的工具选择。"),
]


def _with_db(url, fn):
    async def run():
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await fn(factory)
        finally:
            await engine.dispose()
    return asyncio.run(run())


async def _session(factory, question_count=6):
    uid = await seed_persona(factory, RAG_RESUME, RAG_FACTS)
    job = await seed_job(factory, "AI 应用开发工程师", JD)
    main, evaluation = ScriptedInterviewer(list(PLAN)), ScriptedEvaluation(list(RULES))
    core = MockInterviewService(factory, ModelRouter(
        {"interview_main": main, "answer_evaluation": evaluation}))
    session = await core.create(uid, CreateSessionRequest(
        company_id=job["company_id"], job_id=job["job_id"],
        interviewer_skill_id="ai_agent_application", adaptive_interview=True,
        question_count=question_count))
    return core, uid, session, main, evaluation


def test_prompt_v37_adds_dialogue_without_editing_frozen_v36():
    assert MAIN_CONVERSATIONAL_V3.version == "2.7.0"
    assert MAIN_CONVERSATIONAL_V3.system.startswith(MAIN_CONVERSATIONAL_V2.system)
    assert "FOLLOW THE ANSWER, NOT A SCRIPT" in MAIN_CONVERSATIONAL_V3.system
    assert "SOURCE-BOUND QUESTIONS" in MAIN_CONVERSATIONAL_V3.system
    assert MAIN_CONVERSATIONAL_V4.version == "2.8.0"
    assert "INTERVIEW RHYTHM" in MAIN_CONVERSATIONAL_V4.system
    assert MAIN_CONVERSATIONAL_V2.version == "2.6.0"
    frozen = freeze_prompts(context_projection=True, gap_scope=True, evaluation_boundaries=True)
    assert frozen["interview_main"]["version"] == "3.0.0"
    assert frozen["answer_evaluation"]["version"] == "2.3.4"


def test_natural_source_bound_next_passes_and_real_drift_is_rejected(database_url):
    async def run(factory):
        core, uid, session, main, evaluation = await _session(factory)
        sid = session["id"]
        intro = await core.start(sid, uid, opening="INTRODUCTION")
        assert intro["question_kind"] == "INTRODUCTION"
        q1 = dict(await core.answer(sid, uid, intro["question_id"], uuid4().hex,
                               "我叫小明，做过智能客服知识库，主要负责检索链路。"))
        assert "question" in q1
        # Read the frozen context through the service, never reconstruct JD prose by hand.
        detail = await core.detail(sid, uid)
        snapshot = detail["context_snapshot"]
        async with factory() as db:
            s = await db.get(InterviewSession, sid)
            questions = list((await db.scalars(select(InterviewSessionQuestion).where(
                InterviewSessionQuestion.session_id == sid).order_by(InterviewSessionQuestion.question_index))).all())
            payload = planning_payload(snapshot, questions, questions[-1], s.interview_plan_json,
                                       answer="我用 FastAPI 写过接口", answer_id=9901,
                                       evaluation_run_id=9902)
        candidate = next(c for c in payload["adaptive"]["candidates"]
                         if c["target_knowledge_id"].endswith(":rag_retrieval")
                         and c["source"]["source_type"] == "JOB_REQUIREMENT")

        def artifact_for(text):
            draft = dict(question=text, expected_points=["回答依据"], dimension="检索与召回",
                         difficulty=payload["desired_difficulty"])
            selection = dict(candidate_id=candidate["candidate_id"], strategy="NEXT",
                supporting_refs=[candidate["source"]["ref"], payload["adaptive"]["current_answer_ref"]],
                reason_tags=["COVERAGE_SWITCH"], proposed_question=draft)
            if payload["snapshot"]["adaptive_policy"].get("gap_scope_contract"):
                selection = ScopedAdaptiveSelection(**selection, focus_gap_ids=[])
            else:
                selection = AdaptiveSelection(**selection)
            return resolve_selection(selection, payload)

        # Natural wording, grounded in a frozen requirement: admitted.
        policy = validate_decision(artifact_for(NATIVE_RAG_NEXT), payload)
        assert policy["target_topic"] == "rag_retrieval"
        # The same target with another explicit technical subject: rejected.
        with pytest.raises(AppError) as exc:
            validate_decision(artifact_for(DRIFTED_NEXT), payload)
        assert exc.value.code == "ADAPTIVE_TOPIC_MISMATCH"
        assert "岗位要求开场" not in exc.value.message  # mid-interview recovery option
        assert exc.value.diagnostic["source_grounded"] is True
        assert "python" in exc.value.diagnostic["conflicting_topics"]
        # A zero-model fallback comes from a different frozen source and passes the same gate.
        fallback, fallback_policy = fallback_selection(payload, "ai_agent_application:rag_retrieval")
        assert fallback.target_knowledge_id != "ai_agent_application:python"
        assert validate_decision(fallback, payload) == fallback_policy
        assert "岗位要求里有一条" in fallback.question.question or "简历里提到" in fallback.question.question
    _with_db(database_url, run)


def test_rejected_next_keeps_paid_evaluation_and_uses_fallback(database_url):
    async def run(factory):
        core, uid, session, main, evaluation = await _session(factory)
        sid = session["id"]
        intro = await core.start(sid, uid, opening="INTRODUCTION")
        await core.answer(sid, uid, intro["question_id"], uuid4().hex,
                          "我叫小明，做过智能客服知识库，主要负责检索链路。")
        detail = await core.detail(sid, uid)
        q1 = detail["current_question"]["question_id"]
        events = dict(await core.answer(sid, uid, q1, uuid4().hex,
                                   "我用 FastAPI 写过接口，请求进来先做鉴权再查库。"))
        assert "evaluation" in events and "question" in events
        assert "岗位要求里有一条" in events["question"]["question"] or "简历里提到" in events["question"]["question"]
        # Evaluation ran exactly once; no second Main call for the fallback.
        assert len([c for c in evaluation.calls if c == "AdaptiveEvaluation"]) == 1
        assert main.calls.count("AdaptiveSelection") == 2
        async with factory() as db:
            rows = (await db.scalars(select(InterviewEvaluation).where(
                InterviewEvaluation.session_id == sid))).all()
            assert len(rows) == 1
            answers = (await db.scalars(select(InterviewAnswer).where(
                InterviewAnswer.session_id == sid))).all()
            assert all(a.status == "COMPLETED" for a in answers)
            s = await db.get(InterviewSession, sid)
            assert "answer_recovery" not in (s.interview_plan_json or {})
            AuditLog = __import__("interview_api.modules.audit.models", fromlist=["AuditLog"]).AuditLog
            logs = await db.scalars(select(AuditLog).where(
                AuditLog.action == "ADAPTIVE_TOPIC_MISMATCH", AuditLog.resource_id == str(sid)))
            assert len(logs.all()) == 1
    _with_db(database_url, run)


def test_evaluation_is_recovered_from_ledger_without_another_call(database_url):
    async def run(factory):
        core, uid, session, main, evaluation = await _session(factory)
        sid = session["id"]
        intro = await core.start(sid, uid, opening="INTRODUCTION")
        await core.answer(sid, uid, intro["question_id"], uuid4().hex,
                          "我叫小明，做过智能客服知识库，主要负责检索链路。")
        detail = await core.detail(sid, uid)
        q1 = detail["current_question"]["question_id"]
        await core.answer(sid, uid, q1, uuid4().hex, "我用 FastAPI 写过接口，请求进来先做鉴权再查库。")
        async with factory() as db:
            first = (await db.scalars(select(InterviewEvaluation).where(
                InterviewEvaluation.session_id == sid))).all()[0]
            q = InterviewSessionQuestion(session_id=sid, question_index=99,
                question="恢复检查点用的独立题目", status="ANSWERED", source="DYNAMIC_GENERATED",
                question_kind="NEXT")
            db.add(q)
            await db.flush()
            message = InterviewMessage(session_id=sid, role="USER", content="恢复检查点用的回答",
                turn_index=99, metadata_json={"question_id": q.id})
            db.add(message)
            await db.flush()
            msg = InterviewAnswer(session_id=sid, session_question_id=q.id,
                message_id=message.id, idempotency_key=uuid4().hex, status="FAILED",
                error="ADAPTIVE_TOPIC_MISMATCH")
            db.add(msg)
            await db.flush()
            answer_id = msg.id
            run_row = ExperienceAgentRun(agent_role="AnswerEvaluationAgent", graph_name="mock_interview",
                graph_version="1.0.0", status="SUCCEEDED",
                output_json={"schema": "AdaptiveEvaluation", "validated_artifact": first.artifact_json})
            db.add(run_row)
            await db.flush()
            db.add(InterviewAgentCall(session_id=sid, answer_id=answer_id, agent_run_id=run_row.id))
            db.add(ExperienceAgentStepRun(agent_run_id=run_row.id, step_name="answer_evaluation",
                status="SUCCEEDED", provider_name="replay", model_name="R127_RECOVERY",
                prompt_version="2.3.4", token_usage_json={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}))
            await db.commit()
        recovered = await core.recovered_evaluation(sid, uid, answer_id)
        assert recovered is not None
        assert recovered.run_id == run_row.id
        assert recovered.artifact.model_dump() == first.artifact_json
        assert recovered.usage["total_tokens"] == 2
        # Second call reuses the checkpoint rather than rescanning the ledger.
        again = await core.recovered_evaluation(sid, uid, answer_id)
        assert again.run_id == run_row.id
    _with_db(database_url, run)


def test_scripted_continuous_dialogue_switches_after_sufficient_and_clarifies_vague(database_url):
    """Scripted wording, real decisions: a dialogue *sequence*, not single turns.

    This is a contract/regression check, not evidence of real-model tone; the
    natural-language quality still needs the live interview the user will run.
    """
    async def run(factory):
        uid = await seed_persona(factory, RAG_RESUME, RAG_FACTS)
        job = await seed_job(factory, "AI 应用开发工程师", JD)
        plan = [
            dict(strategy="NEXT", topic="rag_retrieval", source="CANDIDATE_PROFILE",
                 text="先聊聊你那个智能客服知识库，你在这个项目里主要负责哪部分？"),
            dict(strategy="FOLLOW_UP", topic="rag_retrieval", reason="ANSWER_TOO_GENERAL",
                 text=lambda _p: "那检索回来的内容是怎么交给模型生成答案的？"),
            dict(strategy="NEXT", topic="python", source="JOB_REQUIREMENT",
                 text="换一个基础问题：你在 Python 项目里怎么处理过并发？"),
            dict(strategy="NEXT", topic="agent", text="再聊聊 Agent 的工具选择是怎么定的。"),
        ]
        rules = [
            dict(match="我叫小明", state="DEMONSTRATED_IN_SESSION", score=4,
                 evaluation="开场清楚。", covered=["自我介绍"], missing=[], missing_mechanisms=[]),
            dict(match="我就是做了个知识库", state="INSUFFICIENT_EVIDENCE", score=2,
                 evaluation="回答很笼统。", covered=[], missing=["自己负责的范围"],
                 missing_mechanisms=["本人负责的模块"]),
            dict(match="我负责检索链路", state="PARTIALLY_DEMONSTRATED", score=4,
                 evaluation="职责与链路都讲清楚了。", covered=["职责"], missing=[], missing_mechanisms=[]),
            dict(match="用协程", state="PARTIALLY_DEMONSTRATED", score=4,
                 evaluation="基础问题回答充分。", covered=["并发"], missing=[], missing_mechanisms=[]),
        ]
        main, evaluation = ScriptedInterviewer(plan), ScriptedEvaluation(rules)
        core = MockInterviewService(factory, ModelRouter(
            {"interview_main": main, "answer_evaluation": evaluation}))
        session = await core.create(uid, CreateSessionRequest(
            company_id=job["company_id"], job_id=job["job_id"],
            interviewer_skill_id="ai_agent_application", adaptive_interview=True, question_count=6))
        sid = session["id"]
        intro = await core.start(sid, uid, opening="INTRODUCTION")
        assert intro["question_kind"] == "INTRODUCTION"
        actions = []
        qid = intro["question_id"]
        for index, answer in enumerate(("我叫小明，做过智能客服知识库", "我就是做了个知识库",
                                        "我负责检索链路，用户提问后检索片段交给模型生成", "用协程做过并发控制")):
            events = dict(await core.answer(sid, uid, qid, uuid4().hex, answer))
            if index:
                actions.append(events["evaluation"]["action"])
            nxt = events.get("follow_up") or events.get("question")
            assert nxt, events
            qid = nxt["question_id"]
        assert actions == ["FOLLOW_UP", "NEXT", "NEXT"]
        assert "主要负责" in (await core.detail(sid, uid))["questions"][1]["question"]
        assert "Python" in (await core.detail(sid, uid))["questions"][3]["question"]
    _with_db(database_url, run)


def test_r128_introduction_does_not_consume_question_budget(database_url):
    """question_count means scored technical questions, not the opening."""
    async def run(factory):
        core, uid, session, main, evaluation = await _session(factory, question_count=3)
        sid = session["id"]
        intro = await core.start(sid, uid, opening="INTRODUCTION")
        assert intro["question_kind"] == "INTRODUCTION"
        # Intro first: it must not consume the 3-question technical budget.
        await core.answer(sid, uid, intro["question_id"], uuid4().hex,
                          "我叫小明，做过智能客服知识库，主要负责检索链路。")
        detail = await core.detail(sid, uid)
        qid = detail["current_question"]["question_id"]
        assert detail["current_question"]["question_index"] == 1
        await core.answer(sid, uid, qid, uuid4().hex, "我用 FastAPI 写过接口，请求进来先做鉴权再查库。")
        detail = await core.detail(sid, uid)
        assert detail["status"] == "IN_PROGRESS"
        assert detail["current_question"]["question_index"] == 2, [(q["question_id"], q["question_index"], q["status"]) for q in detail["questions"]]
    _with_db(database_url, run)
