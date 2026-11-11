"""V2.1 public contracts: frozen blueprint, evidence observations and decisions."""
from copy import deepcopy
import pytest

from interview_api.modules.interview.skill_engine import SkillRegistry


def context(skill="java_backend"):
    return dict(interviewer_config=SkillRegistry().freeze(skill, "technical_screen", "standard"),
        requirements=[dict(id=1, statement="Kafka 消息可靠性", importance="REQUIRED")],
        candidate_facts=[], candidate_evidence=[], public_questions=[], interview_experiences=[],
        approved_memory_context=dict(items=[]), recent_performance_observations=dict(items=[]),
        budgets=dict(question_count=8, max_follow_ups=2), job_id=1, job_title="Java 后端")


def test_blueprint_is_bounded_source_backed_and_skill_specific():
    from interview_api.modules.interview.adaptive import build_blueprint
    java = context()
    original = deepcopy(java)
    plan = build_blueprint(java)
    assert java == original
    assert plan["version"] == "2.1.0"
    assert 5 <= len(plan["targets"]) <= 8
    kafka = next(t for t in plan["targets"] if t["topic"] == "kafka_reliability")
    assert "requirement:1" in [s["ref"] for s in kafka["sources"]]
    assert plan["targets"][0]["topic"] == "kafka_reliability"
    ai = build_blueprint(context("ai_agent_application"))
    assert {t["knowledge_id"] for t in ai["targets"]} != {t["knowledge_id"] for t in plan["targets"]}
    assert build_blueprint(java) == plan


def test_observations_resolve_real_quotes_and_preserve_gap_history():
    import pytest
    from interview_api.core.exceptions import AppError
    from interview_api.modules.interview.adaptive import AdaptiveEvaluation, resolve_observations, apply_observations, knowledge_state
    from test_m4_interview import evaluation
    kid = "java_backend:kafka_reliability"
    payload = dict(answer="ACK 在数据库事务提交之后发送", adaptive=dict(active_knowledge_id=kid, knowledge_state=knowledge_state(None)))
    artifact = AdaptiveEvaluation.model_validate(dict(evaluation(), knowledge_observations=[dict(
        knowledge_id=kid, state="PARTIALLY_DEMONSTRATED", answer_quotes=["数据库事务提交之后发送"],
        missing_mechanisms=["重复消费如何去重"])]))
    resolved = resolve_observations(artifact, payload)
    state = apply_observations(knowledge_state(None), resolved, question_id=1, answer_id=1, evaluation_id=1)
    gap = state["items"][kid]["open_gaps"][0]["id"]
    payload["adaptive"]["knowledge_state"] = state
    payload["answer"] = "业务唯一键与数据库事务保证重复请求返回原结果"
    second = artifact.model_copy(deep=True)
    observation = second.knowledge_observations[0]
    observation.state = "DEMONSTRATED_IN_SESSION"
    observation.answer_quotes = ["业务唯一键与数据库事务"]
    observation.missing_mechanisms, observation.resolved_gap_ids = [], [gap]
    updated = apply_observations(state, resolve_observations(second, payload), question_id=2, answer_id=2, evaluation_id=2)
    assert updated["items"][kid]["open_gaps"] == []
    assert len(updated["observations"]) == 2
    assert state["items"][kid]["open_gaps"]  # old input unchanged
    observation.answer_quotes = ["模型虚构的回答"]
    with pytest.raises(AppError, match="回答依据"):
        resolve_observations(second, payload)


