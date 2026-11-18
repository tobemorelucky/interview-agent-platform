"""R130: the interviewer decides when to switch, and may mix three shapes.

Zero model HTTP. Model text is scripted; the Controller, the decision space and
the topic gate are the production ones. The #630 shape is reproduced as a
reversal case: a project question on Redis, one clarification, a second answer
that adds nothing — the controller must withdraw further follow-ups on that
narrow point instead of spending every remaining slot on it.
"""
import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.interview.adaptive import (
    ScopedAdaptiveSelection, planning_payload, resolve_selection, validate_decision)
from interview_api.modules.interview.adaptive_prompts import (
    MAIN_INTERVIEWER_V9, MAIN_INTERVIEWER_V10, freeze_prompts)
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.models import (
    InterviewContextSnapshot, InterviewSession, InterviewSessionQuestion)
from interview_api.modules.interview.schemas import CreateSessionRequest
from r125_support import ScriptedEvaluation, ScriptedInterviewer, seed_job, seed_persona
from test_r125_conversation import JAVA_FACTS, JAVA_JD, JAVA_RESUME
from v21_support import selection_artifact

STALL_PLAN = [
    dict(strategy="NEXT", topic="redis", source="CANDIDATE_PROFILE",
         text="你简历里那个订单秒杀系统，库存前置校验这块是怎么设计的？比如扣减放在哪一步。"),
    dict(strategy="FOLLOW_UP", topic="redis", reason="MISSING_MECHANISM",
         text="库存这个 key 在 Redis 里具体怎么存？脚本里判断和扣减是怎么写的？"),
    # The third slot goes to a different, job-related engineering scenario
    # instead of a third wording of the same stock-key question.
    dict(strategy="NEXT", topic="kafka_reliability", reason="COVERAGE_SWITCH",
         text="如果活动开始后 Kafka 消息消费积压，你会先看哪些指标？"),
]
STALL_RULES = [
    dict(match="Lua", state="PARTIALLY_DEMONSTRATED", score=3,
         evaluation="原子性和限购说到了，库存 key 的结构和扣减没讲。",
         missing=["库存 key 结构与扣减"], missing_mechanisms=["库存 key 结构与扣减"]),
    dict(match="排名", state="INSUFFICIENT_EVIDENCE", score=1,
         evaluation="没有回答库存数量和扣减逻辑。",
         missing=["库存数量的存储与扣减"], missing_mechanisms=["库存数量存储"]),
    dict(match="", state="PARTIALLY_DEMONSTRATED", score=3,
         evaluation="提到了消费延迟指标。", missing=[], missing_mechanisms=[]),
]
STALL_ANSWERS = [
    "我用 Lua 脚本做前置拦截，还用 Set 做了一人一单。",
    "就是各自算各自的，按排名来。",
    "先看 lag 和分区堆积情况。",
]

RICH_PLAN = [
    dict(strategy="NEXT", topic="redis", source="CANDIDATE_PROFILE",
         text="你简历里那个订单秒杀系统，库存这块你实际负责了哪部分？"),
    dict(strategy="FOLLOW_UP", topic="redis", reason="MISSING_MECHANISM",
         text="扣减那一步具体怎么保证原子性？中间失败了一次会怎样？"),
    dict(strategy="NEXT", topic="kafka_reliability", reason="COVERAGE_SWITCH",
         text="那消息这块，Kafka 重复消费你们一般怎么处理？"),
    dict(strategy="NEXT", topic="mysql_consistency", reason="COVERAGE_SWITCH",
         text="换个基础一点的问题：MySQL 的事务隔离级别，你平时怎么选？"),
]
RICH_RULES = [
    dict(match="Lua", state="PARTIALLY_DEMONSTRATED", score=4,
         evaluation="讲到了预校验和原子扣减，失败窗口还可以更细。",
         missing=["失败窗口的处理"], missing_mechanisms=["失败窗口处理"]),
    dict(match="WATCH", state="PARTIALLY_DEMONSTRATED", score=4,
         evaluation="补充了事务边界，取舍说得比较清楚。",
         missing=["回滚后的补偿"], missing_mechanisms=[]),
    dict(match="", state="PARTIALLY_DEMONSTRATED", score=3,
         evaluation="给出了基本思路。", missing=[], missing_mechanisms=[]),
]
RICH_ANSWERS = [
    "库存扣减我用了 Redis 的 Lua 脚本，前面有预校验，热点商品预热到本地。",
    "脚本里先用 WATCH 看库存，再扣减，失败会重试一次；重试还失败就走异步补偿。",
    "重复消费用订单号做幂等键，先去重表插一条，冲突就跳过。",
    "一般默认用可重复读，业务上需要读已提交的场景会单独调。",
]


