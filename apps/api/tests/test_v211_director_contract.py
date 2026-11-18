"""Offline target/source contract. Constructed outputs are NOT lost LIVE responses."""
from copy import deepcopy

import pytest

from interview_api.core.exceptions import AppError
from test_v21_adaptive import context


def packet():
    from interview_api.modules.interview.adaptive import build_blueprint, planning_payload
    ctx = context()
    ctx.update(profile={}, normalized={}, adaptive_policy=dict(version="2.1.0", prompts={},
        decision_contract="TARGET_SOURCE_PAIR_V1"))
    ctx["adaptive_blueprint"] = build_blueprint(ctx)
    return planning_payload(ctx, [], None, None)


def test_role_projection_has_separate_internal_and_wire_byte_limits():
    import json
    from interview_api.modules.interview.adaptive import build_blueprint, planning_payload
    from interview_api.modules.interview.adaptive_prompts import freeze_prompts
    from interview_api.modules.interview.context_projection import model_payload
    ctx = context()
    ctx["adaptive_blueprint"] = build_blueprint(ctx)
    ctx["recent_performance_observations"] = {"items": [{"content": "未投影的历史信息" * 4000}]}
    ctx["adaptive_policy"] = dict(version="2.1.0", decision_contract="TARGET_SOURCE_PAIR_V1",
        context_projection="ROLE_CONTEXT_V1", prompts=freeze_prompts(context_projection=True))
    plan = planning_payload(ctx, [], None, None)
    assert len(json.dumps(plan, ensure_ascii=False).encode("utf-8")) > 96_000
    assert "recent_performance_observations" not in model_payload(plan, "interview_main")["snapshot"]
    plan["answer"] = "实" * 33_000
    with pytest.raises(AppError) as error:
        model_payload(plan, "interview_main")
    assert error.value.code == "ADAPTIVE_CONTEXT_BUDGET"


def test_main_selects_one_legal_pair_without_writing_source_or_target_ids():
    from interview_api.modules.interview.adaptive import AdaptiveSelection, resolve_selection, validate_decision
    payload = packet()
    selected = next(c for c in payload["adaptive"]["candidates"] if c["source"]["ref"] == "requirement:1")
    raw = dict(candidate_id=selected["candidate_id"], strategy="NEXT", supporting_refs=["requirement:1"],
        reason_tags=["OPENING"], proposed_question=dict(question="Kafka 的 ACK 与事务提交如何配合？",
            expected_points=["提交边界"], dimension="消息可靠性", difficulty="MEDIUM"))
    result = resolve_selection(AdaptiveSelection.model_validate(raw), payload)
    assert result.target_knowledge_id == "java_backend:kafka_reliability"
    assert result.question_source_ref == "requirement:1"
    assert result.question.job_requirement_id == 1
    assert validate_decision(result, payload)["source_type"] == "JOB_REQUIREMENT"
    # Resolver must not mutate the packet/frozen sources.
    assert payload == packet()
    assert "target_knowledge_id" not in AdaptiveSelection.model_json_schema()["properties"]
    assert "question_source_ref" not in AdaptiveSelection.model_json_schema()["properties"]


def test_pairs_are_stable_and_one_skill_source_can_support_distinct_targets():
    payload = packet()
    candidates = payload["adaptive"]["candidates"]
    version = payload["snapshot"]["interviewer_config"]["skill"]["version"]
    generic = [c for c in candidates if c["source"]["ref"] == f"skill:java_backend@{version}"]
    assert len(generic) >= 2
    assert len({c["candidate_id"] for c in generic}) == len(generic)
    assert candidates == packet()["adaptive"]["candidates"]


def test_frozen_blueprint_tampering_is_rejected_before_model_selection():
    from interview_api.modules.interview.adaptive import decision_candidates
    payload = packet()
    ctx = deepcopy(payload["snapshot"])
    ctx["adaptive_blueprint"]["targets"][0]["sources"][0]["ref"] = "skill:invented@9.9.9"
    with pytest.raises(AppError) as caught:
        decision_candidates(ctx, payload["adaptive"]["choices"])
    assert caught.value.code == "ADAPTIVE_BLUEPRINT_CHANGED"