def test_replay_goal_decisions_commit_observations_retry_and_switch(database_url):
    import asyncio
    from uuid import uuid4
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.modules.interview.adaptive import planning_payload
    from test_m4_interview import seed, evaluation

    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            core, main, evaluator, uid, old = await seed(factory, budget=4)
            detail = await core.create(uid, CreateSessionRequest(company_id=old["context_snapshot"]["company_id"],
                job_id=old["context_snapshot"]["job_id"], question_count=4, interviewer_skill_id="java_backend", adaptive_interview=True))
            sid = detail["id"]
            pinned = deepcopy(detail["context_snapshot"])
            session, ctx, qs, active, _ = await core.read_state(sid, uid)
            payload = planning_payload(ctx, qs, active, session.interview_plan_json)

            def decision(payload, strategy, kid=None, wording="Redis 缓存更新失败时如何恢复？"):
                choice = next(c for c in payload["adaptive"]["choices"] if strategy in c["strategies"] and (not kid or c["target"]["knowledge_id"] == kid))
                target = choice["target"]
                source = target["sources"][0]
                return dict(decision_version="2.1.0", strategy=strategy, target_knowledge_id=target["knowledge_id"],
                    supporting_refs=[payload["adaptive"]["current_answer_ref"] or source["ref"]], reason_tags=["OPENING" if not payload["answer"] else "MISSING_MECHANISM"],
                    proposed_question=dict(question=wording, expected_points=["机制", "失败恢复"], dimension="可靠性", difficulty="MEDIUM",
                        source_type="DYNAMIC_GENERATED" if strategy in ("NEXT", "DEEP_DIVE") else "FOLLOW_UP"), question_source_ref=source["ref"])

            first = decision(payload, "NEXT")
            kid = first["target_knowledge_id"]
            main.responses.append(first)
            await core.start(sid, uid)
            for index, strategy in enumerate(("REQUEST_EXAMPLE", "DEEP_DIVE", "NEXT", None)):
                session, ctx, qs, active, _ = await core.read_state(sid, uid)
                content = "我使用业务唯一键与数据库事务"
                ev = dict(evaluation("NEXT"), knowledge_observations=[dict(
                    knowledge_id=(active.candidate_refs_json["adaptive_decision"])["target_knowledge_id"],
                    state="PARTIALLY_DEMONSTRATED", answer_quotes=["业务唯一键与数据库事务"], missing_mechanisms=["并发失败恢复"] )])
                evaluator.responses.append(ev)
                if strategy:
                    # Current pending IDs are references, not invented Evaluation IDs.
                    # Determine actual answer ID only in the external model boundary below.
                    main.responses.append(dict(_strategy=strategy, _kid=kid if strategy != "NEXT" else None, _index=index))
                key = uuid4().hex
                events = await core.answer(sid, uid, active.id, key, content)
                assert events[0][0] == "evaluation"
                assert events == await core.answer(sid, uid, active.id, key, content)
            final = await core.detail(sid, uid)
            assert final["status"] == "COMPLETED"
            assert len(final["interview_plan_json"]["adaptive_knowledge"]["observations"]) == 4
            assert final["context_snapshot"] == pinned
            assert [q["question_kind"] for q in final["questions"]] == ["NEXT", "FOLLOW_UP", "DEEP_DIVE", "NEXT"]
            assert main.calls == 4 and evaluator.calls == 4
        finally:
            await engine.dispose()

    # A real Replay provider boundary selects only from the supplied public contract.
    from interview_api.infrastructure.models.provider import ReplayModelProvider, ModelResponse
    import json
    original = ReplayModelProvider.generate
    async def generate(self, messages, *, schema=None):
        payload = json.loads(messages[-1]["content"])["input"]
        if self.responses and "_strategy" in self.responses[0]:
            request = self.responses.pop(0)
            payload = json.loads(messages[-1]["content"])["input"]
            choices = payload["adaptive"].get("choices") or [dict(target=dict(knowledge_id=c["target_knowledge_id"],
                label=c.get('label') or payload['adaptive']['targets'][c['target_knowledge_id']]['label'],
                sources=[payload['adaptive'].get('sources', {}).get(c['source']['ref'], c['source'])]),
                strategies=c["strategies"]) for c in payload["adaptive"]["candidates"]]
            choice = next(c for c in choices if request["_strategy"] in c["strategies"] and
                (not request["_kid"] or c["target"]["knowledge_id"] == request["_kid"]))
            source = choice["target"]["sources"][0]
            self.responses.insert(0, dict(decision_version="2.1.0", strategy=request["_strategy"],
                target_knowledge_id=choice["target"]["knowledge_id"], supporting_refs=[payload["adaptive"]["current_answer_ref"]],
                reason_tags=["MISSING_MECHANISM"], question_source_ref=source["ref"], proposed_question=dict(
                    question=f"{choice['target']['label']} 第{request['_index'] + 2}轮：请说明并发失败后怎样恢复？", expected_points=["机制", "恢复"],
                    dimension="可靠性", difficulty="MEDIUM", source_type="DYNAMIC_GENERATED" if request["_strategy"] in ("NEXT", "DEEP_DIVE") else "FOLLOW_UP")))
        if schema["title"] == "AdaptiveSelection" and self.responses and "target_knowledge_id" in self.responses[0]:
            from v21_support import selection_artifact
            self.responses[0] = selection_artifact(self.responses[0], payload,
                scoped="focus_gap_ids" in schema.get("properties", {}))
        return await original(self, messages, schema=schema)
    from unittest.mock import patch
    with patch.object(ReplayModelProvider, "generate", generate):
        asyncio.run(run())


