import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interview_api.modules.models  # noqa: F401
from interview_api.core.config import settings
from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, EXTRACT_ROUTE, QUALITY_ROUTE
from interview_api.modules.experience.agents.service import ExperienceAgentService
from interview_api.modules.experience.models import (
    ExperienceCollectionTask, ExperienceSourceItem, ExperienceAgentRun, ExperienceAgentStepRun,
    InterviewExperience, InterviewQuestion,
)
from interview_api.modules.public_career.models import (
    PublicDataRefreshTask, SourceSnapshot, Company, CompanyAlias, Job, Claim, ClaimEvidence,
    JobDescriptionVersion, JobRequirement,
)
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
from interview_api.modules.public_career.research.evidence import EvidenceCatalog
from interview_api.modules.public_career.refresh_service import PublicDataRefreshService
from interview_api.modules.public_career.tools.factory import build_tool_gateway
from interview_api.modules.public_career.tools.replay import ReplayFetchProvider, ReplaySearchProvider
from interview_api.modules.public_career.tool_models import ToolInvocation


def router(responses):
    provider = ReplayModelProvider(responses)
    return ModelRouter({EXTRACT_ROUTE: provider, QUALITY_ROUTE: provider})


async def collect(factory, target, urls, body):
    gateway = build_tool_gateway(factory,
        search_provider=ReplaySearchProvider([{"url": url, "title": "Public career data"} for url in urls]),
        fetch_provider=ReplayFetchProvider({url: {"content": body, "title": "Fetched body"} for url in urls}))
    async with factory() as db:
        task = PublicDataRefreshTask(target_type=target, target_ref=uuid4().hex, reason="test")
        db.add(task)
        await db.commit()
        await PublicDataRefreshService(db, gateway).execute_to_ready(task, query="career", time_window_hours=24, max_results=5)
        assert task.status == "READY_FOR_EXTRACTION"
        snapshots = await PublicDataResearchAgent(factory)._snapshots(db, task.id)
        return task.id, snapshots, EvidenceCatalog(snapshots)


def job_response(company, ids):
    return {"company_name": company, "job_title": "Backend Engineer",
            "identity_evidence_ids": ids, "jd_evidence_ids": ids,
            "requirements": [{"statement": "Python experience", "evidence_ids": ids,
                              "requirement_type": "SKILL", "importance": "REQUIRED"}]}


async def job_scenario(url):
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    company = "Official" + uuid4().hex[:12]
    body = f"{company} Backend Engineer. Python experience required. " + "Engineering role. " * 25
    saved_trust = settings.public_research_official_domains
    settings.public_research_official_domains = {company.lower(): ["careers.example"]}
    try:
        outputs = []
        for body_version in (body, body, body + "New requirement in version two."):
            task_id, snapshots, catalog = await collect(factory, "JOB_JD", [
                f"https://careers.example/{company}", f"https://mirror.careers.example/{company}",
            ], body_version)
            ids = [span.evidence_id for span in catalog.spans.values() if span.start == 0]
            result = await PublicDataResearchAgent(factory, router([
                job_response(company, ids), {"suitable": True, "risk_flags": []},
            ])).run(task_id)
            assert result["status"] == "COMPLETED", result
            assert result["publication_status"] == "APPROVED"
            assert result["independent_content_count"] == result["source_count"] == 1
            assert result["occurrence_count"] == 2 and not result["multi_source"]
            outputs.append(result)
            async with factory() as db:
                task = await db.get(PublicDataRefreshTask, task_id)
                assert task.completed_at is not None and task.error_message is None
                run = await db.get(ExperienceAgentRun, result["agent_run_id"])
                assert run.refresh_task_id == task_id and run.agent_role == "PublicDataResearchAgent"
                assert run.status == "SUCCEEDED" and run.source_item_id is None
                steps = list((await db.scalars(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == run.id))).all())
                assert len(steps) == 2
                assert all(step.provider_name == "replay" and step.model_name == "fixture" and step.prompt_version == "1.0.0" for step in steps)
                assert all(step.latency_ms is not None and step.structured_retry == 0 and step.token_usage_json["total_tokens"] == 0 for step in steps)
                calls = list((await db.scalars(select(ToolInvocation).where(ToolInvocation.refresh_task_id == task_id))).all())
                assert len(calls) == 3 and all(call.provider_mode == "REPLAY" for call in calls)
        assert outputs[0]["jd_version_id"] == outputs[1]["jd_version_id"]
        assert outputs[2]["jd_version_id"] != outputs[0]["jd_version_id"]
        async with factory() as db:
            jds = list((await db.scalars(select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == outputs[0]["job_id"]).order_by(JobDescriptionVersion.version_number))).all())
            assert [jd.version_number for jd in jds] == [1, 2]
            assert jds[0].raw_text == body
            requirements = list((await db.scalars(select(JobRequirement).where(JobRequirement.job_description_version_id.in_([jd.id for jd in jds])))).all())
            assert len(requirements) == 2
            for requirement in requirements:
                assert requirement.claim_id is not None
                claim = await db.get(Claim, requirement.claim_id)
                assert claim.statement == "Python experience"
                evidence = list((await db.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == claim.id))).all())
                assert evidence
                for row in evidence:
                    snapshot = await db.get(SourceSnapshot, row.source_snapshot_id)
                    loc = row.source_location_json
                    assert snapshot.content_text[loc["start_char"]:loc["end_char"]] == row.excerpt
            assert (await db.get(Job, outputs[0]["job_id"])).company_id == outputs[0]["company_id"]
    finally:
        settings.public_research_official_domains = saved_trust
        await engine.dispose()


