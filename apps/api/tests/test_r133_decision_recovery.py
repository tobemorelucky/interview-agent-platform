"""R133: a refused question must not kill the turn, and the review tool must work.

Zero model HTTP. Every decision, gate, Controller transaction and tool read is
the production one; only the model text is scripted.
"""
import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
from interview_api.modules.experience.models import (
    ExperienceAgentRun, ExperienceAgentStepRun, InterviewExperience, InterviewQuestion)
from interview_api.modules.interview.adaptive import (
    RECOVERABLE_DECISION_CODES, ScopedAdaptiveSelection, gap_scope_diagnostic, resolve_selection,
    validate_decision)
from interview_api.modules.interview.main_harness import MainHarnessAction
from interview_api.modules.interview.topics import topic_terms
from test_gap_scope_contract import interview_fixture
from test_m4_interview import run_with_database

DIMENSIONS = ("technical_correctness", "coverage", "depth", "relevance", "communication")
ANSWER = "我用 Redis Lua 扣库存，再用唯一索引兜底，压测峰值一万 QPS。"


class Scripted(ModelProvider):
    """Play one scripted action per step; evaluation leaves a real open gap."""

    provider_name, model_name = "replay", "R133_SCRIPTED"

    def __init__(self, decide):
        self.decide, self.calls = decide, []

    async def generate(self, messages, *, schema):
        packet = json.loads(messages[-1]["content"])["input"]
        self.calls.append(schema["title"])
        if schema["title"] == "AdaptiveEvaluation":
            kid = packet["adaptive"]["active_knowledge_id"]
            value = dict(**{key: 2 for key in DIMENSIONS}, evaluation="只讲了单点做法，未展开机制。",
                covered_points=["Redis 扣减"], missing_points=["未说明超卖兜底"],
                factual_or_reasoning_issues=[], follow_up_focus=["说明并发下的兜底"],
                next_action_recommendation="FOLLOW_UP", reference_answer=None,
                knowledge_observations=[dict(knowledge_id=kid, state="PARTIALLY_DEMONSTRATED",
                    answer_quotes=[ANSWER[:20]], missing_mechanisms=["并发下的库存兜底机制未说明"],
                    resolved_gap_ids=[])])
            return ModelResponse(json.dumps(value, ensure_ascii=False),
                dict(input_tokens=0, output_tokens=0, total_tokens=0))
        return ModelResponse(json.dumps(self.decide(packet, len(self.calls)), ensure_ascii=False),
            dict(input_tokens=0, output_tokens=0, total_tokens=0))


def candidate_for(packet, *, strategy, source_ref=None, target=None):
    for row in packet["adaptive"]["candidates"]:
        if strategy not in row["strategies"]:
            continue
        if source_ref and row["source"]["ref"] != source_ref:
            continue
        if target and row["target_knowledge_id"] != target:
            continue
        return row
    raise AssertionError(f"no candidate for {strategy} {source_ref} {target}")


def selection(packet, row, *, strategy, focus=(), question=None):
    label = packet["adaptive"]["targets"][row["target_knowledge_id"]]["label"]
    topic = packet["adaptive"]["targets"][row["target_knowledge_id"]]["topic"]
    term = (topic_terms(topic) or [label])[0]
    refs = [row["source"]["ref"]]
    if packet["adaptive"].get("current_answer_ref"):
        refs.append(packet["adaptive"]["current_answer_ref"])
    return dict(candidate_id=row["candidate_id"], strategy=strategy, supporting_refs=refs,
        reason_tags=["OPENING" if strategy == "NEXT" else "MISSING_MECHANISM"],
        focus_gap_ids=list(focus),
        proposed_question=dict(question=question or f"{term} 这块你在项目里具体怎么落地的？",
            expected_points=["实际做法", "一次真实取舍"], dimension=label,
            difficulty=packet["desired_difficulty"]))


def final(packet, row, **kwargs):
    return dict(kind="final", selection=selection(packet, row, **kwargs))