def test_new_prompt_uses_pairs_and_old_prompt_remains_frozen():
    from interview_api.modules.interview.adaptive_prompts import freeze_prompts, AdaptivePromptRegistry
    old = freeze_prompts()
    new = freeze_prompts(contract="TARGET_SOURCE_PAIR_V1")
    assert AdaptivePromptRegistry(dict(prompts=old)).get("interview_main").version == "2.1.0"
    prompt = AdaptivePromptRegistry(dict(prompts=new)).get("interview_main")
    assert prompt.version == "2.1.2" and "candidate_id" in prompt.system
    assert old["answer_evaluation"] == new["answer_evaluation"]


def test_agent_uses_selection_schema_only_for_explicit_new_contract():
    from interview_api.modules.interview.agents import InterviewMainAgent
    agent = InterviewMainAgent(None, None)
    assert agent.adaptive(packet()).schema.__name__ == "AdaptiveSelection"
    assert agent.adaptive().schema.__name__ == "AdaptiveDecision"


def selection(payload, pair=None, strategy="NEXT"):
    from interview_api.modules.interview.adaptive import AdaptiveSelection
    pair = pair or payload["adaptive"]["candidates"][0]
    return AdaptiveSelection(candidate_id=pair["candidate_id"], strategy=strategy,
        supporting_refs=[payload["adaptive"].get("current_answer_ref") or pair["source"]["ref"]],
        reason_tags=["OPENING"], proposed_question=dict(question="Kafka 的事务提交与 ACK 边界如何保证？",
            expected_points=["幂等"], dimension="可靠性", difficulty="MEDIUM"))


def test_unknown_candidate_and_cross_target_source_replacement_are_rejected():
    from interview_api.modules.interview.adaptive import resolve_selection, validate_decision
    payload = packet()
    raw = selection(payload)
    raw.candidate_id = "C_fabricated"
    with pytest.raises(AppError) as caught:
        resolve_selection(raw, payload)
    assert caught.value.code == "UNKNOWN_DIRECTOR_CANDIDATE"
    pair = next(c for c in payload["adaptive"]["candidates"] if c["source"]["ref"] == "requirement:1")
    result = resolve_selection(selection(payload, pair), payload)
    result.target_knowledge_id = next(c["target_knowledge_id"] for c in payload["adaptive"]["candidates"]
        if c["target_knowledge_id"] != result.target_knowledge_id)
    with pytest.raises(AppError) as caught:
        validate_decision(result, payload)
    assert caught.value.code == "DIRECTOR_PAIR_MISMATCH"


def test_shared_requirement_is_two_explicit_pairs_not_a_global_source_allowlist():
    from interview_api.modules.interview.adaptive import build_blueprint, planning_payload
    ctx = context()
    ctx["requirements"][0]["statement"] = "Kafka 可靠性与 MySQL 数据库事务一致性"
    ctx.update(profile={}, normalized={}, adaptive_policy=dict(decision_contract="TARGET_SOURCE_PAIR_V1"))
    ctx["adaptive_blueprint"] = build_blueprint(ctx)
    payload = planning_payload(ctx, [], None, None)
    refs = [c for c in payload["adaptive"]["candidates"] if c["source"]["ref"] == "requirement:1"]
    assert {c["topic"] for c in refs} == {"kafka_reliability", "mysql_consistency"}
    assert len({c["candidate_id"] for c in refs}) == 2
    assert not any(c["source"]["ref"] == "requirement:1" and c["topic"] == "redis"
        for c in payload["adaptive"]["candidates"])


def test_empty_target_sources_fall_back_to_pinned_generic_skill_not_public_evidence():
    from interview_api.modules.interview.adaptive import planning_payload
    from interview_api.modules.candidate_profile.evidence import digest
    payload = packet()
    ctx = deepcopy(payload["snapshot"])
    target = ctx["adaptive_blueprint"]["targets"][0]
    target["sources"] = []
    blueprint = ctx["adaptive_blueprint"]
    blueprint["fingerprint"] = digest({k: v for k, v in blueprint.items() if k != "fingerprint"})
    fresh = planning_payload(ctx, [], None, None)
    pairs = [c for c in fresh["adaptive"]["candidates"] if c["target_knowledge_id"] == target["knowledge_id"]]
    assert len(pairs) == 1
    assert pairs[0]["source_semantics"] == "GENERIC_SKILL_NOT_COMPANY_EVIDENCE"
    assert pairs[0]["source"]["source_type"] == "DYNAMIC_GENERATED"