def test_job_jd_full_replay_versions_evidence_audit_and_dedupe(database_url, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Replay accessed HTTP network")
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "public_research_api_key", "")
    asyncio.run(job_scenario(database_url))


async def failure_or_review(url, failure):
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    company = "Community" + uuid4().hex[:12]
    try:
        task_id, snapshots, catalog = await collect(factory, "JOB_JD", [f"https://community.example/{company}"],
                                                    f"{company} Backend Engineer Python experience. " * 20)
        ids = [next(iter(catalog.spans))]
        data = job_response(company, ids)
        responses = [data, {"suitable": True}]
        if failure == "unknown_evidence":
            data["requirements"][0]["evidence_ids"] = ["ev_" + "0" * 24]
        elif failure == "missing_evidence":
            data["requirements"][0]["evidence_ids"] = []
            responses = [data, data]
        elif failure == "identity":
            data["company_name"] = "Unsupported" + company
        result = await PublicDataResearchAgent(factory, router(responses)).run(task_id)
        async with factory() as db:
            task = await db.get(PublicDataRefreshTask, task_id)
            run = await db.get(ExperienceAgentRun, result["agent_run_id"])
            if failure:
                assert result["status"] == task.status == "FAILED", result
                assert run.status == "FAILED" and task.error_message and task.completed_at
                assert await db.scalar(select(Company.id).where(Company.normalized_name == company.lower())) is None
                if failure == "missing_evidence":
                    steps = list((await db.scalars(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == run.id))).all())
                    assert len(steps) == 1 and steps[0].status == "FAILED" and steps[0].structured_retry == 1
            else:
                assert result["status"] == "COMPLETED", result
                assert result["publication_status"] == "WAITING_REVIEW"
                assert (await db.get(Company, result["company_id"])).status == "WAITING_REVIEW"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("failure", [None, "unknown_evidence", "missing_evidence", "identity"])
def test_community_gate_and_failed_refresh(database_url, failure):
    asyncio.run(failure_or_review(database_url, failure))


def interview_responses(company, ids):
    questions = ["请解释缓存穿透的处理方案？", "数据库索引为什么使用B加树？"]
    extraction = {"is_interview_experience": True, "company": company, "position": "Backend Engineer",
                  "experience_summary": "一轮后端技术面试，考察缓存和数据库索引。", "extraction_confidence": 0.95,
                  "identity_evidence_ids": ids, "evidence_ids": ids,
                  "questions": [{"question": question, "question_type": "TECHNICAL", "confidence": 0.95,
                                 "evidence_ids": ids} for question in questions]}
    routing = {"company": company, "position": "Backend Engineer", "overall_job_direction": "BACKEND",
               "routing_confidence": 0.95, "question_results": [
                   {"question_index": i, "normalized_question": question, "routing_confidence": 0.95,
                    "question_type": "BASIC_KNOWLEDGE", "job_direction": "BACKEND"}
                   for i, question in enumerate(questions)]}
    reliability = {"is_reliable": True, "reliability_score": 0.9, "content_quality_score": 0.9,
                   "source_quality_score": 0.9, "spam_risk_score": 0.1, "publish_recommendation": "APPROVE"}
    return [extraction, routing, reliability]


