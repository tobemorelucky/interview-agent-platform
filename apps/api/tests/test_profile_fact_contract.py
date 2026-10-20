"""Extractive contract fixtures, never real resume/model data."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.provider import CompatibleModelProvider
from interview_api.infrastructure.models import router as routing
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
from interview_api.modules.candidate_profile.context import build_profile_context
from interview_api.modules.candidate_profile.evidence import resolve_and_validate, ProfileEvidenceError, digest
from interview_api.modules.candidate_profile.models import CandidateFact, CandidateProfileVersion, ProfileGeneration
from interview_api.modules.candidate_profile.prompts import CandidatePromptRegistry
from interview_api.modules.candidate_profile.schemas import CandidateProfileDelta, CandidateFactsExtraction, CorrectionRequest
from interview_api.modules.candidate_profile.service import CandidateProfileService, iter_facts
from interview_api.modules.experience.models import ExperienceAgentRun
from test_m3_candidate_profile import seed, response, model_router


EVIDENCE = {
    "real_kafka": dict(key="real_kafka", section="projects", excerpt="DEMO 订单项目使用 Kafka 进行订单异步消费"),
    "real_redis": dict(key="real_redis", section="projects", excerpt="DEMO 缓存项目使用 Redis 实现缓存"),
}
ALIASES = {"real_kafka": "E01", "real_redis": "E02"}


@pytest.mark.parametrize("field,refs,expected", [
    ("使用 Kafka 进行订单异步消费", ["real_kafka"], "PASS"),
    ("负责系统架构设计", ["real_kafka"], "UNSUPPORTED_CANDIDATE_FACT"),
    ("具备 Kafka 异步消息处理实践", ["real_kafka"], "UNSUPPORTED_CANDIDATE_FACT"),
    ("使用 Kafka 进行订单异步消费，使用 Redis 实现缓存", ["real_kafka", "real_redis"], "UNSUPPORTED_CANDIDATE_FACT"),
])
def test_extractive_project_literal_support_unchanged(field, refs, expected):
    delta = CandidateFactsExtraction(projects=[dict(label="DEMO", statement=field, evidence_ids=refs)])
    if expected == "PASS":
        resolve_and_validate(delta, EVIDENCE, stable_only=True)
    else:
        with pytest.raises(ProfileEvidenceError, match=expected):
            resolve_and_validate(delta, EVIDENCE, stable_only=True)


@pytest.mark.parametrize("field,value", [
    ("candidate_summary", dict(statement="DEMO", evidence_ids=["E01"])),
    ("strengths", [dict(statement="DEMO", evidence_ids=["E01"])]),
])
def test_extraction_schema_rejects_derived_analysis(field, value):
    with pytest.raises(ValidationError):
        CandidateFactsExtraction.model_validate({field: value})
    # Historical snapshot contract is still readable, not rewritten.
    assert getattr(CandidateProfileDelta.model_validate({field: value}), field)


@pytest.mark.parametrize("field,value,possible_wrong", [
    ("statement", "使用 Kafka 进行订单异步消费", True),
    ("statement", "不存在的架构职责", False),
    ("label", "Kafka", True),
])
def test_failure_diagnostics_do_not_repair_or_leak_candidate_text(field, value, possible_wrong):
    item = dict(label="DEMO", statement="使用 Redis 实现缓存", evidence_ids=["real_redis"])
    item[field] = value
    delta = CandidateFactsExtraction(projects=[item])
    original = deepcopy(delta.model_dump())
    with pytest.raises(ProfileEvidenceError) as error:
        resolve_and_validate(delta, EVIDENCE, stable_only=True, evidence_aliases=ALIASES)
    diagnostic = error.value.diagnostics
    assert diagnostic["field_path"] == "projects.0." + field
    assert diagnostic["category"] == "projects" and diagnostic["value_field"] == field
    assert diagnostic["cited_aliases"] == ["E02"]
    assert diagnostic["cited_literal_match"] is False
    assert diagnostic["uncited_literal_match"] == possible_wrong
    assert diagnostic["possible_wrong_evidence_reference"] == possible_wrong
    assert value not in json.dumps(diagnostic, ensure_ascii=False)
    assert delta.model_dump() == original


def test_only_profile_route_has_non_thinking_policy(monkeypatch):
    # Default route isolation, independent of local maintainer configuration.
    # Public research has an explicit optional override tested separately.
    monkeypatch.setattr(settings, "public_research_thinking", None)
    monkeypatch.setattr(settings, "answer_evaluation_thinking", None)  # independently configured M4 route
    for name in ("interview_main_thinking", "interview_coach_thinking", "job_matching_thinking"):
        monkeypatch.setattr(settings, name, None)  # route overrides remain independently configurable
    for name in ("llm_api_key", "public_research_api_key", "resume_profile_api_key", "interview_model_api_key", "job_matching_api_key", "interview_coach_api_key"):
        monkeypatch.setattr(settings, name, "fixture-not-a-real-key")
    profile = routing.build_resume_profile_router().primary(routing.RESUME_PROFILE_ROUTE)
    assert profile.thinking_type == "disabled" and profile.timeout == 60
    for router in (routing.build_model_router(), routing.build_interview_router(), routing.build_interview_coach_router(), routing.build_job_matching_router()):
        assert all(provider.thinking_type is None for provider in router.routes.values())


@pytest.mark.parametrize("policy", [None, "disabled"])
def test_actual_provider_request_reasoning_parameter(policy):
    async def run():
        seen = []
        def reply(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            provider = CompatibleModelProvider(base_url="https://fixture.invalid/v1", api_key="fixture", model="fixture",
                timeout_seconds=60, max_output_tokens=6000, client=client, thinking_type=policy)
            await provider.generate([], schema={})
        assert "reasoning_effort" not in seen[0]
        if policy is None:
            assert "thinking" not in seen[0]
        else:
            assert seen[0]["thinking"] == {"type": "disabled"}
        assert seen[0]["max_tokens"] == 6000
    asyncio.run(run())


def test_extraction_prompt_and_legacy_registry():
    registry = CandidatePromptRegistry()
    prompt = registry.get("resume_profile")
    assert prompt.version == "1.0.4" and "Select and classify existing evidence" in prompt.system
    assert "candidate_summary=null and strengths=[]" in registry.get("resume_profile", "1.0.3").system
    for version in ("1.0.0", "1.0.1", "1.0.2", "1.0.3"):
        assert registry.get("resume_profile", version).version == version


def test_persisted_facts_consumers_and_confirmed_regeneration(database_url):
    async def run():
        from interview_api.modules.job_matching.retrieval import profile_snapshot
        from interview_api.modules.interview.context import build_context
        from interview_api.modules.interview.schemas import CreateSessionRequest
        from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion, JobRequirement
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, rid = await seed(factory)
            router, provider = model_router([response(rid), response(rid)])
            agent = ResumeProfileAgent(factory, router)
            generated = await agent.run(rid)
            assert generated["status"] == "COMPLETED", generated
            retry = await agent.run(rid)
            assert retry["reused"] and provider.calls == 1
            async with factory() as db:
                version = await db.get(CandidateProfileVersion, generated["version_id"])
                assert version.summary is None and version.snapshot_json["strengths"] == []
                facts = (await db.scalars(select(CandidateFact).where(CandidateFact.version_id == version.id))).all()
                assert {f.field_path.split(".")[0] for f in facts} == {"skills", "projects", "education", "work_experience", "internship_experience"}
                assert all(f.field_path != "candidate_summary" and not f.field_path.startswith("strengths") for f in facts)
                fixed = await CandidateProfileService(db).correct(uid, CorrectionRequest(base_version_id=version.id,
                    field_path="skills.0.label", new_value="Redis 缓存", reason="DEMO user confirmation"))
                await db.commit()
            result = await agent.run(rid, regeneration_token="fixture-explicit-regeneration")
            assert result["status"] == "COMPLETED" and provider.calls == 2
            async with factory() as db:
                view = await CandidateProfileService(db).serialize(uid)
                assert view["version_number"] == 3
                assert view["snapshot"]["skills"][0]["label"] == "Redis 缓存"
                assert view["provenance"]["skills.0"] == "USER_CONFIRMED"
                matched = await profile_snapshot(db, uid)
                assert {f["category"] for f in matched["facts"]} == {"skills", "projects", "education", "work_experience", "internship_experience"}
                assert matched["summary"] is None
                company = Company(canonical_name="Contract DEMO " + str(rid))
                db.add(company); await db.flush()
                job = Job(company_id=company.id, canonical_title="DEMO engineer")
                db.add(job); await db.flush()
                jd = JobDescriptionVersion(job_id=job.id, version_number=1, raw_text="DEMO requires Redis", publication_status="PUBLISHED")
                db.add(jd); await db.flush()
                db.add(JobRequirement(job_description_version_id=jd.id, requirement_type="SKILL", statement="Redis", normalized_value="Redis"))
                await db.flush()
                context, _, _ = await build_context(db, uid, CreateSessionRequest(company_id=company.id, job_id=job.id))
                assert context["candidate_facts"]
                assert all(f["field_path"].split(".")[0] in {"skills", "projects", "education", "work_experience", "internship_experience"} for f in context["candidate_facts"])
                assert context["profile"]["candidate_summary"] is None and context["profile"]["strengths"] == []
                await db.rollback()
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_agent_failure_audit_contains_field_diagnostics(database_url):
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, rid = await seed(factory)
            body = response(rid)
            body["projects"][0]["label"] = "DEMO nonexistent architecture claim"
            router, provider = model_router([body])
            result = await ResumeProfileAgent(factory, router).run(rid)
            assert result["status"] == "FAILED" and result["error"] == "UNSUPPORTED_CANDIDATE_FACT"
            assert provider.calls == 1
            async with factory() as db:
                assert await CandidateProfileService(db).current(uid) is None
                generation = await db.scalar(select(ProfileGeneration).where(ProfileGeneration.resume_id == rid))
                run = await db.get(ExperienceAgentRun, generation.agent_run_id)
                diagnostic = run.output_json["validation_failure"]
                assert diagnostic["field_path"] == "projects.0.label" and diagnostic["cited_aliases"]
                assert "nonexistent architecture" not in json.dumps(run.output_json)
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_legacy_display_path_lookup_not_promoted_to_new_stable_facts():
    legacy = response(7, aliases=False)
    assert "candidate_summary" in dict(iter_facts(legacy))
    assert "strengths.0" in dict(iter_facts(legacy))
    stable = dict(iter_facts(legacy, stable_only=True))
    assert "candidate_summary" not in stable and "strengths.0" not in stable


@pytest.mark.parametrize("field,value", [
    ("candidate_summary", dict(statement="DEMO", evidence_ids=[])),
    ("strengths", [dict(statement="DEMO", evidence_ids=[])]),
])
def test_service_rejects_new_analysis_without_switching_current(database_url, field, value):
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, rid = await seed(factory)
            router, _ = model_router([response(rid)])
            result = await ResumeProfileAgent(factory, router).run(rid)
            async with factory() as db:
                current = await CandidateProfileService(db).current(uid)
                original = deepcopy(current.snapshot_json)
                snapshot = deepcopy(original)
                # A legitimate raw reference still cannot make synthesis enter
                # the stable write path. Use a valid ID to reach the domain gate.
                value_with_ref = deepcopy(value)
                fact = value_with_ref if field == "candidate_summary" else value_with_ref[0]
                fact["evidence_ids"] = original["skills"][0]["evidence_ids"]
                snapshot[field] = value_with_ref
                with pytest.raises(AppError) as error:
                    await CandidateProfileService(db).persist(user_id=uid, resume_id=rid, snapshot=snapshot,
                        generation_key=digest(["invalid-analysis", rid, field]), base_version_id=current.id, producer="REPLAY_FIXTURE")
                assert error.value.code == "DERIVED_PROFILE_ANALYSIS_NOT_ALLOWED"
                await db.rollback()
                assert (await CandidateProfileService(db).current(uid)).id == result["version_id"]
                assert (await db.get(CandidateProfileVersion, result["version_id"])).snapshot_json == original
        finally:
            await engine.dispose()
    asyncio.run(run())