def test_v21_failure_class_replay_uses_known_input_not_an_invented_live_output():
    """Known LIVE first answer + CONSTRUCTED cross-target source, explicitly labelled."""
    from interview_api.modules.interview.adaptive import AdaptiveDecision, validate_decision, resolve_selection
    from test_v21_adaptive import decision_contract
    payload, value = decision_contract()
    # We have the true input from the retained batch script, not its lost output.
    payload["answer"] = "消费者收到 Kafka 消息后处理订单，然后 ACK，我没有考虑数据库提交失败和重试。"
    payload["adaptive"].update(current_answer_ref="answer:1", current_evaluation_ref="evaluation_run:1")
    payload["adaptive"]["allowed_refs"].append("answer:1")
    # requirement:1 exists and is globally known, but is NOT a Redis source.
    # This cross-product is a synthetic reproduction, not the missing LIVE tuple.
    value.update(supporting_refs=["answer:1"], target_knowledge_id="java_backend:redis", question_source_ref="requirement:1")
    with pytest.raises(AppError) as caught:
        validate_decision(AdaptiveDecision.model_validate(value), payload)
    assert caught.value.code == "UNKNOWN_DIRECTOR_SOURCE"
    fixed = packet()
    result = resolve_selection(selection(fixed), fixed)
    assert validate_decision(result, fixed)["target_topic"] == "kafka_reliability"


@pytest.mark.parametrize("answer,missing,expected", [
    ("只了解一些概念", ["具体实现"], "REQUEST_EXAMPLE"),
    ("Kafka 消费后 ACK", ["ACK 与事务边界"], "DEEP_DIVE"),
])
def test_new_model_boundary_preserves_answer_driven_strategy(answer, missing, expected):
    import asyncio
    import json
    from types import SimpleNamespace
    from interview_api.modules.interview.adaptive import AdaptiveSelection, planning_payload, resolve_selection, validate_decision
    from v21_support import AdaptiveReplayProvider
    p = packet()
    ctx = p["snapshot"]
    kid = "java_backend:kafka_reliability"
    active = SimpleNamespace(id=1, question="Kafka ACK 如何处理？", status="ASKED", difficulty="MEDIUM",
        parent_question_id=None, question_kind="NEXT", standard_answer="事务边界", source="DYNAMIC_GENERATED",
        candidate_refs_json=dict(adaptive_decision=dict(target_knowledge_id=kid)))
    p = planning_payload(ctx, [active], active, None, answer=answer, answer_id=1)
    p["evaluation"] = dict(missing_points=missing)
    model_input = deepcopy(p)
    model_input["adaptive"].pop("choices")
    response = asyncio.run(AdaptiveReplayProvider().generate([dict(role="user", content=json.dumps(dict(input=model_input)))],
        schema=AdaptiveSelection.model_json_schema()))
    artifact = resolve_selection(AdaptiveSelection.model_validate_json(response.text), p)
    assert artifact.strategy == expected
    assert artifact.target_knowledge_id == kid
    assert validate_decision(artifact, p)["target_topic"] == "kafka_reliability"


def test_source_free_output_cannot_sneak_extra_provenance_into_question():
    from pydantic import ValidationError
    from interview_api.modules.interview.adaptive import AdaptiveSelection
    raw = selection(packet()).model_dump()
    raw["proposed_question"]["source_question_id"] = 99999
    with pytest.raises(ValidationError):
        AdaptiveSelection.model_validate(raw)


@pytest.mark.parametrize("strategy", ["FOLLOW_UP", "REQUEST_EXAMPLE", "DEEP_DIVE", "NEXT"])
def test_each_autonomous_strategy_still_has_legal_provenance_and_cooldown(strategy):
    from types import SimpleNamespace
    from interview_api.modules.interview.adaptive import planning_payload, resolve_selection, validate_decision
    ctx = packet()["snapshot"]
    active = SimpleNamespace(id=1, question="Kafka 消息如何确认？", status="ANSWERED", difficulty="MEDIUM",
        parent_question_id=None, question_kind="NEXT", standard_answer="事务与幂等", source="DYNAMIC_GENERATED",
        candidate_refs_json=dict(adaptive_decision=dict(target_knowledge_id="java_backend:kafka_reliability",
            question_source_ref="skill:java_backend@1.0.0")))
    p = planning_payload(ctx, [active], active, None, answer="消费后 ACK", answer_id=7)
    pair = next(c for c in p["adaptive"]["candidates"] if strategy in c["strategies"])
    raw = selection(p, pair, strategy)
    raw.proposed_question.question = pair["label"] + "的具体机制和异常边界如何设计？"
    result = resolve_selection(raw, p)
    policy = validate_decision(result, p)
    assert policy["target_topic"] == pair["topic"]
    assert result.recommendation == ("FOLLOW_UP" if strategy == "REQUEST_EXAMPLE" else strategy)
    if strategy != "NEXT":
        assert result.target_knowledge_id == "java_backend:kafka_reliability"
        assert result.question.source_question_id is None
    else:
        # R130: NEXT is a new question, not necessarily a new subject. It may take
        # the same target from a different frozen source, but never repeat the
        # exact target+source pair that is already active.
        assert (result.target_knowledge_id != "java_backend:kafka_reliability"
                or result.question_source_ref != "skill:java_backend@1.0.0")


