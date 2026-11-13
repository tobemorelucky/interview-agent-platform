"""Round 15: does either Coach tool add evidence the projection does not have?

Two scenarios, both built through the *formal* service path (no hand-written
evaluation or memory rows):

B. A long formal evaluation whose decisive mechanism sits beyond V2's clipping
   points. Checks that the detail tool returns only the increment and that it is
   still the frozen row.
C. A legal CONFLICTED state produced by real replay evaluations and
   CapabilityMemoryService, so RECHECK is genuinely allowed.

Plus a deterministic, zero-model-call suggestion baseline built from the same
rule function TrainingService uses. It is an offline comparison, not a product
change: the point is to separate "a structured suggestion is useful" from
"autonomous tool use is necessary".
"""
import asyncio
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.provider import ModelResponse
from interview_api.infrastructure.models.router import (
    ANSWER_EVALUATION_ROUTE, INTERVIEW_MAIN_ROUTE, ModelRouter,
)
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.models import InterviewContextSnapshot, InterviewSession
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.training import modes
from interview_api.modules.interview_report import tools
from interview_api.modules.interview_report.evidence import (
    LIMITS, coach_evidence, evaluation_fingerprint)
from interview_api.modules.interview_report.resolver import resolve_coach_artifact
from interview_api.modules.interview_report.schemas import HarnessCoachArtifact
from interview_api.modules.interview_report.service import InterviewReportService
from interview_api.modules.interview_report.statistics import (
    deterministic_statistics, evaluation_fingerprint as fingerprint_of, interview_records)
from test_coach_harness import response_for
from test_private_recheck import CONFLICT_HISTORY, session_for
from test_job_target import create_target, seed_user
from v22_support import MemoryReplayProvider, kafka_seed

# The decisive fact is placed deliberately past V2's 800-character narrative cap
# and past its 4-item list cap. It flips the conclusion from "mechanism explained"
# to "critical boundary still missing".
DECISIVE = "决定性证据：消费位点必须在幂等事务提交之后手动提交，否则重平衡会重复扣款"
LONG_HEAD = "本轮回答把主链路讲得比较完整，机制表述清晰，追问时也能主动收窄边界。" * 28
LONG_ANSWER = "我按事务与幂等表处理消息投递，先写库再提交，再由投递任务发送。" * 16


class LongEvaluationProvider(MemoryReplayProvider):
    """A replay provider that produces one long, formally validated evaluation."""

    async def generate(self, messages, *, schema):
        if schema["title"] != "AdaptiveEvaluation":
            return await super().generate(messages, schema=schema)
        payload = json.loads(messages[-1]["content"])["input"]
        kid = payload["adaptive"]["active_knowledge_id"]
        answer = payload["answer"]
        gaps = payload["adaptive"]["knowledge_state"]["items"].get(kid, {}).get("open_gaps", [])
        value = dict(
            **{key: 4 for key in ("technical_correctness", "coverage", "depth", "relevance", "communication")},
            evaluation=LONG_HEAD + DECISIVE + "整体仍需在失败恢复与并发边界上补强。",
            covered_points=[f"已覆盖实现要点 {i}" for i in range(6)],
            missing_points=[f"待补边界 {i}" for i in range(5)] + [DECISIVE],
            factual_or_reasoning_issues=[f"推理边界 {i}" for i in range(5)],
            follow_up_focus=[f"追问方向 {i}" for i in range(5)],
            next_action_recommendation="NEXT",
            knowledge_observations=[dict(knowledge_id=kid, state="DEMONSTRATED_IN_SESSION",
                answer_quotes=[answer[:60]], missing_mechanisms=[],
                resolved_gap_ids=[g["id"] for g in gaps])])
        return ModelResponse(json.dumps(value, ensure_ascii=False),
                             dict(input_tokens=0, output_tokens=0, total_tokens=0))


def run(database_url, scenario):
    async def main():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await scenario(factory)
        finally:
            await engine.dispose()
    asyncio.run(main())