async def interview_scenario(url, target="INTERVIEW_DATA"):
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    company = "Interview" + uuid4().hex[:12]
    body = f"{company} Backend Engineer 一轮面经。请解释缓存穿透的处理方案？数据库索引为什么使用B加树？" + "面试官围绕项目设计提问，候选人解释缓存和数据库的取舍。" * 20
    urls = [f"https://community.example/{company}", f"https://repost.example/{company}"]
    try:
        task_id, snapshots, catalog = await collect(factory, target, urls, body)
        async with factory() as db:
            public_company = Company(canonical_name=company)
            db.add(public_company)
            await db.flush()
            public_job = Job(company_id=public_company.id, canonical_title="Backend Engineer")
            db.add(public_job)
            legacy = ExperienceCollectionTask(public_refresh_task_id=task_id, status="FETCH_COMPLETED")
            db.add(legacy)
            await db.flush()
            # Existing UI source remains the entry point.
            from interview_api.modules.experience.search.url_utils import hash_url
            source = ExperienceSourceItem(task_id=legacy.id, source_url=urls[0], normalized_url_hash=hash_url(urls[0]),
                                          raw_text=body, fetch_status="FETCHED")
            db.add(source)
            await db.commit()
            source_id, company_id, job_id = source.id, public_company.id, public_job.id
        ids = [span.evidence_id for span in catalog.spans.values() if span.start == 0]
        model_router = router(interview_responses(company, ids))
        async with factory() as db:
            result = await ExperienceAgentService(db, research_router=model_router).run_extraction_for_source(source_id)
            assert result["experience_id"] is not None, result
            assert result["review_status"] == "WAITING_REVIEW"  # even model APPROVE must not publish
            assert result["question_count"] == 2
            assert not result["skipped"]
            again = await ExperienceAgentService(db, research_router=model_router).run_extraction_for_source(source_id)
            assert again["skipped"] and again["experience_id"] == result["experience_id"]
        async with factory() as db:
            exp = await db.get(InterviewExperience, result["experience_id"])
            assert exp.company == company and exp.position == "Backend Engineer"
            assert exp.company_id == company_id and exp.job_id == job_id
            assert exp.source_snapshot_id and exp.claim_id
            assert exp.reliability_json["independent_content_count"] == exp.reliability_json["source_count"] == 1
            assert exp.reliability_json["occurrence_count"] == 2 and not exp.reliability_json["multi_source"]
            questions = list((await db.scalars(select(InterviewQuestion).where(InterviewQuestion.experience_id == exp.id))).all())
            assert len(questions) == 2
            assert all(q.claim_id and q.question_fingerprint and q.company_id == company_id and q.job_id == job_id for q in questions)
            assert all(q.index_status == "NOT_INDEXED" and q.review_status == "WAITING_REVIEW" for q in questions)
            assert all(q.routing_json["source_count"] == 1 for q in questions)
            for question in questions:
                rows = list((await db.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == question.claim_id))).all())
                assert len(rows) == 2  # both mirror occurrences have exact provenance
                assert question.evidence == sorted(rows, key=lambda row: row.evidence_key)[0].excerpt[:500]
            run = await db.get(ExperienceAgentRun, result["agent_run_id"])
            assert run.refresh_task_id == task_id and run.source_item_id == source_id
            assert await db.scalar(select(func.count(ExperienceAgentRun.id)).where(ExperienceAgentRun.refresh_task_id == task_id)) == 1
            steps = list((await db.scalars(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == run.id))).all())
            assert {step.step_name for step in steps} == {"interview_extract", "interview_routing", "interview_reliability"}
            assert (await db.get(PublicDataRefreshTask, task_id)).status == "COMPLETED"
        # Explicit legacy force reruns use the same public state boundary and
        # only replace an unapproved draft. Published records are protected.
        async with factory() as db:
            forced = await ExperienceAgentService(db, research_router=router(interview_responses(company, ids))).run_extraction_for_source(source_id, force=True)
            assert not forced["skipped"] and forced["experience_id"] != result["experience_id"]
            assert await db.scalar(select(func.count(InterviewExperience.id)).where(InterviewExperience.source_item_id == source_id)) == 1
            current = await db.get(InterviewExperience, forced["experience_id"])
            current.review_status = "APPROVED"
            await db.commit()
        blocked = await PublicDataResearchAgent(factory, router(interview_responses(company, ids))).run(task_id, force=True)
        assert blocked["status"] == "FAILED" and "approved" in blocked["error"]
        async with factory() as db:
            assert (await db.get(InterviewExperience, forced["experience_id"])).review_status == "APPROVED"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("target", ["INTERVIEW_DATA", "EXPERIENCE_COLLECTION_TASK"])