def test_public_and_candidate_sources_resolve_only_the_selected_exact_refs():
    from interview_api.modules.interview.adaptive import build_blueprint, planning_payload, resolve_selection, validate_decision
    ctx = context()
    ctx.update(profile={}, normalized={}, adaptive_policy=dict(decision_contract="TARGET_SOURCE_PAIR_V1"),
        public_questions=[dict(id=17, question="Kafka 消息重复投递怎么办？")],
        candidate_facts=[dict(id=29, field_path="projects.0", statement="Kafka 订单消息", evidence_ids=["real_evidence_key"])],
        candidate_evidence=[dict(key="real_evidence_key", excerpt="Kafka 订单消息", section="projects")])
    ctx["adaptive_blueprint"] = build_blueprint(ctx)
    p = planning_payload(ctx, [], None, None)
    for ref, expected_type in [("public_question:17", "PUBLIC_QUESTION"), ("fact:29", "CANDIDATE_PROFILE")]:
        pair = next(c for c in p["adaptive"]["candidates"] if c["source"]["ref"] == ref)
        artifact = resolve_selection(selection(p, pair), p)
        assert validate_decision(artifact, p)["source_type"] == expected_type
        if expected_type == "PUBLIC_QUESTION":
            assert artifact.question.source_question_id == 17
            assert artifact.question.candidate_fact_ids == []
        else:
            assert artifact.question.candidate_fact_ids == [29]
            assert artifact.question.candidate_evidence_ids == ["real_evidence_key"]


def test_resolved_goal_is_not_offered_again_after_supported_gap_resolution():
    from types import SimpleNamespace
    from interview_api.modules.interview.adaptive import planning_payload, knowledge_state, apply_observations
    ctx = packet()["snapshot"]
    kid = "java_backend:kafka_reliability"
    state = apply_observations(knowledge_state(None), [dict(knowledge_id=kid, state="DEMONSTRATED_IN_SESSION",
        answer_quotes=["提交后 ACK"], missing_mechanisms=[], resolved_gap_ids=["real_previous_gap"],
        open_gaps=[], answer_spans=[dict(start=0, end=7, excerpt="提交后 ACK")])], question_id=1, answer_id=1, evaluation_id=1)
    active = SimpleNamespace(id=1, question="Kafka 如何消费？", status="EVALUATED", difficulty="MEDIUM",
        parent_question_id=None, question_kind="NEXT", standard_answer="事务", source="DYNAMIC_GENERATED",
        candidate_refs_json=dict(adaptive_decision=dict(target_knowledge_id=kid)))
    p = planning_payload(ctx, [active], active, dict(adaptive_knowledge=state))
    assert p["adaptive"]["candidates"]
    assert all(c["target_knowledge_id"] != kid for c in p["adaptive"]["candidates"])