def decision_contract():
    from interview_api.modules.interview.adaptive import build_blueprint, planning_payload
    ctx = context()
    ctx["adaptive_blueprint"] = build_blueprint(ctx)
    ctx["adaptive_policy"] = dict(version="2.1.0", prompts={})
    ctx.update(profile={}, normalized={}, candidate_facts=[], candidate_evidence=[])
    payload = planning_payload(ctx, [], None, None)
    target = payload["adaptive"]["choices"][0]["target"]
    data = dict(decision_version="2.1.0", strategy="NEXT", target_knowledge_id=target["knowledge_id"],
        supporting_refs=[target["sources"][0]["ref"]], reason_tags=["OPENING"], question_source_ref=target["sources"][0]["ref"],
        proposed_question=dict(question="Kafka 消费提交与 ACK 应如何配合？", expected_points=["事务边界"],
            dimension="消息可靠性", difficulty="MEDIUM", source_type="DYNAMIC_GENERATED"))
    return payload, data


@pytest.mark.parametrize("change,code", [
    (dict(target_knowledge_id="invented"), "ADAPTIVE_DECISION_REJECTED"),
    (dict(supporting_refs=["answer:99999"]), "UNKNOWN_DIRECTOR_REFERENCE"),
    (dict(question_source_ref="public_question:99999"), "UNKNOWN_DIRECTOR_SOURCE"),
    (dict(strategy="FOLLOW_UP"), "ADAPTIVE_DECISION_REJECTED"),
])
def test_director_rejects_unknown_goals_sources_and_opening_probe(change, code):
    from interview_api.modules.interview.adaptive import AdaptiveDecision, validate_decision
    from interview_api.core.exceptions import AppError
    payload, data = decision_contract()
    data.update(change)
    with pytest.raises(AppError) as caught:
        validate_decision(AdaptiveDecision.model_validate(data), payload)
    assert caught.value.code == code


def test_director_cannot_relabel_an_unrelated_question_or_select_complete():
    from pydantic import ValidationError
    from interview_api.modules.interview.adaptive import AdaptiveDecision, validate_decision
    from interview_api.core.exceptions import AppError
    payload, data = decision_contract()
    data["proposed_question"]["question"] = "Redis 的缓存过期如何实现？"
    with pytest.raises(AppError) as caught:
        validate_decision(AdaptiveDecision.model_validate(data), payload)
    assert caught.value.code == "ADAPTIVE_TOPIC_MISMATCH"
    data["strategy"] = "COMPLETE"
    with pytest.raises(ValidationError):
        AdaptiveDecision.model_validate(data)


def test_prompt_frozen_and_public_view_hides_future_rules():
    import json
    from interview_api.modules.interview.adaptive_prompts import freeze_prompts, AdaptivePromptRegistry
    from interview_api.modules.interview.skill_registry import public_session_view
    from interview_api.core.exceptions import AppError
    policy = dict(version="2.1.0", prompts=freeze_prompts())
    assert AdaptivePromptRegistry(policy).get("interview_main").version == "2.1.0"
    altered = deepcopy(policy)
    altered["prompts"]["interview_main"]["system"] += " mutation"
    with pytest.raises(AppError): AdaptivePromptRegistry(altered).get("interview_main")
    raw = dict(context_snapshot=dict(adaptive_policy=policy, adaptive_blueprint=dict(version="2.1.0", targets=[dict(label="Kafka", sources=["SECRET"])]),
        public_questions=[dict(question="FUTURE", expected_points=["SECRET"])]),
        planner_trace_json=dict(adaptive_decisions=[dict(proposed_question=dict(expected_points=["SECRET"]))]),
        messages=[dict(metadata_json=dict(evidence=dict(adaptive_decision=dict(proposed_question="SECRET"))))])
    serialized = json.dumps(public_session_view(raw))
    assert "SECRET" not in serialized and "FUTURE" not in serialized and "system" not in serialized
    assert raw["context_snapshot"]["adaptive_policy"] == policy


