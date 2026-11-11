"""Versioned question-vs-goal behavior; original provider responses from docs/69."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.adaptive import AdaptiveEvaluation, resolve_observations

CALLS = json.loads((Path(__file__).parent / "fixtures/v232_gap_failure.json").read_text(encoding="utf-8"))["calls"]
KID = "ai_agent_application:rag_retrieval"
SCOPE = "QUESTION_GAP_SCOPE_V1"
FORMULA = "gap_ebf04b4e255cc571e5412c3e"
REMAINS = ["gap_a304838ff6b4c044b3f363f8", "gap_e966b66c767a0f23b3615679"]


def original_packet():
    prior = {"adaptive":{"active_knowledge_id":KID,"knowledge_state":{"items":{}}},"answer":CALLS[1]["answer"]}
    gaps = resolve_observations(AdaptiveEvaluation.model_validate(CALLS[1]["output"]), prior)[0]["open_gaps"]
    assert [g["id"] for g in gaps] == [FORMULA, *REMAINS]
    return {"adaptive":{"active_knowledge_id":KID,"knowledge_state":{"items":{KID:{"open_gaps":gaps}}}},
            "answer":CALLS[3]["answer"]}


def scoped(focus):
    p = original_packet()
    p["snapshot"] = {"adaptive_policy":{"gap_scope_contract":SCOPE}}
    p["adaptive"]["focus_gap_ids"] = focus
    return p


def test_exact_live_reply_legacy_rejects_but_frozen_scope_can_commit_current_performance():
    reply = AdaptiveEvaluation.model_validate(CALLS[3]["output"])
    with pytest.raises(AppError, match="未解决") as error:
        resolve_observations(reply, original_packet())
    assert error.value.code == "UNRESOLVED_KNOWLEDGE_GAP"
    row, = resolve_observations(reply, scoped([FORMULA]))
    assert row["question_state"] == "DEMONSTRATED_IN_SESSION"
    assert row["state"] == "PARTIALLY_DEMONSTRATED"
    assert row["open_gaps"] == original_packet()["adaptive"]["knowledge_state"]["items"][KID]["open_gaps"][1:]
    assert [getattr(reply,k) for k in ("technical_correctness","coverage","depth","relevance","communication")] == [5,5,4,5,5]


@pytest.mark.parametrize("focus", [[FORMULA, REMAINS[0]], None])
def test_demonstrated_with_unmet_or_unknown_focal_scope_still_rejected(focus):
    with pytest.raises(AppError) as error:
        resolve_observations(AdaptiveEvaluation.model_validate(CALLS[3]["output"]), scoped(focus))
    assert error.value.code == "UNRESOLVED_KNOWLEDGE_GAP"


def test_multi_gap_partial_preserves_both_unanswered_gaps():
    raw=deepcopy(CALLS[3]["output"])
    raw["knowledge_observations"][0]["state"]="PARTIALLY_DEMONSTRATED"
    row,=resolve_observations(AdaptiveEvaluation.model_validate(raw),scoped([FORMULA, *REMAINS]))
    assert [g["id"] for g in row["open_gaps"]] == REMAINS


def test_both_focal_gaps_resolved_but_untested_history_stays_open():
    p=scoped([FORMULA,REMAINS[0]])
    detail="并集扩大召回范围，BM25遗漏同义词时可由向量召回补上。"
    p["answer"]+=detail
    raw=deepcopy(CALLS[3]["output"])
    observation=raw["knowledge_observations"][0]
    observation["answer_quotes"][-1]=detail
    observation["resolved_gap_ids"].append(REMAINS[0])
    row,=resolve_observations(AdaptiveEvaluation.model_validate(raw),p)
    assert row["question_state"]=="DEMONSTRATED_IN_SESSION"
    assert row["state"]=="PARTIALLY_DEMONSTRATED" and [g["id"] for g in row["open_gaps"]]==REMAINS[1:]


def test_explicit_empty_focus_does_not_close_old_gaps_and_new_missing_still_blocks():
    p=scoped([])
    raw=deepcopy(CALLS[3]["output"])
    raw["knowledge_observations"][0]["resolved_gap_ids"]=[]
    row,=resolve_observations(AdaptiveEvaluation.model_validate(raw),p)
    assert [g["id"] for g in row["open_gaps"]]==[FORMULA,*REMAINS]
    raw["knowledge_observations"][0]["missing_mechanisms"]=["本题尚未覆盖的机制"]
    with pytest.raises(AppError) as err:
        resolve_observations(AdaptiveEvaluation.model_validate(raw),p)
    assert err.value.code=="UNRESOLVED_KNOWLEDGE_GAP"


def test_stale_or_unknown_scope_cannot_be_used_to_bypass_validation():
    with pytest.raises(AppError) as error:
        resolve_observations(AdaptiveEvaluation.model_validate(CALLS[3]["output"]), scoped(["gap_other_goal"]))
    assert error.value.code == "INVALID_QUESTION_GAP_SCOPE"


def test_evaluator_cannot_set_question_focus_in_output():
    from pydantic import ValidationError
    raw=deepcopy(CALLS[3]["output"])
    raw["knowledge_observations"][0]["focus_gap_ids"]=[]
    with pytest.raises(ValidationError):
        AdaptiveEvaluation.model_validate(raw)


async def interview_fixture(factory, provider, count):
    from test_m4_interview import seed
    from interview_api.infrastructure.models.router import ModelRouter
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.modules.public_career.models import JobDescriptionVersion, JobRequirement
    _, _, _, uid, seeded = await seed(factory, public=False)
    snap = seeded["context_snapshot"]
    async with factory() as db:
        jd = JobDescriptionVersion(job_id=snap["job_id"], version_number=2, raw_text="DEMO RAG 与 AI 应用",
                                   publication_status="PUBLISHED")
        db.add(jd)
        await db.flush()
        for topic in ("RAG 检索与重排", "Agent 工具调用", "大模型结构化输出", "Python 异步", "系统设计故障恢复"):
            db.add(JobRequirement(job_description_version_id=jd.id, statement=topic, requirement_type="SKILL"))
        await db.commit()
    router = ModelRouter({route:provider for route in ("interview_main","answer_evaluation","interview_coach")})
    req = CreateSessionRequest(company_id=snap["company_id"],job_id=snap["job_id"],question_count=count,
                               interviewer_skill_id="ai_agent_application",adaptive_interview=True)
    return uid, MockInterviewService(factory,router), req


@pytest.mark.parametrize("mode", ["legacy", "scoped", "multi_focal"])
def test_original_failure_through_service_freeze_retry_and_memory(database_url, mode):
    import asyncio
    from unittest.mock import patch
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
    from interview_api.modules.candidate_profile.evidence import digest
    from interview_api.modules.interview.adaptive_prompts import freeze_prompts
    from interview_api.modules.interview import controller
    from interview_api.modules.memory.capability_service import CapabilityMemoryService

    class Provider(ModelProvider):
        provider_name, model_name = "replay", "ORIGINAL_GAP_FAILURE_REPLAY"
        calls = []
        rejected = False
        async def generate(self,messages,*,schema):
            p = json.loads(messages[-1]["content"])["input"]
            self.calls.append(schema["title"])
            if schema["title"] == "AdaptiveSelection":
                first = not p.get("answer")
                raw = deepcopy(CALLS[0 if first else 2]["output"])
                a = p["adaptive"]
                pair = next(c for c in a["candidates"] if c["target_knowledge_id"]==KID and c["source"]["ref"].startswith("skill:"))
                raw["candidate_id"] = pair["candidate_id"]
                raw["supporting_refs"] = [pair["source"]["ref"]] + ([] if first else [a["current_answer_ref"]])
                if "focus_gap_ids" in schema["properties"]:
                    raw["focus_gap_ids"] = [] if first else [FORMULA]
                    if not first and mode == "scoped" and not self.rejected:
                        self.rejected=True
                        raw["focus_gap_ids"]=["gap_other_goal"]
                    if not first and mode == "multi_focal":
                        raw["focus_gap_ids"].append(REMAINS[0])
            else:
                sc=p["scoring_context"]
                first=sc["parent_status"]=="NOT_APPLICABLE"
                assert p["answer"]==CALLS[1 if first else 3]["answer"]
                if mode=="legacy":
                    assert "focus_gap_ids" not in sc
                else:
                    assert sc["focus_gap_ids"] == ([] if first else [FORMULA] + (REMAINS[:1] if mode=="multi_focal" else []))
                raw=CALLS[1 if first else 3]["output"]
            return ModelResponse(json.dumps(raw,ensure_ascii=False), {"total_tokens":100})

    async def run():
        engine=create_async_engine(database_url)
        factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            p=Provider()
            uid,core,req=await interview_fixture(factory,p,2)
            original=controller.build_context
            async def legacy_context(*args,**kwargs):
                context,rid,_=await original(*args,**kwargs)
                policy=context["adaptive_policy"]
                policy.pop("gap_scope_contract")
                policy["prompts"]=freeze_prompts(context_projection=True,evaluation_context=True)
                return context,rid,digest(context)
            if mode=="legacy":
                with patch.object(controller,"build_context",legacy_context):
                    session=await core.create(uid,req)
            else:
                session=await core.create(uid,req)
            sid=session["id"]
            q1=await core.start(sid,uid)
            if mode=="scoped":
                with pytest.raises(AppError) as failure:
                    await core.answer(sid,uid,q1["question_id"],"p0-a",CALLS[1]["answer"])
                assert failure.value.code=="INVALID_QUESTION_GAP_SCOPE"
                assert p.calls.count("AdaptiveEvaluation")==1
                assert len((await core.detail(sid,uid))["questions"])==1
            await core.answer(sid,uid,q1["question_id"],"p0-a",CALLS[1]["answer"])
            assert p.calls.count("AdaptiveEvaluation")==1  # validated evaluation reuse
            before=await core.detail(sid,uid)
            q2=before["questions"][-1]
            decision=q2["evidence"]["adaptive_decision"]
            if mode=="scoped":
                assert decision["focus_gap_ids"]==[FORMULA]
                events=await core.answer(sid,uid,q2["question_id"],"p0-b",CALLS[3]["answer"])
                assert events==await core.answer(sid,uid,q2["question_id"],"p0-b",CALLS[3]["answer"])
                after=await core.detail(sid,uid)
                assert after["status"]=="COMPLETED" and after["state_version"]==before["state_version"]+2
                assert after["state_version"]==6  # rejected first attempt consumed a version, not a question
                row=after["interview_plan_json"]["adaptive_knowledge"]["items"][KID]
                assert row["question_state"]=="DEMONSTRATED_IN_SESSION" and row["state"]=="PARTIALLY_DEMONSTRATED"
                assert [g["id"] for g in row["open_gaps"]]==REMAINS
                assert after["questions"][-1]["evidence"]["adaptive_decision"]==decision
                async with factory() as db:
                    mem=next(m for m in await CapabilityMemoryService(db).list(uid) if m["topic"]=="rag_retrieval")
                    assert mem["state"]!="VERIFIED" and mem["independent_sessions"]==0
            else:
                with pytest.raises(AppError) as error:
                    await core.answer(sid,uid,q2["question_id"],"p0-b",CALLS[3]["answer"])
                assert error.value.code=="UNRESOLVED_KNOWLEDGE_GAP"
                after=await core.detail(sid,uid)
                assert after["status"]=="IN_PROGRESS" and after["active_question_id"]==q2["question_id"]
                assert len(after["interview_plan_json"]["adaptive_knowledge"]["observations"])==1
            assert p.calls.count("AdaptiveEvaluation")==2
            assert (await core.read_state(sid,uid))[-1]==100*len(p.calls)
            assert after["context_snapshot"]==session["context_snapshot"]
            assert not after["interview_plan_json"]["model_call_reservations"]
        finally:
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("harness", [False, True])
def test_scoped_twenty_round_replay_completes_coach_and_next_memory(database_url, harness):
    import asyncio
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
    from interview_api.modules.interview_report.service import InterviewReportService
    from interview_api.modules.memory.capability_service import CapabilityMemoryService
    scenarios=json.loads((Path(__file__).parents[1]/"scripts/replay20_scenarios.json").read_text(encoding="utf-8"))

    class Provider(ModelProvider):
        provider_name, model_name = "replay", "SCOPED_REPLAY_20"
        evaluations=0
        tool_calls=0
        async def generate(self,messages,*,schema):
            p=json.loads(messages[-1]["content"])["input"]
            title=schema["title"]
            if title in ("AdaptiveSelection", "MainHarnessAction"):
                n=len(p["history"])
                topic,q,_,_,_,closes,rubric=scenarios[n]
                a=p["adaptive"]
                strategy="FOLLOW_UP" if n+1 in (2,4,6,8,10,13,15,18) else "NEXT"
                c=next(c for c in a["candidates"] if c["target_knowledge_id"].endswith(":"+topic)
                       and strategy in c["strategies"] and c["source"]["ref"].startswith("skill:"))
                gaps=a["knowledge_state"]["items"].get(c["target_knowledge_id"],{}).get("open_gaps",[])
                if harness and strategy == "FOLLOW_UP":
                    if not p["tool_feedback"]:
                        self.tool_calls += 1
                        return ModelResponse(json.dumps(dict(kind="tool", name="read_active_knowledge_gaps",
                            arguments={})), {"total_tokens":100})
                    gaps = p["tool_feedback"][0]["result"]["data"]["gaps"]
                focus=[g["id"] for g in gaps if g["text"] in closes] if strategy=="FOLLOW_UP" else []
                value=dict(candidate_id=c["candidate_id"],strategy=strategy,focus_gap_ids=focus,
                    supporting_refs=[c["source"]["ref"]]+([a["current_answer_ref"]] if n else []),
                    reason_tags=["MISSING_MECHANISM" if focus else "COVERAGE_SWITCH" if n else "OPENING"],
                    proposed_question=dict(question=q,expected_points=rubric,dimension=topic,difficulty=p["desired_difficulty"]))
                if harness:
                    value = dict(kind="final", selection=value)
            elif title=="AdaptiveEvaluation":
                topic,_,answer,missing,state,closes,rubric=scenarios[self.evaluations]
                self.evaluations+=1
                kid=p["adaptive"]["active_knowledge_id"]
                gaps=p["adaptive"]["knowledge_state"]["items"][kid]["open_gaps"]
                assert kid.endswith(":"+topic) and p["answer"]==answer and p["rubric"]==rubric
                new=missing.split("；") if missing else []
                resolved=[g["id"] for g in gaps if g["text"] in closes]
                assert len(resolved)==len(closes)
                scores=(5,5,4,5,5) if state=="DEMONSTRATED_IN_SESSION" else (3,3,3,4,4)
                value=dict(zip(("technical_correctness","coverage","depth","relevance","communication"),scores),
                    evaluation="离线回放：评估当前答题机制。",covered_points=[rubric[0]],missing_points=new,
                    factual_or_reasoning_issues=[],follow_up_focus=new,next_action_recommendation="NEXT",
                    knowledge_observations=[dict(knowledge_id=kid,state=state,answer_quotes=[answer],
                        missing_mechanisms=new,resolved_gap_ids=resolved)])
            else:
                assert title=="CoachArtifact"
                value=dict(overall_summary="离线回放：复盘检索和异常恢复边界。",strengths=[],
                    weaknesses=[dict(statement="初答没有考虑检索分数的量纲差异。",
                        supporting_evaluation_ids=[f"evaluation:{p['evaluations'][0]['id']}"])],
                    recurring_issues=[],job_alignment=[],recommended_actions=["演练失败恢复"],
                    practice_plan=["复盘排名融合"],memory_proposals=[])
            return ModelResponse(json.dumps(value,ensure_ascii=False),{"total_tokens":100})

    async def run():
        engine=create_async_engine(database_url)
        factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            p=Provider()
            uid,core,req=await interview_fixture(factory,p,20)
            req = req.model_copy(update={"main_harness": harness})
            start=await core.create(uid,req)
            sid=start["id"]
            await core.start(sid,uid)
            for n,scenario in enumerate(scenarios,1):
                q=(await core.detail(sid,uid))["current_question"]
                events=await core.answer(sid,uid,q["question_id"],f"p0-long-{n}",scenario[2])
                assert events==await core.answer(sid,uid,q["question_id"],f"p0-long-{n}",scenario[2])
            final=await core.detail(sid,uid)
            assert final["status"]=="COMPLETED" and final["state_version"]==41
            assert final["context_snapshot"]==start["context_snapshot"]
            assert p.evaluations==20 and len(final["questions"])==20
            assert p.tool_calls == (8 if harness else 0)
            assert (await core.read_state(sid,uid))[-1] == 4000 + 100 * p.tool_calls
            assert len(final["interview_plan_json"]["adaptive_knowledge"]["observations"])==20
            reports=InterviewReportService(factory,core.router)
            report=await reports.generate(sid,uid)
            assert report["generation_status"]=="COMPLETED" and report["producer"]=="REPLAY_FIXTURE"
            assert report["statistics"]["evaluated_count"]==20 and report["statistics"]["follow_up_count"]==8
            assert len(report["coach_analysis"]["weaknesses"][0]["supporting_evaluations"])==1
            assert (await reports.generate(sid,uid))["reused"]
            async with factory() as db:
                states=await CapabilityMemoryService(db).list(uid)
            assert len(states)==5 and all(m["state"]!="VERIFIED" for m in states)
            nxt=await core.create(uid,req)
            assert nxt["context_snapshot"]["memory_guided_plan"]["items"]
            print(f"P1_REPLAY_20 harness={harness} session={sid} report={report['id']} next_session={nxt['id']}")
        finally:
            await engine.dispose()
    asyncio.run(run())