def test_gap_scope_rejection_is_recoverable_and_diagnosable():
    """The strict rule stays; the turn must not be marked terminal."""
    assert "INVALID_QUESTION_GAP_SCOPE" in RECOVERABLE_DECISION_CODES
    payload = {"snapshot": {"adaptive_policy": {"gap_scope_contract": "QUESTION_GAP_SCOPE_V1",
                                                "decision_contract": "TARGET_SOURCE_PAIR_V1"}},
        "adaptive": {"active_knowledge_id": "ai_agent_application:agent",
            "knowledge_state": {"items": {"ai_agent_application:agent": {"state": "PARTIALLY_DEMONSTRATED",
                "open_gaps": [{"id": "gap_one", "text": "并发兜底未说明"}]}}},
            "choices": [{"target": {"knowledge_id": "ai_agent_application:agent",
                "topic": "agent", "label": "Agent 状态管理",
                "sources": [{"ref": "requirement:1", "source_type": "JOB_REQUIREMENT", "source_id": 1}]},
                "strategies": ["FOLLOW_UP", "DEEP_DIVE"]}],
            "candidates": [{"candidate_id": "C_1", "target_knowledge_id": "ai_agent_application:agent",
                "topic": "agent", "label": "Agent 状态管理", "strategies": ["FOLLOW_UP", "DEEP_DIVE"],
                "source": {"ref": "requirement:1", "source_type": "JOB_REQUIREMENT", "source_id": 1}}],
            "allowed_refs": ["requirement:1", "answer:1"],
            "current_answer_ref": "answer:1", "current_evaluation_ref": "evaluation_run:1"}}
    artifact = resolve_selection(ScopedAdaptiveSelection(
        candidate_id="C_1", strategy="FOLLOW_UP", supporting_refs=["requirement:1", "answer:1"],
        reason_tags=["MISSING_MECHANISM"], focus_gap_ids=[],
        proposed_question=dict(question="并发下库存怎么兜底？", expected_points=["兜底"],
            dimension="库存", difficulty="MEDIUM")), payload)
    with pytest.raises(AppError) as error:
        validate_decision(artifact, payload)
    assert error.value.code == "INVALID_QUESTION_GAP_SCOPE"
    assert error.value.diagnostic["open_gap_ids"] == ["gap_one"]
    assert error.value.diagnostic["focus_gap_ids"] == []
    # This synthetic packet still carries the gaps inline; the harness projection
    # keeps them on demand, which is reported as such rather than guessed.
    assert error.value.diagnostic["gap_ids_in_projection"] == "INLINE"
    projected = gap_scope_diagnostic(artifact, {"knowledge_state": {"items": {
        "ai_agent_application:agent": {"state": "PARTIALLY_DEMONSTRATED", "open_gap_count": 1}}}},
        {"gap_one": {}})
    assert projected["gap_ids_in_projection"] == "ON_DEMAND_ONLY"


def test_harness_schema_exposes_the_review_search_tool():
    schema = json.dumps(MainHarnessAction.model_json_schema(), ensure_ascii=False)
    assert "search_reviewed_interview_experiences" in schema
    assert "max_items" in schema and "topics" in schema