def test_interview_legacy_pipeline_entry_review_provenance_and_counts(database_url, monkeypatch, target):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Replay accessed HTTP network")
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    asyncio.run(interview_scenario(database_url, target))


async def multiple_groups(url):
    from interview_api.modules.public_career.dedupe import group_independent_content
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    company = "Separate" + uuid4().hex[:12]
    bodies = [f"{company} Backend Engineer 请解释缓存穿透的处理方案？数据库索引为什么使用B加树？" + (f"第{i}轮的不同面经正文。" * 40) for i in (1, 2)]
    urls = [f"https://community.example/{company}/{i}" for i in (1, 2)]
    gateway = build_tool_gateway(factory, search_provider=ReplaySearchProvider([{"url": url, "title": "Interview"} for url in urls]),
                                 fetch_provider=ReplayFetchProvider({url: {"content": body} for url, body in zip(urls, bodies)}))
    try:
        async with factory() as db:
            task = PublicDataRefreshTask(target_type="INTERVIEW_DATA")
            db.add(task)
            await db.commit()
            await PublicDataRefreshService(db, gateway).execute_to_ready(task, query="interview", time_window_hours=24, max_results=5)
            snapshots = await PublicDataResearchAgent(factory)._snapshots(db, task.id)
            task_id = task.id
        responses = []
        by_id = {snapshot.id: snapshot for snapshot in snapshots}
        for group in group_independent_content(snapshots).groups:
            catalog = EvidenceCatalog([by_id[sid] for sid in group.snapshot_ids])
            responses.extend(interview_responses(company, [next(iter(catalog.spans))]))
        result = await PublicDataResearchAgent(factory, router(responses)).run(task_id)
        assert result["status"] == "COMPLETED", result
        assert len(result["experiences"]) == 2
        assert result["collection_metrics"]["independent_content_count"] == 2
        assert result["collection_metrics"]["multi_source"]
        assert all(item["independent_content_count"] == 1 for item in result["experiences"])
        async with factory() as db:
            exps = list((await db.scalars(select(InterviewExperience).where(InterviewExperience.id.in_([item["experience_id"] for item in result["experiences"]])))).all())
            assert len(exps) == 2 and {exp.content_text for exp in exps} == set(bodies)
            assert all(exp.review_status == "WAITING_REVIEW" for exp in exps)
            assert all(exp.company_id is None and exp.job_id is None for exp in exps)
    finally:
        await engine.dispose()


def test_distinct_interviews_are_not_merged_or_mistaken_for_mutual_support(database_url):
    asyncio.run(multiple_groups(database_url))


async def not_interview(url):
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        task_id, snapshots, catalog = await collect(factory, "INTERVIEW_DATA", [f"https://community.example/{uuid4().hex}"],
                                                    "This is a recruiting advertisement, not an interview experience. " * 20)
        result = await PublicDataResearchAgent(factory, router([
            {"is_interview_experience": False, "extraction_confidence": 0.95,
             "evidence_ids": [next(iter(catalog.spans))]},
        ])).run(task_id)
        assert result["status"] == "COMPLETED" and result["experience_id"] is None, result
        async with factory() as db:
            item = await db.get(ExperienceSourceItem, result["source_item_id"])
            assert item.extract_status == "NOT_EXPERIENCE"
            assert await db.scalar(select(func.count(ClaimEvidence.id)).where(ClaimEvidence.source_snapshot_id == snapshots[0].id)) == 0
    finally:
        await engine.dispose()