def test_pair_rejection_preserves_answer_and_reuses_evaluation_on_original_key(database_url):
    """Real DB/service integration. Must remain skipped, not faked, when DB is down."""
    import asyncio
    import json
    from uuid import uuid4
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from interview_api.infrastructure.models.provider import ModelResponse
    from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview.models import InterviewAgentCall
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.modules.experience.models import ExperienceAgentRun
    from test_m4_interview import seed
    from v21_support import AdaptiveReplayProvider

    class BadCandidateOnce(AdaptiveReplayProvider):
        bad = False

        async def generate(self, messages, *, schema):
            result = await super().generate(messages, schema=schema)
            p = json.loads(messages[-1]["content"])["input"]
            if schema["title"] == "AdaptiveSelection" and p.get("answer") and not self.bad:
                self.bad = True
                value = json.loads(result.text)
                value["candidate_id"] = "C_CONSTRUCTED_NOT_A_LIVE_RESPONSE"
                return ModelResponse(json.dumps(value), result.usage)
            return result

    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            _, _, _, uid, old = await seed(factory)
            provider = BadCandidateOnce()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))
            snapshot = old["context_snapshot"]
            detail = await core.create(uid, CreateSessionRequest(company_id=snapshot["company_id"], job_id=snapshot["job_id"],
                interviewer_skill_id="java_backend", adaptive_interview=True, question_count=3))
            sid = detail["id"]
            first = await core.start(sid, uid)
            key, answer = uuid4().hex, "我只了解整体思路"
            with pytest.raises(AppError) as caught:
                await core.answer(sid, uid, first["question_id"], key, answer)
            assert caught.value.code == "UNKNOWN_DIRECTOR_CANDIDATE"
            failed = await core.detail(sid, uid)
            assert failed["status"] == "IN_PROGRESS" and failed["turn_count"] == 1
            assert failed["pending_answer"]["retryable"] and failed["pending_answer"]["idempotency_key"] == key
            assert failed["current_question"]["status"] == "ANSWERED" and len(failed["questions"]) == 1
            assert not failed["interview_plan_json"].get("answer_processing")
            assert not any(m["metadata_json"].get("type") == "EVALUATION" for m in failed["messages"] if m["metadata_json"])
            async with factory() as db:
                audit = await db.scalar(select(ExperienceAgentRun).join(InterviewAgentCall,
                    InterviewAgentCall.agent_run_id == ExperienceAgentRun.id).where(
                    InterviewAgentCall.session_id == sid, ExperienceAgentRun.error_message == "UNKNOWN_DIRECTOR_CANDIDATE"))
                assert audit.output_json["validation"]["candidate_id"] == "C_CONSTRUCTED_NOT_A_LIVE_RESPONSE"
            events = await core.answer(sid, uid, first["question_id"], key, answer)
            assert events[0][0] == "evaluation" and events[1][0] == "follow_up"
            assert provider.calls.count("AdaptiveEvaluation") == 1
            assert events == await core.answer(sid, uid, first["question_id"], key, answer)
            recovered = await core.detail(sid, uid)
            assert len([m for m in recovered["messages"] if m["role"] == "USER"]) == 1
            assert len(recovered["questions"]) == 2 and recovered["turn_count"] == 1
            assert recovered["context_snapshot"] == detail["context_snapshot"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_frozen_legacy_and_pair_sessions_execute_without_upgrading_history(database_url):
    """Recreate the old creation contract only at the test fixture boundary."""
    import asyncio
    from unittest.mock import patch
    from uuid import uuid4
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from interview_api.modules.interview import controller
    from interview_api.modules.interview.adaptive_prompts import freeze_prompts
    from interview_api.modules.candidate_profile.evidence import digest
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview.models import InterviewAgentCall
    from interview_api.modules.experience.models import ExperienceAgentStepRun
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
    from test_m4_interview import seed
    from v21_support import AdaptiveReplayProvider

    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            _, _, _, uid, seeded = await seed(factory)
            provider = AdaptiveReplayProvider()
            core = MockInterviewService(factory, ModelRouter({route: provider for route in
                (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))
            snap = seeded["context_snapshot"]
            request = CreateSessionRequest(company_id=snap["company_id"], job_id=snap["job_id"],
                interviewer_skill_id="java_backend", adaptive_interview=True, question_count=2)
            original_builder = controller.build_context

            async def historical_creation(db, user_id, req):
                context, resume_id, _ = await original_builder(db, user_id, req)
                context['adaptive_policy'].pop('gap_scope_contract')
                context['adaptive_policy'].pop('evaluation_context', None)
                context['adaptive_policy'].pop('context_projection', None)
                context["adaptive_policy"].pop("decision_contract")
                context["adaptive_policy"]["prompts"] = freeze_prompts()
                context.pop("memory_guided_plan", None)
                return context, resume_id, digest(context)

            with patch.object(controller, "build_context", historical_creation):
                historical = await core.create(uid, request)
            async def pair_creation(db, user_id, req):
                context, resume_id, _ = await original_builder(db, user_id, req)
                context['adaptive_policy'].pop('gap_scope_contract')
                context['adaptive_policy'].pop('evaluation_context', None)
                context['adaptive_policy'].pop('context_projection', None)
                from interview_api.modules.interview.adaptive_prompts import MAIN_PAIRS, EVALUATION
                context["adaptive_policy"]["prompts"] = {p.name: dict(version=p.version,system=p.system,
                    fingerprint=p.content_hash) for p in (MAIN_PAIRS,EVALUATION)}
                context.pop("memory_guided_plan", None)
                return context, resume_id, digest(context)
            with patch.object(controller, "build_context", pair_creation):
                paired = await core.create(uid, request)
            async def old_memory_creation(db, user_id, req):
                context, resume_id, _ = await original_builder(db, user_id, req)
                context['adaptive_policy'].pop('gap_scope_contract')
                context['adaptive_policy'].pop('evaluation_context', None)
                context['adaptive_policy'].pop('context_projection', None)
                from interview_api.modules.interview.adaptive_prompts import MAIN_MEMORY, EVALUATION
                context["adaptive_policy"]["prompts"] = {p.name: dict(version=p.version,system=p.system,
                    fingerprint=p.content_hash) for p in (MAIN_MEMORY,EVALUATION)}
                return context, resume_id, digest(context)
            with patch.object(controller, "build_context", old_memory_creation):
                old_memory = await core.create(uid, request)
            async def reference_creation(db, user_id, req):
                context, resume_id, _ = await original_builder(db, user_id, req)
                context['adaptive_policy'].pop('gap_scope_contract')
                context['adaptive_policy'].pop('evaluation_context', None)
                from interview_api.modules.interview.adaptive_prompts import MAIN_MEMORY_REFS, EVALUATION
                context['adaptive_policy'].pop('context_projection', None)
                context['adaptive_policy']['prompts'] = {p.name: dict(version=p.version, system=p.system,
                    fingerprint=p.content_hash) for p in (MAIN_MEMORY_REFS, EVALUATION)}
                return context, resume_id, digest(context)
            with patch.object(controller, 'build_context', reference_creation):
                old_references = await core.create(uid, request)
            async def projected_creation(db, user_id, req):
                context, resume_id, _ = await original_builder(db, user_id, req)
                context['adaptive_policy'].pop('gap_scope_contract')
                from interview_api.modules.interview.adaptive_prompts import freeze_prompts
                context['adaptive_policy'].pop('evaluation_context', None)
                context['adaptive_policy']['prompts'] = freeze_prompts(context_projection=True)
                return context, resume_id, digest(context)
            with patch.object(controller, 'build_context', projected_creation):
                old_projected = await core.create(uid, request)
            modern = await core.create(uid, request)
            for detail, version, schema in [(historical, "2.1.0", "AdaptiveDecision"),
                                             (paired, "2.1.1", "AdaptiveSelection"),
                                             (old_memory, "2.2.0", "AdaptiveSelection"),
                                             (old_references, "2.2.1", "AdaptiveSelection"),
                                             (old_projected, "2.3.1", "AdaptiveSelection"),
                                             (modern, "3.0.0", "AdaptiveSelection")]:
                pinned = deepcopy(detail["context_snapshot"])
                sid = detail["id"]
                question = await core.start(sid, uid)
                await core.answer(sid, uid, question["question_id"], uuid4().hex, "我只了解整体思路")
                current = (await core.detail(sid, uid))["current_question"]
                await core.answer(sid, uid, current["question_id"], uuid4().hex,
                    "使用事务和业务唯一键，并处理失败恢复")
                finished = await core.detail(sid, uid)
                assert finished["status"] == "COMPLETED"
                assert finished["context_snapshot"] == pinned
                assert schema in provider.calls
                async with factory() as db:
                    versions = (await db.scalars(select(ExperienceAgentStepRun.prompt_version)
                        .join(InterviewAgentCall, InterviewAgentCall.agent_run_id == ExperienceAgentStepRun.agent_run_id)
                        .where(InterviewAgentCall.session_id == sid,
                               ExperienceAgentStepRun.step_name == "interview_main"))).all()
                    assert versions and set(versions) == {version}
                    evaluation_versions = (await db.scalars(select(ExperienceAgentStepRun.prompt_version)
                        .join(InterviewAgentCall, InterviewAgentCall.agent_run_id == ExperienceAgentStepRun.agent_run_id)
                        .where(InterviewAgentCall.session_id == sid,
                               ExperienceAgentStepRun.step_name == 'answer_evaluation'))).all()
                    assert set(evaluation_versions) == {pinned['adaptive_policy']['prompts']['answer_evaluation']['version']}
        finally:
            await engine.dispose()

    asyncio.run(run())