def test_refused_follow_up_recovers_without_a_second_main_call(database_url):
    def decide(packet, n):
        if n == 1:
            return final(packet, candidate_for(packet, strategy="NEXT"), strategy="NEXT")
        row = candidate_for(packet, strategy="FOLLOW_UP",
                            target=packet["adaptive"]["active_knowledge_id"])
        return final(packet, row, strategy="FOLLOW_UP")  # empty focus with open gaps

    async def run(factory):
        replay = Scripted(decide)
        uid, core, request = await interview_fixture(factory, replay, 3)
        sid = (await core.create(uid, request.model_copy(update={"main_harness": True})))["id"]
        opening = await core.start(sid, uid)
        events = dict(await core.answer(sid, uid, opening["question_id"], "r133-gap", ANSWER))
        assert "question" in events and "evaluation" in events
        assert events["question"]["question_id"] != opening["question_id"]
        # Exactly one evaluation and one Main decision for this answer.
        assert replay.calls == ["MainHarnessAction", "AdaptiveEvaluation", "MainHarnessAction"]
        async with factory() as db:
            from interview_api.modules.audit.models import AuditLog
            assert len((await db.scalars(select(AuditLog).where(
                AuditLog.action == "INVALID_QUESTION_GAP_SCOPE", AuditLog.resource_id == str(sid)))).all()) == 1
            run_row = await db.scalar(select(ExperienceAgentRun).where(
                ExperienceAgentRun.graph_name == "interview_main_harness").order_by(
                ExperienceAgentRun.id.desc()).limit(1))
            assert run_row.status == "FAILED" and run_row.error_message == "INVALID_QUESTION_GAP_SCOPE"
            # Not terminal: a later turn may still use this answer's evaluation.
            assert (run_row.output_json or {}).get("terminal") is False
        detail = await core.detail(sid, uid)
        assert detail["status"] == "IN_PROGRESS"
    asyncio.run(run_with_database(database_url, run))


async def _seed_reviewed(factory, company_id, job_id, question_text):
    async with factory() as db:
        experience = InterviewExperience(company_id=company_id, job_id=job_id,
            company="R133 验收公司", position="R133 验收岗位", interview_round="一面",
            summary=question_text, content_text=question_text, source_url="https://example.com/r133",
            platform="nowcoder", reliability_score=0.8, review_status="APPROVED",
            quality_flags_json=[], tags_json=[])
        db.add(experience)
        await db.flush()
        question = InterviewQuestion(experience_id=experience.id, company_id=company_id, job_id=job_id,
            question=question_text, standard_answer="召回与重排的实际做法", category="检索",
            difficulty="MEDIUM", should_index=True, review_status="APPROVED",
            index_status="INDEXED", tags_json=[], routing_json={})
        db.add(question)
        await db.commit()
        return experience.id, question.id


def test_model_can_search_reviewed_experience_and_use_the_result(database_url):
    holder = {}

    def decide(packet, n):
        if n == 1:
            return final(packet, candidate_for(packet, strategy="NEXT"), strategy="NEXT")
        feedback = packet["tool_feedback"]
        if not feedback:
            return {"kind": "tool", "name": "search_reviewed_interview_experiences", "arguments": {}}
        result = feedback[0]["result"]
        assert result["status"] == "OK" and result["code"] == "REVIEWED_EXPERIENCES"
        assert result["trust"] == "UNTRUSTED_DATA"
        ref = result["data"]["items"][0]["questions"][0]["ref"]
        holder["ref"] = ref
        row = candidate_for(packet, strategy="NEXT", source_ref=ref)
        return final(packet, row, strategy="NEXT", question="RAG 的召回环节你们实际怎么做的？")

    async def run(factory):
        replay = Scripted(decide)
        uid, core, request = await interview_fixture(factory, replay, 3)
        await _seed_reviewed(factory, request.company_id, request.job_id, "RAG 的召回和重排怎么做的？")
        sid = (await core.create(uid, request.model_copy(update={"main_harness": True})))["id"]
        opening = await core.start(sid, uid)
        events = dict(await core.answer(sid, uid, opening["question_id"], "r133-tool", ANSWER))
        assert holder["ref"].startswith("public_question:")
        assert events["question"]["source_question_id"] == int(holder["ref"].split(":")[1])
        async with factory() as db:
            tool = await db.scalar(select(ExperienceAgentStepRun).where(
                ExperienceAgentStepRun.step_name == "main_harness_tool").order_by(
                ExperienceAgentStepRun.id.desc()).limit(1))
            assert tool.output_json["tool"] == "search_reviewed_interview_experiences"
            assert tool.output_json["result"]["status"] == "OK"
            run_row = await db.scalar(select(ExperienceAgentRun).where(
                ExperienceAgentRun.graph_name == "interview_main_harness",
                ExperienceAgentRun.status == "SUCCEEDED").order_by(ExperienceAgentRun.id.desc()).limit(1))
            audit = run_row.output_json["tool_audit"]
            assert audit["reviewed_search_called"] is True
            assert audit["used_tool_result"] is True
    asyncio.run(run_with_database(database_url, run))