class RecordingInterviewer(ScriptedInterviewer):
    """Keep the exact payload each decision was made from."""

    def __init__(self, plan):
        super().__init__(plan)
        self.payloads = []

    async def generate(self, messages, *, schema):
        # Record what the model actually sees: the bounded wire DTO.
        from r125_support import _decode
        self.payloads.append(_decode(json.loads(messages[-1]["content"])["input"]))
        return await super().generate(messages, schema=schema)


def _with_db(url, fn):
    async def run():
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await fn(factory)
        finally:
            await engine.dispose()
    return asyncio.run(run())


async def _session(factory, plan, rules, question_count):
    uid = await seed_persona(factory, JAVA_RESUME, JAVA_FACTS)
    job = await seed_job(factory, "Java 后端开发工程师", JAVA_JD)
    main, evaluation = RecordingInterviewer(list(plan)), ScriptedEvaluation(list(rules))
    core = MockInterviewService(factory, ModelRouter(
        {"interview_main": main, "answer_evaluation": evaluation}))
    session = await core.create(uid, CreateSessionRequest(
        company_id=job["company_id"], job_id=job["job_id"],
        interviewer_skill_id="java_backend", adaptive_interview=True,
        question_count=question_count))
    return core, uid, session, main, evaluation


async def _payload(factory, session_id, *, opening=False):
    """The real planning payload. ``opening=True`` is the pre-question state,
    where every target may still legitimately be chosen by NEXT."""
    async with factory() as db:
        session = await db.get(InterviewSession, session_id)
        snapshot = await db.get(InterviewContextSnapshot, session.context_snapshot_id)
        questions = list((await db.scalars(select(InterviewSessionQuestion).where(
            InterviewSessionQuestion.session_id == session_id).order_by(
            InterviewSessionQuestion.question_index))).all())
        await db.refresh(snapshot)
        if opening:
            return planning_payload(snapshot.context_json, [], None, session.interview_plan_json)
        return planning_payload(snapshot.context_json, questions, questions[-1],
                                session.interview_plan_json)


def test_prompt_v30_states_shapes_and_thread_pacing():
    assert MAIN_INTERVIEWER_V9.version == "2.9.0"  # frozen, never rewritten
    assert MAIN_INTERVIEWER_V10.version == "3.0.0"
    system = MAIN_INTERVIEWER_V10.system
    for marker in ("QUESTION SHAPES.", "THREAD PACING.", "adaptive.thread",
                   "question_shapes", "stalled=true", "prefer_switch=true"):
        assert marker in system, marker
    assert "Never work through" in system and "as a checklist" in system
    frozen = freeze_prompts(context_projection=True, gap_scope=True, evaluation_boundaries=True)
    assert frozen["interview_main"]["version"] == "3.0.0"
    assert frozen["answer_evaluation"]["version"] == "2.3.4"


def test_stalled_redis_thread_switches_instead_of_asking_a_third_wording(database_url):
    """The #630 reversal: root project question + one clarification + no progress."""
    async def run(factory):
        core, uid, session, main, evaluation = await _session(factory, STALL_PLAN, STALL_RULES, 3)
        sid = session["id"]
        first = await core.start(sid, uid)
        assert first["question_kind"] != "INTRODUCTION"
        await core.answer(sid, uid, first["question_id"], uuid4().hex, STALL_ANSWERS[0])
        detail = await core.detail(sid, uid)
        second_q = detail["current_question"]["question_id"]
        await core.answer(sid, uid, second_q, uuid4().hex, STALL_ANSWERS[1])

        # The decision after the second (stalled) answer is payloads[2].
        assert len(main.payloads) == 3, [len(p["history"]) for p in main.payloads]
        stalled_payload = main.payloads[2]
        thread = stalled_payload["adaptive"]["thread"]
        assert thread["follow_ups_used"] == 1
        assert thread["latest_answer_added_information"] is False
        assert thread["stalled"] is True
        assert thread["root_question_id"] == first["question_id"]
        # The narrowed thread is no longer offered at all, but other targets are.
        assert not [c for c in stalled_payload["adaptive"]["candidates"]
                    if c["target_knowledge_id"].endswith(":redis")]
        assert any("NEXT" in c["strategies"] and c["topic"] == "kafka_reliability"
                   for c in stalled_payload["adaptive"]["candidates"])

        async with factory() as db:
            questions = list((await db.scalars(select(InterviewSessionQuestion).where(
                InterviewSessionQuestion.session_id == sid).order_by(
                InterviewSessionQuestion.question_index))).all())
        assert [q.question_kind for q in questions] == ["NEXT", "FOLLOW_UP", "NEXT"]
        assert questions[2].parent_question_id is None
        assert "Kafka" in questions[2].question
        # No paid call was added to work around a stalled thread: three questions
        # were generated, and only the two answered ones were evaluated.
        assert len(main.calls) == 3
        assert len(evaluation.calls) == 2
    _with_db(database_url, run)