def test_answer_diagnostic_cannot_invent_or_drop_old_gap():
    from interview_api.modules.interview.adaptive import AdaptiveEvaluation, resolve_observations, knowledge_state
    from interview_api.core.exceptions import AppError
    from test_m4_interview import evaluation
    kid = "java_backend:kafka_reliability"
    state = knowledge_state(None)
    state["items"][kid] = dict(open_gaps=[dict(id="real_gap", text="ACK 边界")])
    payload = dict(answer="我会在事务提交后 ACK", adaptive=dict(active_knowledge_id=kid, knowledge_state=state))
    value = dict(evaluation(), knowledge_observations=[dict(knowledge_id=kid, state="DEMONSTRATED_IN_SESSION",
        answer_quotes=["事务提交后 ACK"], missing_mechanisms=[], resolved_gap_ids=[])])
    with pytest.raises(AppError) as caught:
        resolve_observations(AdaptiveEvaluation.model_validate(value), payload)
    assert caught.value.code == "UNRESOLVED_KNOWLEDGE_GAP"
    value["knowledge_observations"][0]["resolved_gap_ids"] = ["fake_gap"]
    with pytest.raises(AppError) as caught:
        resolve_observations(AdaptiveEvaluation.model_validate(value), payload)
    assert caught.value.code == "UNKNOWN_KNOWLEDGE_GAP"