def test_non_interview_keeps_legacy_rejection_semantics_without_claims(database_url):
    asyncio.run(not_interview(database_url))


async def rollback_or_missing_body(url, missing_body):
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    company = "Atomic" + uuid4().hex[:12]
    try:
        if missing_body:
            async with factory() as db:
                task = PublicDataRefreshTask(target_type="JOB_JD", status="READY_FOR_EXTRACTION")
                db.add(task)
                await db.commit()
                task_id, snapshots = task.id, []
            model_router = router([])
        else:
            task_id, snapshots, catalog = await collect(factory, "JOB_JD", [f"https://community.example/{company}"],
                                                        f"{company} Backend Engineer Python experience. " * 20)
            model_router = router([job_response(company, [next(iter(catalog.spans))]), {"suitable": True}])

        class FailAfterWrite(PublicDataResearchAgent):
            async def _publish_job(self, *args, **kwargs):
                await super()._publish_job(*args, **kwargs)
                raise RuntimeError("intentional failure after domain writes")

        agent_class = PublicDataResearchAgent if missing_body else FailAfterWrite
        result = await agent_class(factory, model_router).run(task_id)
        assert result["status"] == "FAILED", result
        async with factory() as db:
            assert await db.scalar(select(Company.id).where(Company.normalized_name == company.lower())) is None
            assert await db.scalar(select(func.count(ClaimEvidence.id)).where(ClaimEvidence.source_snapshot_id.in_([s.id for s in snapshots]))) == 0
            run = await db.get(ExperienceAgentRun, result["agent_run_id"])
            assert run.status == "FAILED" and run.error_message
            step_count = await db.scalar(select(func.count(ExperienceAgentStepRun.id)).where(ExperienceAgentStepRun.agent_run_id == run.id))
            assert step_count == (0 if missing_body else 2)
            if not missing_body:
                assert await db.get(SourceSnapshot, snapshots[0].id) is not None
    finally:
        await engine.dispose()


@pytest.mark.parametrize("missing_body", [False, True])
def test_atomic_publish_rollback_and_no_fetch_evidence_rejection(database_url, missing_body):
    asyncio.run(rollback_or_missing_body(database_url, missing_body))


async def alias_scenario(url, ambiguous):
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    alias = "Brand" + uuid4().hex[:12]
    try:
        async with factory() as db:
            parent = Company(canonical_name="Parent" + uuid4().hex[:12], official_domain="careers.example")
            db.add(parent)
            await db.flush()
            db.add(CompanyAlias(company_id=parent.id, alias=alias))
            parent_id = parent.id
            if ambiguous:
                other = Company(canonical_name="Other" + uuid4().hex[:12])
                db.add(other)
                await db.flush()
                db.add(CompanyAlias(company_id=other.id, alias=alias))
            await db.commit()
        task_id, snapshots, catalog = await collect(factory, "JOB_JD", [f"https://careers.example/{alias}"],
                                                    f"{alias} Backend Engineer Python experience. " * 20)
        result = await PublicDataResearchAgent(factory, router([
            job_response(alias, [next(iter(catalog.spans))]), {"suitable": True},
        ])).run(task_id)
        async with factory() as db:
            assert await db.scalar(select(Company.id).where(Company.normalized_name == alias.lower())) is None
            if ambiguous:
                assert result["status"] == "FAILED" and "Ambiguous" in result["error"]
            else:
                assert result["status"] == "COMPLETED" and result["company_id"] == parent_id, result
                assert result["publication_status"] == "APPROVED"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("ambiguous", [False, True])
def test_jd_company_alias_exact_unique_resolution_or_ambiguity_rejection(database_url, ambiguous):
    asyncio.run(alias_scenario(database_url, ambiguous))