def test_sufficient_answer_keeps_the_thread_open_for_a_real_deep_dive(database_url):
    async def run(factory):
        core, uid, session, main, _ = await _session(factory, RICH_PLAN + [
            dict(strategy="NEXT", topic="java", reason="COVERAGE_SWITCH",
                 text="再问个 Java 基础：线程池的核心参数你怎么定？")], RICH_RULES, 5)
        sid = session["id"]
        first = await core.start(sid, uid)
        await core.answer(sid, uid, first["question_id"], uuid4().hex, RICH_ANSWERS[0])
        detail = await core.detail(sid, uid)
        await core.answer(sid, uid, detail["current_question"]["question_id"],
                          uuid4().hex, RICH_ANSWERS[1])

        stalled_payload = main.payloads[2]
        thread = stalled_payload["adaptive"]["thread"]
        # The first clarification produced a partially demonstrated answer, so the
        # thread is not stalled and further depth is still legal.
        assert thread["latest_answer_added_information"] is True
        assert thread["stalled"] is False
        redis = [c for c in stalled_payload["adaptive"]["candidates"]
                 if c["target_knowledge_id"].endswith(":redis")]
        assert any("FOLLOW_UP" in c["strategies"] for c in redis)
    _with_db(database_url, run)


def test_question_shapes_follow_the_frozen_source(database_url):
    async def run(factory):
        core, uid, session, main, _ = await _session(factory, RICH_PLAN, RICH_RULES, 5)
        await core.start(session["id"], uid)
        candidates = main.payloads[0]["adaptive"]["candidates"]
        profile = [c for c in candidates if c["source"]["source_type"] == "CANDIDATE_PROFILE"]
        project = [c for c in profile if "PROJECT" in c["question_shapes"]]
        skills = [c for c in profile if "PROJECT" not in c["question_shapes"]]
        requirement = [c for c in candidates if c["source"]["source_type"] == "JOB_REQUIREMENT"]
        generic = [c for c in candidates if c["source"]["source_type"] == "DYNAMIC_GENERATED"]
        assert project and all(set(c["question_shapes"]) == {"PROJECT", "SCENARIO"} for c in project)
        assert skills and all(set(c["question_shapes"]) == {"FUNDAMENTALS"} for c in skills)
        assert requirement and all(set(c["question_shapes"]) == {"FUNDAMENTALS", "SCENARIO"}
                                   for c in requirement)
        assert generic and all(set(c["question_shapes"]) == {"FUNDAMENTALS", "SCENARIO"}
                               for c in generic)
    _with_db(database_url, run)