def test_adaptive_replay_failure_retry_resolved_gap_report_and_isolation(database_url):
    import asyncio
    from uuid import uuid4
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from interview_api.core.exceptions import AppError
    from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.modules.interview_report.service import InterviewReportService
    from v21_support import AdaptiveReplayProvider
    from test_m4_interview import seed

    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            _, _, _, uid, old = await seed(factory)
            provider = AdaptiveReplayProvider()
            router = ModelRouter({r: provider for r in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE)})
            core = MockInterviewService(factory, router)
            detail = await core.create(uid, CreateSessionRequest(company_id=old["context_snapshot"]["company_id"],
                job_id=old["context_snapshot"]["job_id"], interviewer_skill_id="java_backend", adaptive_interview=True, question_count=3))
            sid = detail["id"]
            first = await core.start(sid, uid)
            key = uuid4().hex
            with pytest.raises(Exception) as caught:
                await core.answer(sid, uid, first["question_id"], key, "DEMO_TIMEOUT_ONCE")
            assert caught.value.code == "model_timeout"
            failed = await core.detail(sid, uid)
            assert failed["pending_answer"]["retryable"] and failed["pending_answer"]["status"] == "FAILED"
            assert failed["current_question"]["status"] == "ANSWERED" and not failed["completion_json"]
            assert "adaptive_knowledge" not in (failed["interview_plan_json"] or {})
            events = await core.answer(sid, uid, first["question_id"], key, "DEMO_TIMEOUT_ONCE")
            assert events[1][0] == "follow_up"
            state = (await core.detail(sid, uid))["interview_plan_json"]["adaptive_knowledge"]
            kid = next(iter(state["items"]))
            original_gap = state["items"][kid]["open_gaps"][0]["id"]
            for _ in range(2):
                q = (await core.detail(sid, uid))["current_question"]
                k = uuid4().hex
                e = await core.answer(sid, uid, q["question_id"], k, "使用数据库事务与业务唯一键，提交后确认结果，并处理失败恢复")
                assert e == await core.answer(sid, uid, q["question_id"], k, "使用数据库事务与业务唯一键，提交后确认结果，并处理失败恢复")
            done = await core.detail(sid, uid)
            assert done["status"] == "COMPLETED"
            state = done["interview_plan_json"]["adaptive_knowledge"]
            assert original_gap in state["observations"][1]["resolved_gap_ids"]
            assert state["items"][kid]["state"] == "DEMONSTRATED_IN_SESSION"
            assert done["questions"][2]["evidence"]["adaptive_decision"]["target_knowledge_id"] != kid
            with pytest.raises(AppError) as caught: await core.detail(sid, uid + 999999)
            assert caught.value.status_code == 404
            reports = InterviewReportService(factory, router)
            report = await reports.generate(sid, uid)
            assert report["generation_status"] == "COMPLETED"
            assert "并发名额验证" not in report["coach_analysis"]["overall_summary"]
            review = (await reports.session_detail(sid, uid))["adaptive_review"]
            assert review["targets"][0]["observations"][1]["resolved_gap_count"] == 1
            assert (await core.detail(sid, uid))["context_snapshot"] == detail["context_snapshot"]
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_invalid_director_keeps_answer_retryable_and_reuses_valid_evaluation(database_url):
    import asyncio
    import json
    from uuid import uuid4
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from interview_api.core.exceptions import AppError
    from interview_api.infrastructure.models.provider import ModelResponse
    from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from v21_support import AdaptiveReplayProvider
    from test_m4_interview import seed

    class BadOnce(AdaptiveReplayProvider):
        bad = False
        async def generate(self, messages, *, schema):
            result = await super().generate(messages, schema=schema)
            p = json.loads(messages[-1]["content"])["input"]
            if schema["title"] in ("AdaptiveDecision", "AdaptiveSelection") and p.get("answer") and not self.bad:
                self.bad = True
                value = json.loads(result.text)
                value["supporting_refs"] = ["answer:99999999"]
                return ModelResponse(json.dumps(value), result.usage)
            return result

    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            _, _, _, uid, old = await seed(factory)
            provider = BadOnce()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))
            detail = await core.create(uid, CreateSessionRequest(company_id=old["context_snapshot"]["company_id"],
                job_id=old["context_snapshot"]["job_id"], interviewer_skill_id="java_backend", adaptive_interview=True, question_count=3))
            sid = detail["id"]
            q = await core.start(sid, uid)
            key = uuid4().hex
            with pytest.raises(AppError) as caught: await core.answer(sid, uid, q["question_id"], key, "我只了解概念")
            assert caught.value.code == "UNKNOWN_DIRECTOR_REFERENCE"
            failed = await core.detail(sid, uid)
            assert failed["pending_answer"]["retryable"] and failed["current_question"]["status"] == "ANSWERED"
            assert "adaptive_knowledge" not in failed["interview_plan_json"]
            await core.answer(sid, uid, q["question_id"], key, "我只了解概念")
            done = await core.detail(sid, uid)
            assert len(done["interview_plan_json"]["adaptive_knowledge"]["observations"]) == 1
            assert provider.calls.count("AdaptiveEvaluation") == 1
            assert len([m for m in done["messages"] if m["role"] == "USER"]) == 1
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_blueprint_uses_only_approved_exact_job_sources_and_freezes(database_url):
    import asyncio
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from interview_api.modules.experience.models import InterviewExperience, InterviewQuestion
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from test_m4_interview import seed

    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            core, _, _, uid, old = await seed(factory)
            ctx = old["context_snapshot"]
            async with factory() as db:
                public = await db.scalar(select(InterviewQuestion).where(InterviewQuestion.job_id == ctx["job_id"], InterviewQuestion.review_status == "APPROVED"))
                excluded = [InterviewQuestion(company_id=ctx["company_id"], job_id=None, question="Kafka 公司但岗位未确认", review_status="APPROVED"),
                    InterviewQuestion(company_id=ctx["company_id"], job_id=ctx["job_id"], question="Kafka 报道主题不能冒充原问句", review_status="APPROVED",
                        routing_json=dict(source_anchor=dict(kind="REPORTED_TOPIC")))]
                db.add_all(excluded)
                await db.commit()
                public_id, excluded_ids = public.id, {q.id for q in excluded}
            detail = await core.create(uid, CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"],
                interviewer_skill_id="java_backend", adaptive_interview=True, question_count=6))
            pinned = detail["context_snapshot"]
            ids = {q["id"] for q in pinned["public_questions"]}
            assert public_id in ids and not ids & excluded_ids
            assert "UNREVIEWED" not in str(pinned)
            async with factory() as db:
                public = await db.get(InterviewQuestion, public_id)
                public.question = "ADMIN CHANGE AFTER FREEZE"
                await db.commit()
            assert (await core.detail(detail["id"], uid))["context_snapshot"] == pinned
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_whole_evaluation_deadline_is_timeout_not_browser_cancel(database_url, monkeypatch):
    import asyncio
    from uuid import uuid4
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from interview_api.core.config import settings
    from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
    from interview_api.modules.interview.models import InterviewAgentCall
    from v21_support import AdaptiveReplayProvider
    from test_m4_interview import seed
    class SlowEvaluation(AdaptiveReplayProvider):
        async def generate(self, messages, *, schema):
            if schema["title"] == "AdaptiveEvaluation": await asyncio.sleep(.1)
            return await super().generate(messages, schema=schema)
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            _, _, _, uid, old = await seed(factory)
            provider = SlowEvaluation()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))
            detail = await core.create(uid, CreateSessionRequest(company_id=old["context_snapshot"]["company_id"], job_id=old["context_snapshot"]["job_id"],
                interviewer_skill_id="java_backend", adaptive_interview=True, question_count=3))
            q = await core.start(detail["id"], uid)
            monkeypatch.setattr(settings, "interview_model_timeout_seconds", .03)
            with pytest.raises(Exception) as caught: await core.answer(detail["id"], uid, q["question_id"], uuid4().hex, "DEMO slow answer")
            assert getattr(caught.value, "code", None) == "model_timeout"
            current = await core.detail(detail["id"], uid)
            assert current["pending_answer"]["error"] == "model_timeout"
            async with factory() as db:
                run = await db.scalar(select(ExperienceAgentRun).join(InterviewAgentCall, InterviewAgentCall.agent_run_id == ExperienceAgentRun.id)
                    .where(InterviewAgentCall.session_id == detail["id"], ExperienceAgentRun.agent_role == "AnswerEvaluationAgent"))
                step = await db.scalar(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == run.id))
                assert run.status == step.status == "FAILED" and run.error_message == step.error_message == "model_timeout"
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_fixed_cases_contrast_v1_action_space_and_v2_goal_decisions():
    """Policy comparison only. The explicit Replay double is NOT an LLM quality judge."""
    import asyncio
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from interview_api.modules.interview.adaptive import build_blueprint, planning_payload, AdaptiveDecision, knowledge_state, apply_observations
    from interview_api.modules.interview.controller import SessionController
    from v21_support import AdaptiveReplayProvider
    data = json.loads((Path(__file__).parent / "fixtures/v21_behavior_cases.json").read_text(encoding="utf-8"))
    for case in data["cases"]:
        ctx = context("ai_agent_application" if case["id"].startswith("agent") else "java_backend")
        if case["id"].startswith("agent"):
            ctx["requirements"] = [dict(id=1, statement="Agent 工具调用与状态管理", importance="REQUIRED")]
        ctx.update(profile={}, normalized={}, adaptive_policy=dict(version="2.1.0", prompts={}))
        ctx["adaptive_blueprint"] = build_blueprint(ctx)
        kid = ctx["adaptive_blueprint"]["targets"][0]["knowledge_id"]
        active = SimpleNamespace(id=1, question="请说明具体实现", status="ASKED", source="DYNAMIC_GENERATED",
            difficulty="MEDIUM", parent_question_id=None, question_kind="NEXT", standard_answer="机制与边界",
            candidate_refs_json=dict(adaptive_decision=dict(target_knowledge_id=kid)))
        # Same scored-answer advisory NEXT: V1 can only execute that controlled action.
        # V2 can instead select a concrete gap strategy within the permitted goal space.
        ctx["budgets"].update(max_rounds=40, max_model_tokens=100000, max_duration_seconds=1800)
        assert SessionController.choose_action(ctx, [active], active, "NEXT", rounds=1, tokens=0, started_at=None) == "NEXT"
        state = knowledge_state(None)
        if case["id"] == "java_repaired":
            state = apply_observations(state, [dict(knowledge_id=kid, state="DEMONSTRATED_IN_SESSION", answer_quotes=[case["answer"]],
                missing_mechanisms=[], resolved_gap_ids=[], open_gaps=[], answer_spans=[])], question_id=1, answer_id=1, evaluation_id=1)
        payload = planning_payload(ctx, [active], active, dict(adaptive_knowledge=state), answer=case["answer"], answer_id=1)
        payload["evaluation"] = dict(missing_points=["ACK 与事务边界"] if "ACK" in case["answer"] else ["工具权限"] if "工具" in case["answer"] else [])
        provider = AdaptiveReplayProvider()
        result = asyncio.run(provider.generate([dict(role="user", content=json.dumps(dict(input=payload)))], schema=AdaptiveDecision.model_json_schema()))
        decision = AdaptiveDecision.model_validate_json(result.text)
        assert decision.strategy in case["expected"]
        if case["id"] == "java_repaired": assert decision.target_knowledge_id != kid