async def completed_with_long_evaluation(factory):
    """Create the session, question, answer and evaluation through the service."""
    user_id, ctx = await kafka_seed(factory)
    provider = LongEvaluationProvider()
    core = MockInterviewService(factory, ModelRouter(
        {route: provider for route in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))
    created = await core.create(user_id, CreateSessionRequest(
        company_id=ctx["company_id"], job_id=ctx["job_id"],
        interviewer_skill_id="java_backend", adaptive_interview=True, question_count=1))
    question = await core.start(created["id"], user_id)
    await core.answer(created["id"], user_id, question["question_id"], uuid4().hex, LONG_ANSWER)
    return user_id, created["id"]


async def payload_for(factory, session_id):
    async with factory() as db:
        session = await db.get(InterviewSession, session_id)
        snapshot = await db.get(InterviewContextSnapshot, session.context_snapshot_id)
        questions, evaluations = await interview_records(db, session_id)
    payload, projection = coach_evidence(
        snapshot=snapshot.context_json,
        statistics=deterministic_statistics(questions, evaluations),
        questions=[dict(id=q.id, question=q.question, standard_answer=q.standard_answer,
            source=q.source, question_kind=q.question_kind, status=q.status,
            candidate_refs=q.candidate_refs_json, job_requirement_id=q.job_requirement_id)
            for q in questions],
        evaluations=evaluations, fingerprint=fingerprint_of(evaluations))
    return payload, projection, snapshot, evaluations


# ── The deterministic, zero-model-call suggestion baseline ──

def deterministic_suggestions(training_scope, evaluations):
    """Legal structured training candidates from rules the product already has.

    No model, no tool, no new state machine. State -> mode comes from `modes()`,
    the exact function TrainingService uses. Ranking is fixed, so the result is
    reproducible; ties are broken by knowledge_id rather than model mood.
    """
    rank = {"CONFLICTED": 4, "RECHECK_DUE": 3, "GAP_SUSPECTED": 2, "PRACTICING": 1, "UNKNOWN": 0}
    refs = [e["id"] for e in evaluations][-3:]
    out = []
    for knowledge_id, target in (training_scope or {}).get("targets", {}).items():
        allowed = [m for m in modes(target["state"], target["revoked"]) if m != "FULL"]
        if not allowed:
            continue
        out.append(dict(knowledge_id=knowledge_id, state=target["state"],
            mode=allowed[0], supporting_evaluation_ids=refs,
            skill_id=training_scope.get("skill_id"), scope="CURRENT_JOB_TARGET",
            reason=f"按当前能力状态 {target['state']} 选择一次针对性练习",
            priority=rank.get(target["state"], 0)))
    return sorted(out, key=lambda row: (-row["priority"], row["knowledge_id"]))


def test_deterministic_baseline_needs_no_model_and_stays_legal():
    scope = dict(skill_id="ai_agent_application", targets={
        "ai_agent_application:agent": dict(state="PRACTICING", revoked=False),
        "ai_agent_application:rag_retrieval": dict(state="PRACTICING", revoked=False),
        "ai_agent_application:llm": dict(state="UNKNOWN", revoked=False),
        "ai_agent_application:kafka": dict(state="CONFLICTED", revoked=False),
    })
    candidates = deterministic_suggestions(scope, [dict(id=i) for i in (1, 2, 3, 4)])
    assert [c["mode"] for c in candidates] == ["RECHECK", "WEAKNESS", "WEAKNESS"]
    assert [c["knowledge_id"].rsplit(":", 1)[-1] for c in candidates] == \
        ["kafka", "agent", "rag_retrieval"]
    assert all(c["mode"] in modes(c["state"]) for c in candidates)
    # Both real round-14 ON suggestions fall inside this rule-generated set when
    # the same three PRACTICING topics are present, at zero model calls.
    live = dict(scope, targets={k: v for k, v in scope["targets"].items()
                                if k != "ai_agent_application:kafka"})
    live_candidates = deterministic_suggestions(live, [dict(id=i) for i in (1, 2, 3)])
    assert {"ai_agent_application:agent", "ai_agent_application:rag_retrieval"} <= \
        {c["knowledge_id"] for c in live_candidates}
    assert all(c["mode"] == "WEAKNESS" for c in live_candidates)


# ── Scenario B: a formally clipped evaluation ──

def test_scenario_b_the_detail_tool_returns_only_the_hidden_evidence(database_url):
    """V2 omits a decision-relevant fact; the tool returns exactly that fact."""
    async def scenario(factory):
        user_id, session_id = await completed_with_long_evaluation(factory)
        payload, projection, _, evaluations = await payload_for(factory, session_id)
        row = evaluations[0]
        projected = payload["evaluations"][0]

        assert len(row["evaluation"]) > LIMITS["evaluation"]
        assert DECISIVE not in projected["evaluation"], "V2 must really omit it"
        assert projection["omitted"]["clipped_text_fields"] > 0
        assert tools.available_tools(payload, None)["read_evaluation_detail"] is True

        result = await tools.read_evaluation_detail(factory=factory, session_id=session_id,
            payload=payload, evaluation_id=row["id"])
        assert result["status"] == "OK" and result["code"] == "EVALUATION_DETAIL"
        data = result["data"]
        assert result["semantics"] == tools.UNTRUSTED
        assert tools._json_bytes(result) <= tools.DETAIL_MAX_BYTES
        assert data["hidden_total"] > 0 and not data["no_additional_evidence"]
        assert DECISIVE in (data["evaluation_hidden"] or "") or \
               any(DECISIVE in (p or "") for p in data["extra_missing_points"])
        # Nothing V2 already shows in full is repeated, and no reference answer
        # or provider metadata is present.
        assert not (set(data) & {"question", "scores", "question_kind", "session_question_id",
                                 "answer", "evaluation", "reference_answer", "provider", "model"})
        assert evaluation_fingerprint(row) == projected["fingerprint"]
        assert result["refs"] == [f"evaluation:{row['id']}", f"session:{session_id}"]

        # A different user's / another session's id is not in the frozen catalogue.
        foreign = await tools.read_evaluation_detail(factory=factory, session_id=session_id,
            payload=payload, evaluation_id=row["id"] + 100000)
        assert foreign["code"] == "EVALUATION_OUT_OF_SCOPE"
        # A row that changed after freezing must not be served.
        changed = dict(payload)
        changed["evaluations"] = [{**projected, "fingerprint": "not-the-frozen-one"}]
        stale = await tools.read_evaluation_detail(factory=factory, session_id=session_id,
            payload=changed, evaluation_id=row["id"])
        assert stale["code"] == "EVALUATION_CHANGED"
    run(database_url, scenario)


def test_scenario_b_a_replay_coach_without_the_tool_cannot_cite_the_hidden_fact():
    """The harness must not invent the hidden fact when it declines to query."""
    evaluations = [dict(id=7, evidence_ref="evaluation:7", session_question_id=7,
        question="消息可靠性如何处理？", answer=LONG_ANSWER[:200], evaluation="本轮机制表述清晰。",
        scores=dict(technical_correctness=4, coverage=4, depth=4, relevance=4, communication=4))]
    artifact = HarnessCoachArtifact.model_validate(response_for([7]))
    resolved = resolve_coach_artifact(artifact, evaluations)
    blob = json.dumps(resolved, ensure_ascii=False)
    assert DECISIVE not in blob
    assert resolved["strengths"] and resolved["weaknesses"]


# ── Scenario C: a legal CONFLICTED state from real evaluations ──

def test_scenario_c_the_history_tool_adds_refs_but_the_state_is_already_known(database_url):
    """Synthetic pressure test: five formal replay sessions -> CONFLICTED."""
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        for answer in CONFLICT_HISTORY:
            await session_for(factory, user_id, target_id, answer)
        # A report session created *after* the conflict: its frozen plan should
        # already describe the conflicted topic.
        created = await session_for(factory, user_id, target_id, "Kafka 事务和唯一键，提交后 ACK，失败恢复")
        session_id = created["id"]
        payload, _, snapshot, evaluations = await payload_for(factory, session_id)
        service = InterviewReportService(factory, None)
        async with factory() as db:
            history = await service._capability_history(db, user_id, snapshot)
        scope = service._training_scope(snapshot, history)
        conflicted = [k for k, v in scope["targets"].items() if "RECHECK" in v["available_modes"]]
        assert conflicted, "the replay history must legally produce a RECHECK-eligible state"

        knowledge_id = conflicted[0]
        result = await tools.read_capability_history(knowledge_id=knowledge_id,
            capability_history=history, allowed_knowledge_ids=set(scope["targets"]))
        assert result["status"] == "OK" and result["code"] == "CAPABILITY_HISTORY"
        data = result["data"]
        assert tools._json_bytes(result) <= tools.HISTORY_MAX_BYTES
        assert "answer" not in data and "evaluation" not in data
        assert data["source"] == "CAPTURED_AT_REPORT_GENERATION_NOT_A_LIVE_QUERY"
        assert data["fingerprint"] == history["fingerprint"]
        assert all(ref.startswith(("evaluation:", "session:")) for ref in result["refs"])
        assert set(data) & {"observations", "conflicts", "state", "revision"}

        # V2 already carries the state (and therefore the legal mode). The tool
        # adds observation-level refs, not a different decision.
        ctx = payload["snapshot"].get("capability_context") or {}
        plan_items = [i for i in (ctx.get("items") or []) if i["knowledge_id"] == knowledge_id]
        if plan_items:
            assert plan_items[0]["state"] == data["state"]
            assert "RECHECK" in modes(plan_items[0]["state"])
        assert "RECHECK" in modes(data["state"], data["revoked"])

        # The resolver, not the tool, is what makes a RECHECK suggestion legal.
        def artifact(mode):
            value = response_for([e["id"] for e in evaluations][-2:])
            value["suggested_training"] = dict(mode=mode, knowledge_id=knowledge_id,
                reason="针对分歧点做一次独立复测", scope="CURRENT_JOB_TARGET",
                supporting_evaluation_ids=[f"evaluation:{e['id']}" for e in evaluations[-2:]])
            return HarnessCoachArtifact.model_validate(value)
        legal = resolve_coach_artifact(artifact("RECHECK"), evaluations, training_scope=scope)
        assert legal["suggested_training"]["mode"] == "RECHECK"
        assert legal["suggested_training"]["validation"] == "LEGAL_AT_REPORT_TIME_ADVISORY_ONLY"
        illegal = resolve_coach_artifact(artifact("WEAKNESS"), evaluations, training_scope=scope)
        assert illegal["suggested_training"] is None
        assert illegal["suggested_training_rejected"]["code"] == "MODE_NOT_ALLOWED_FOR_STATE"

        # A deterministic baseline reaches the same mode with zero model calls.
        baseline = deterministic_suggestions(scope, evaluations)
        assert any(c["knowledge_id"] == knowledge_id and c["mode"] == "RECHECK" for c in baseline)
    run(database_url, scenario)