def test_shape_switch_does_not_trip_the_topic_gate_but_real_drift_still_does(database_url):
    """A project→scenario extension and a fundamentals question stay legal."""
    async def run(factory):
        core, uid, session, main, _ = await _session(factory, RICH_PLAN, RICH_RULES, 5)
        sid = session["id"]
        # Before the first question every target is legally reachable by NEXT.
        payload = await _payload(factory, sid, opening=True)
        candidates = payload["adaptive"]["candidates"]

        def artifact_for(text, target_suffix, source_type):
            candidate = next(c for c in candidates
                             if c["target_knowledge_id"].endswith(target_suffix)
                             and c["source"]["source_type"] == source_type)
            selection = dict(target_knowledge_id=candidate["target_knowledge_id"],
                question_source_ref=candidate["source"]["ref"], strategy="NEXT",
                supporting_refs=[candidate["source"]["ref"]],
                reason_tags=["COVERAGE_SWITCH"],
                proposed_question=dict(question=text, expected_points=["机制", "取舍"],
                    dimension=candidate["label"], difficulty=payload["desired_difficulty"]))
            resolved = selection_artifact(selection, payload, scoped=True)
            return resolve_selection(ScopedAdaptiveSelection(**resolved), payload)

        # Project thread extended into a scenario that also names a fallback store.
        scenario = artifact_for(
            "如果活动开始后 Redis 主从切换，MySQL 里那批库存怎么兜底不被重复扣？",
            ":redis", "CANDIDATE_PROFILE")
        assert validate_decision(scenario, payload)["target_topic"] == "redis"

        # A fundamentals question from the job requirement, without repeating "Redis".
        basics = artifact_for(
            "缓存击穿和缓存穿透分别是什么场景，你会怎么处理？", ":redis", "JOB_REQUIREMENT")
        assert validate_decision(basics, payload)["target_topic"] == "redis"

        # A generic Skill source has no frozen wording: drifting to Kafka is refused.
        drifted = artifact_for("Kafka 的重复消费你们一般怎么处理？", ":redis", "DYNAMIC_GENERATED")
        with pytest.raises(AppError) as refused:
            validate_decision(drifted, payload)
        assert refused.value.code == "ADAPTIVE_TOPIC_MISMATCH"
    _with_db(database_url, run)


def test_project_thread_can_move_to_fundamentals_of_the_same_subject(database_url):
    """Project → fundamentals, same target, different frozen source.

    The follow-up budget is spent on the project thread, but the subject is not
    finished: the interviewer may still ask a job-related basics question about
    the same thing instead of being forced onto an unrelated topic.
    """
    plan = [
        dict(strategy="NEXT", topic="redis", source="CANDIDATE_PROFILE",
             text="你简历里那个订单秒杀系统，库存扣减你实际是怎么做的？"),
        dict(strategy="FOLLOW_UP", topic="redis", reason="MISSING_MECHANISM",
             text="扣减脚本里判断库存和写回是一步还是两步？中间断了会怎样？"),
        dict(strategy="NEXT", topic="redis", source="JOB_REQUIREMENT", reason="COVERAGE_SWITCH",
             text="聊个基础一点的：Redis 的 RDB 和 AOF 分别解决什么问题，你会怎么选？"),
    ]
    rules = [
        dict(match="Lua", state="PARTIALLY_DEMONSTRATED", score=4,
             evaluation="讲到了原子扣减。", missing=["失败窗口"], missing_mechanisms=["失败窗口"]),
        dict(match="脚本", state="PARTIALLY_DEMONSTRATED", score=4,
             evaluation="补了脚本步骤。", missing=["线上兜底"], missing_mechanisms=["线上兜底"]),
        dict(match="", state="PARTIALLY_DEMONSTRATED", score=3,
             evaluation="说明了对账补偿。", missing=[], missing_mechanisms=[]),
    ]
    answers = [
        "库存扣减我用 Lua 脚本一次做完，前面还有预校验。",
        "脚本里先取库存再扣减，写回是同一步；中间断了会重试。",
    ]

    async def run(factory):
        core, uid, session, main, _ = await _session(factory, plan, rules, 3)
        sid = session["id"]
        first = await core.start(sid, uid)
        qid = first["question_id"]
        for answer in answers:
            await core.answer(sid, uid, qid, uuid4().hex, answer)
            detail = await core.detail(sid, uid)
            if detail.get("current_question"):
                qid = detail["current_question"]["question_id"]

        async with factory() as db:
            questions = list((await db.scalars(select(InterviewSessionQuestion).where(
                InterviewSessionQuestion.session_id == sid).order_by(
                InterviewSessionQuestion.question_index))).all())
        assert [q.question_kind for q in questions] == ["NEXT", "FOLLOW_UP", "NEXT"]
        basics = questions[2]
        target = (basics.candidate_refs_json or {}).get("adaptive_decision") or {}
        assert target.get("target_knowledge_id", "").endswith(":redis")
        assert target.get("question_source_ref", "").startswith("requirement:")
        assert basics.parent_question_id is None
        assert "AOF" in basics.question
    _with_db(database_url, run)