def test_empty_reviewed_scope_returns_empty_and_interview_continues(database_url):
    def decide(packet, n):
        if n == 1:
            return final(packet, candidate_for(packet, strategy="NEXT"), strategy="NEXT")
        feedback = packet["tool_feedback"]
        if not feedback:
            return {"kind": "tool", "name": "search_reviewed_interview_experiences", "arguments": {}}
        assert feedback[0]["result"]["status"] == "EMPTY"
        assert feedback[0]["result"]["code"] == "NO_REVIEWED_EXPERIENCE_FOR_SCOPE"
        # Move to another goal: re-asking the opening wording is correctly refused.
        row = next(c for c in packet["adaptive"]["candidates"] if "NEXT" in c["strategies"]
                   and c["target_knowledge_id"] != packet["adaptive"]["active_knowledge_id"])
        return final(packet, row, strategy="NEXT")

    async def run(factory):
        replay = Scripted(decide)
        uid, core, request = await interview_fixture(factory, replay, 3)
        sid = (await core.create(uid, request.model_copy(update={"main_harness": True})))["id"]
        opening = await core.start(sid, uid)
        events = dict(await core.answer(sid, uid, opening["question_id"], "r133-empty", ANSWER))
        assert "question" in events
        async with factory() as db:
            run_row = await db.scalar(select(ExperienceAgentRun).where(
                ExperienceAgentRun.graph_name == "interview_main_harness",
                ExperienceAgentRun.status == "SUCCEEDED").order_by(ExperienceAgentRun.id.desc()).limit(1))
            audit = run_row.output_json["tool_audit"]
            assert audit["reviewed_search_called"] is True and audit["used_tool_result"] is False
    asyncio.run(run_with_database(database_url, run))


def test_direct_final_never_requires_a_tool_call(database_url):
    async def run(factory):
        replay = Scripted(lambda packet, n: final(packet, candidate_for(packet, strategy="NEXT"),
                                                  strategy="NEXT"))
        uid, core, request = await interview_fixture(factory, replay, 3)
        await _seed_reviewed(factory, request.company_id, request.job_id, "RAG 的召回和重排怎么做的？")
        sid = (await core.create(uid, request.model_copy(update={"main_harness": True})))["id"]
        await core.start(sid, uid)
        async with factory() as db:
            run_row = await db.scalar(select(ExperienceAgentRun).where(
                ExperienceAgentRun.graph_name == "interview_main_harness").order_by(
                ExperienceAgentRun.id.desc()).limit(1))
            audit = run_row.output_json["tool_audit"]
            assert audit["tool_called"] is False and audit["used_preloaded_without_tool"] is False
    asyncio.run(run_with_database(database_url, run))


def test_reported_topics_are_frozen_into_the_session_scope():
    """The scope builder expected reported_topics; the snapshot never supplied it."""
    from types import SimpleNamespace
    from interview_api.modules.interview.context import reported_topic_anchors, reviewed_experience_scope
    experience = SimpleNamespace(extraction_output_json={"source_anchors": {"reported_topics": [
        {"fragment_id": "ev_1", "kind": "REPORTED_TOPIC", "raw_excerpt": "又问了一些 RAG 召回的问题"}]}})
    anchors = reported_topic_anchors(experience)
    assert anchors == [dict(fragment_id="ev_1", kind="REPORTED_TOPIC", raw_excerpt="又问了一些 RAG 召回的问题")]
    scope = reviewed_experience_scope({"public_questions": [
        {"id": 7, "question": "讲讲你们的检索链路", "experience_id": 1}],
        "interview_experiences": [dict(id=1, round="一面", reviewed_at=None, reliability=0.7,
            source_url="https://example.com/1", reported_topics=anchors)]})
    assert scope["items"][0]["question_refs"] == ["public_question:7"]
    assert "rag_retrieval" in scope["items"][0]["topics"]
