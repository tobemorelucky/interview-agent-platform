"""Offline Replay/DEMO matching tests with real PostgreSQL persistence."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
import httpx
import pytest
from sqlalchemy import select, update, delete, func, text
from sqlalchemy.exc import DBAPIError
from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, JOB_MATCHING_ROUTE, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
from interview_api.modules.job_matching.service import JobMatchingService
from interview_api.modules.job_matching.schemas import MatchRequest, MatchArtifact, MatchFilters
from interview_api.modules.job_matching.policy import eligibility, contains_literal, ranking_components
from interview_api.modules.job_matching.resolver import resolve_artifact
from interview_api.modules.job_matching.models import JobMatchRun, JobMatchCandidate, JobMatchRecommendation, JobMatchAttempt
from interview_api.modules.job_matching.preferences import approved_preferences
from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion, JobRequirement, JobTag
from interview_api.modules.experience.models import InterviewQuestion, ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
from interview_api.modules.candidate_profile.service import CandidateProfileService
from interview_api.modules.candidate_profile.schemas import CorrectionRequest
from interview_api.modules.memory.service import MemoryService
from interview_api.modules.memory.models import UserMemoryItem, UserMemoryEvent
from interview_api.modules.memory.policies import MemoryActor
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from test_m3_candidate_profile import seed as seed_resume, model_router, response
from test_m4_interview import run_with_database, queue_start


async def seed_matching(factory):
    uid, rid = await seed_resume(factory)
    router, _ = model_router([response(rid)])
    generated = await ResumeProfileAgent(factory, router).run(rid)
    assert generated["status"] == "COMPLETED"
    definitions = json.loads((Path(__file__).parent / "fixtures/m6_jobs_demo.json").read_text(encoding="utf-8"))["jobs"]
    async with factory() as db:
        company = Company(canonical_name="M6 DEMO REPLAY " + uuid4().hex)
        db.add(company); await db.flush()
        jobs = []
        for index, data in enumerate(definitions):
            job = Job(company_id=company.id, canonical_title=data["title"], job_family=data["family"], campus_type="CAMPUS")
            db.add(job); await db.flush()
            jd = JobDescriptionVersion(job_id=job.id, version_number=1, raw_text=f"REPLAY fixture JD {index} " + " / ".join(data["topics"]),
                publication_status="PUBLISHED", metadata_json={"fixture": True, "mode": "replay"},
                structured_data_json={"matching_constraints": {"locations": [data["location"]], "education_minimum": data["qualification"], "majors": [], "graduation_years": []}})
            db.add(jd); await db.flush()
            for topic in data["topics"]:
                db.add(JobRequirement(job_description_version_id=jd.id, statement=f"岗位要求涉及 {topic}", normalized_value=topic, requirement_type="SKILL"))
                db.add(JobTag(job_id=job.id, tag=topic))
            db.add(InterviewQuestion(company_id=company.id, job_id=job.id, question=f"REPLAY {index}: Python Redis 的实现与取舍？", review_status="APPROVED", difficulty="MEDIUM", category="工程"))
            jobs.append(dict(job_id=job.id, company_id=company.id, jd_id=jd.id))
        await db.commit()
        others = list((await db.scalars(select(Company.id).where(Company.id != company.id))).all())
        filters = MatchFilters(locations=["北京"], campus_types=["CAMPUS"], exclude_company_ids=others)
        actor = MemoryActor("USER", uid)
        preference = await MemoryService(db).create_memory(uid, dict(memory_type="PREFERENCE", content="用户确认：倾向北京 AI 应用岗位"), actor)
    provider = ReplayModelProvider([])
    core = JobMatchingService(factory, ModelRouter({JOB_MATCHING_ROUTE: provider}))
    request = MatchRequest(request_key=uuid4().hex, filters=filters)
    run = await core.create(uid, request)
    return core, provider, uid, jobs, preference, request, run


def artifact(run, *, reverse=False):
    candidates = list(run["snapshot"]["candidates"])
    if reverse: candidates.reverse()
    output = []
    for c in candidates:
        reasons, strengths, gaps = [], [], []
        for row in c["evidence_matrix"]:
            if row["status"] in ("PARTIAL", "NO_EVIDENCE"): gaps.append(row["id"])
            if row["supports"]:
                s = row["supports"][0]
                item = dict(alignment_id=row["id"], candidate_fact_id=s["fact_ref"], job_requirement_id=row["requirement_ref"],
                    evidence_ids=s["evidence_ids"], explanation=s["explanation"])
                reasons.append(item)
                if s["status"] == "SUPPORTED": strengths.append(item)
        output.append(dict(job_id=f"job:{c['job_id']}", fit_band=c["ranking_components"]["fit_band_ceiling"],
            matched_strengths=strengths, alignment_reasons=reasons, current_evidence_gaps=gaps,
            preparation_focus=gaps, unknowns_to_confirm=c["unknown_constraints"]))
    return dict(ordered_candidate_job_ids=[r["job_id"] for r in output], recommendations=output)


async def replay_e2e(factory):
    core, provider, uid, jobs, preference, request, run = await seed_matching(factory)
    assert len(run["candidates"]) == 2
    assert run["snapshot"]["data_mode"] == "DEMO_REPLAY"
    ai, java, risk = jobs
    byjob = {c["job_id"]: c for c in run["candidates"]}
    assert byjob[ai["job_id"]]["eligibility_status"] == "SATISFIED"
    assert byjob[java["job_id"]]["eligibility_status"] == "UNKNOWN"
    assert byjob[java["job_id"]]["ranking_components"]["fit_band_ceiling"] == "EXPLORATORY"
    excluded = next(c for c in run["excluded_candidates"] if c["job_id"] == risk["job_id"])
    assert excluded["eligibility_status"] == "NOT_SATISFIED" and excluded["pre_rank"] is None
    assert all(c["job_id"] != risk["job_id"] for c in run["snapshot"]["candidates"])
    provider.responses.append(artifact(run, reverse=True))
    final = await core.generate(uid, run["id"])
    assert final["status"] == "COMPLETED", final["error"]
    assert final["producer"] == "REPLAY_FIXTURE" and provider.calls == 1
    assert [c["job_id"] for c in final["candidates"]] == [ai["job_id"], java["job_id"]]
    assert [c["agent_rank"] for c in final["candidates"]] == [2, 1]
    assert await core.generate(uid, run["id"]) == final and provider.calls == 1
    assert (await core.create(uid, request))["id"] == run["id"]
    pinned = deepcopy(final)
    async with factory() as db:
        profile = await CandidateProfileService(db).correct(uid, CorrectionRequest(base_version_id=run["profile_version_id"],
            field_path="skills.0.label", new_value="Redis 缓存", reason="DEMO 用户纠正"))
        await db.commit()
        await MemoryService(db).update_memory(uid, preference["id"], dict(content="用户确认：现在倾向后端岗位"), MemoryActor("USER", uid))
        jd = JobDescriptionVersion(job_id=ai["job_id"], version_number=2, raw_text="DEMO REPLAY JD V2 Python Redis 新要求",
            publication_status="PUBLISHED", metadata_json={"fixture": True})
        db.add(jd); await db.flush()
        new_jd = jd.id
        db.add(JobRequirement(job_description_version_id=jd.id, statement="Redis 新要求", requirement_type="SKILL", normalized_value="Redis"))
        await db.commit()
    assert await core.detail(uid, run["id"]) == pinned
    main, evaluator = ReplayModelProvider([]), ReplayModelProvider([])
    m4 = MockInterviewService(factory, ModelRouter({INTERVIEW_MAIN_ROUTE: main, ANSWER_EVALUATION_ROUTE: evaluator}))
    # Reference-only handoff to unchanged M4 creation rules.
    session = await m4.create(uid, CreateSessionRequest(company_id=ai["company_id"], job_id=ai["job_id"], question_count=6))
    context = session["context_snapshot"]
    assert context["candidate_profile_version_id"] == profile["id"] != run["profile_version_id"]
    assert context["job_description_version_id"] == new_jd != ai["jd_id"]
    assert "现在倾向后端" in json.dumps(context, ensure_ascii=False)
    await queue_start(m4, main, uid, session["id"])
    async with factory() as db:
        await db.execute(update(Job).where(Job.id == ai["job_id"]).values(status="CLOSED")); await db.commit()
        steps = (await db.scalars(select(ExperienceAgentStepRun).join(ExperienceAgentRun,
            ExperienceAgentRun.id == ExperienceAgentStepRun.agent_run_id).where(ExperienceAgentRun.agent_role == "JobMatchingAgent",
            ExperienceAgentRun.input_hash == run["input_fingerprint"]))).all()
        assert steps[0].provider_name == "replay" and steps[0].prompt_version == "1.0.0"
    with pytest.raises(AppError) as error:
        await m4.create(uid, CreateSessionRequest(company_id=ai["company_id"], job_id=ai["job_id"]))
    assert error.value.status_code in (409, 422)
    assert await core.detail(uid, run["id"]) == pinned
    return dict(mode="DEMO / REPLAY / fixture", user_id=uid, run_id=run["id"], session_id=session["id"],
        profile_version_at_match=run["profile_version_id"], profile_version_at_interview=profile["id"],
        jd_at_match=ai["jd_id"], jd_at_interview=new_jd, candidates=[dict(job_id=c["job_id"], title=c["title"],
            eligibility=c["eligibility_status"], band=c["recommendation"]["fit_band"], pre=c["pre_rank"], agent=c["agent_rank"], final=c["final_rank"]) for c in final["candidates"]],
        excluded_job_id=risk["job_id"], input_fingerprint=run["input_fingerprint"], historical_run_unchanged=True, closed_job_rejected=True)


def test_three_jobs_replay_history_m4_handoff(database_url):
    with patch("httpx.AsyncClient.request", side_effect=AssertionError("Replay must not access external network")):
        result = asyncio.run(run_with_database(database_url, replay_e2e))
    assert result["historical_run_unchanged"]


def test_literal_matching_not_semantic():
    assert contains_literal("项目使用 Python 和 Redis", "Redis")
    assert not contains_literal("JavaScript", "Java")
    assert not contains_literal("缓存系统", "Redis")


@pytest.mark.parametrize("qualification,status", [(None, "SATISFIED"), ("硕士", "UNKNOWN"), ([], "SATISFIED")])
def test_qualification_no_free_text_guess(qualification, status):
    job = dict(status="ACTIVE", job_id=1, company_id=1, tags=[], matching_constraints=dict(education_minimum=qualification, majors=[], graduation_years=[]))
    assert eligibility(job, MatchFilters().model_dump())["eligibility_status"] == status
    job["matching_constraints"].pop("education_minimum")
    assert eligibility(job, MatchFilters().model_dump())["eligibility_status"] == "UNKNOWN"


@pytest.mark.parametrize("attack", ["job", "evidence", "reason", "requirement", "band", "unknown", "gap", "score", "eligibility"])
def test_reference_and_policy_attacks_rejected(database_url, attack):
    async def run(factory):
        _, _, _, jobs, _, _, created = await seed_matching(factory)
        body = artifact(created)
        if attack == "job": body["ordered_candidate_job_ids"][0] = f"job:{jobs[2]['job_id']}"
        if attack == "evidence": body["recommendations"][0]["alignment_reasons"][0]["evidence_ids"] = ["re_invented"]
        if attack == "reason": body["recommendations"][0]["alignment_reasons"][0]["explanation"] = "候选人精通高并发"
        if attack == "requirement": body["recommendations"][0]["alignment_reasons"][0]["job_requirement_id"] = "requirement:999999"
        if attack == "band": body["recommendations"][1]["fit_band"] = "STRONG_MATCH"
        if attack == "unknown": body["recommendations"][1]["unknowns_to_confirm"] = []
        if attack == "gap": body["recommendations"][1]["current_evidence_gaps"] = []
        if attack == "score": body["recommendations"][0]["match_score"] = 87.4
        if attack == "eligibility": body["recommendations"][1]["eligibility_status"] = "SATISFIED"
        with pytest.raises(ValueError):
            resolve_artifact(MatchArtifact.model_validate(body), created["snapshot"]["candidates"], created["snapshot"]["profile"])
    asyncio.run(run_with_database(database_url, run))


@pytest.mark.parametrize("failure", [False, True])
def test_repair_and_hard_fail_retry(database_url, failure):
    async def run(factory):
        core, provider, uid, _, _, _, created = await seed_matching(factory)
        body = artifact(created)
        provider.responses.extend([json.dumps(body, ensure_ascii=False)[:-1], "not json" if failure else body])
        final = await core.generate(uid, created["id"])
        assert final["status"] == ("FAILED" if failure else "COMPLETED") and provider.calls == 2
        async with factory() as db:
            attempt = await db.scalar(select(JobMatchAttempt).where(JobMatchAttempt.run_id == created["id"]))
            step = await db.scalar(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == attempt.agent_run_id))
            assert step.structured_retry == 1 and step.status == ("FAILED" if failure else "SUCCEEDED")
        if failure:
            assert all(c["recommendation"] is None for c in final["candidates"])
            provider.responses.append(body)
            retried = await core.generate(uid, created["id"])
            assert retried["status"] == "COMPLETED" and len(retried["attempts"]) == 2
    asyncio.run(run_with_database(database_url, run))


def test_snapshot_database_immutable_and_no_partial_completion(database_url):
    async def run(factory):
        core, provider, uid, _, _, _, created = await seed_matching(factory)
        async with factory() as db:
            with pytest.raises(DBAPIError):
                await db.execute(update(JobMatchRun).where(JobMatchRun.id == created["id"]).values(status="COMPLETED"))
            await db.rollback()
        provider.responses.append(artifact(created))
        completed = await core.generate(uid, created["id"])
        for query in (
            update(JobMatchRun).where(JobMatchRun.id == created["id"]).values(input_fingerprint="f" * 64),
            update(JobMatchCandidate).where(JobMatchCandidate.run_id == created["id"]).values(eligibility_status="SATISFIED"),
            update(JobMatchRecommendation).where(JobMatchRecommendation.candidate_id == completed["candidates"][0]["id"]).values(fit_band="UNCERTAIN"),
            delete(JobMatchRun).where(JobMatchRun.id == created["id"])):
            async with factory() as db:
                with pytest.raises(DBAPIError): await db.execute(query)
                await db.rollback()
        assert await core.detail(uid, created["id"]) == completed
    asyncio.run(run_with_database(database_url, run))


def test_coverage_insufficient_never_model_or_research(database_url):
    async def run(factory):
        core, provider, uid, _, _, request, _ = await seed_matching(factory)
        request = request.model_copy(update={"request_key": uuid4().hex, "filters": request.filters.model_copy(update={"exclude_company_ids": []})})
        async with factory() as db:
            request.filters.exclude_company_ids = list((await db.scalars(select(Company.id))).all())
        created = await core.create(uid, request)
        assert not created["candidates"] and created["coverage"]["status"] == "INSUFFICIENT"
        failed = await core.generate(uid, created["id"])
        assert failed["status"] == "FAILED" and failed["error"] == "MATCH_COVERAGE_INSUFFICIENT" and provider.calls == 0
        assert all(a["agent_run_id"] is None for a in failed["attempts"])
    asyncio.run(run_with_database(database_url, run))


def test_concurrent_generation_and_idempotency(database_url):
    async def run(factory):
        core, provider, uid, _, _, request, created = await seed_matching(factory)
        entered, release = asyncio.Event(), asyncio.Event()
        original = provider.generate
        async def paused(*args, **kwargs):
            entered.set(); await release.wait()
            return await original(*args, **kwargs)
        provider.generate = paused
        provider.responses.append(artifact(created))
        task = asyncio.create_task(core.generate(uid, created["id"]))
        await asyncio.wait_for(entered.wait(), 10)
        with pytest.raises(AppError) as error: await core.generate(uid, created["id"])
        assert error.value.code == "MATCH_RUNNING"
        # No long model transaction lock blocks creation/reading.
        assert (await core.detail(uid, created["id"]))["status"] == "RUNNING"
        release.set()
        assert (await task)["status"] == "COMPLETED" and provider.calls == 1
        with pytest.raises(AppError):
            await core.create(uid, request.model_copy(update={"filters": MatchFilters(locations=["上海"])}))
        async with factory() as db:
            assert await db.scalar(select(func.count(JobMatchRecommendation.id)).join(JobMatchCandidate, JobMatchCandidate.id == JobMatchRecommendation.candidate_id).where(JobMatchCandidate.run_id == created["id"])) == 2
    asyncio.run(run_with_database(database_url, run))


def test_memory_projection_no_skill_or_unconfirmed_inputs(database_url):
    async def run(factory):
        _, _, uid, _, pref, _, _ = await seed_matching(factory)
        async with factory() as db:
            for typ, actor, source in (("PREFERENCE", "AGENT", None), ("SKILL", "USER", None), ("PREFERENCE", "USER", "MEMORY_PROPOSAL")):
                item = UserMemoryItem(user_id=uid, memory_type=typ, scope="INTERVIEW", content="DEMO excluded preference/skill", source_type=source)
                db.add(item); await db.flush()
                db.add(UserMemoryEvent(user_id=uid, memory_item_id=item.id, event_type="CREATED", actor_type=actor, actor_id=uid))
            await db.commit()
            snapshot = await approved_preferences(db, uid)
            assert [m["id"] for m in snapshot["items"]] == [pref["id"]]
            assert all(m["user_confirmed"] for m in snapshot["items"])
    asyncio.run(run_with_database(database_url, run))


def test_matching_api_owner_scope_history(database_url, monkeypatch):
    async def run(factory):
        core, provider, uid, _, _, request, created = await seed_matching(factory)
        from interview_api.main import app
        from interview_api.api.deps import get_current_user
        from interview_api.core.rate_limit import interview_chat_limit
        import interview_api.modules.job_matching.router as routes
        monkeypatch.setattr(routes, "build_core", lambda: core)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
        app.dependency_overrides[interview_chat_limit] = lambda: None
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture.local") as client:
                assert (await client.get("/api/v1/job-matches" )).status_code == 200
                assert (await client.post("/api/v1/job-matches", json=request.model_dump())).json()["data"]["id"] == created["id"]
                provider.responses.append(artifact(created))
                response_ = await client.post(f"/api/v1/job-matches/{created['id']}/generate")
                assert response_.status_code == 200 and response_.json()["data"]["status"] == "COMPLETED"
                app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid + 100000)
                assert (await client.get(f"/api/v1/job-matches/{created['id']}")).status_code == 404
                assert (await client.post(f"/api/v1/job-matches/{created['id']}/generate")).status_code == 404
        finally: app.dependency_overrides.clear()
    asyncio.run(run_with_database(database_url, run))


def test_model_input_eligible_only_and_missing_key_replay(database_url, monkeypatch):
    async def run(factory):
        core, provider, uid, _, _, _, created = await seed_matching(factory)
        monkeypatch.setattr(settings, "llm_api_key", "")
        monkeypatch.setattr(settings, "job_matching_api_key", "")
        original = provider.generate
        async def capture(messages, **kwargs):
            payload = json.loads(messages[-1]["content"])["input"]
            assert "excluded_candidates" not in payload
            assert len(payload["candidates"]) == 2
            assert all(c["eligibility_status"] != "NOT_SATISFIED" for c in payload["candidates"])
            assert "recent_performance_observations" not in payload
            return await original(messages, **kwargs)
        provider.generate = capture
        provider.responses.append(artifact(created))
        assert (await core.generate(uid, created["id"]))["status"] == "COMPLETED"
    asyncio.run(run_with_database(database_url, run))


@pytest.mark.parametrize("case", ["budget", "timeout", "publication_rollback"])
def test_failed_attempt_keeps_inputs_and_no_partial_recommendations(database_url, monkeypatch, case):
    async def run(factory):
        core, provider, uid, _, _, _, created = await seed_matching(factory)
        provider.responses.append(artifact(created))
        if case == "budget": monkeypatch.setattr(settings, "job_matching_max_input_bytes", 1)
        if case == "timeout":
            from interview_api.infrastructure.models.provider import ModelProviderError
            async def fail(*args, **kwargs): raise ModelProviderError("model_timeout", "Fixture timeout")
            provider.generate = fail
        if case == "publication_rollback":
            from sqlalchemy.ext.asyncio import AsyncSession
            original = AsyncSession.flush
            async def fail_flush(self, *args, **kwargs):
                if any(isinstance(obj, JobMatchRecommendation) for obj in self.new): raise RuntimeError("injected publication failure")
                return await original(self, *args, **kwargs)
            monkeypatch.setattr(AsyncSession, "flush", fail_flush)
        failed = await core.generate(uid, created["id"])
        assert failed["status"] == "FAILED" and failed["snapshot"] == created["snapshot"]
        assert all(c["recommendation"] is None and c["agent_rank"] is None and c["final_rank"] is None for c in failed["candidates"])
        assert failed["attempts"][0]["status"] == "FAILED"
        if case == "budget": assert provider.calls == 0
    asyncio.run(run_with_database(database_url, run))


def test_bounded_top_n_and_stable_pre_rank(database_url, monkeypatch):
    async def run(factory):
        core, _, uid, jobs, _, request, created = await seed_matching(factory)
        repeated = await core.create(uid, request.model_copy(update={"request_key": uuid4().hex}))
        assert [(c["job_id"], c["pre_rank"]) for c in repeated["candidates"]] == [(c["job_id"], c["pre_rank"]) for c in created["candidates"]]
        monkeypatch.setattr(settings, "job_matching_top_n", 1)
        monkeypatch.setattr(settings, "job_matching_retrieval_limit", 1)
        limited = await core.create(uid, request.model_copy(update={"request_key": uuid4().hex}))
        assert len(limited["candidates"]) == 1 and limited["candidates"][0]["job_id"] == jobs[0]["job_id"]
        assert limited["coverage"]["window_may_be_truncated"]
        async with factory() as db:
            with pytest.raises(DBAPIError):
                await db.execute(update(JobMatchRun).where(JobMatchRun.id == limited["id"]).values(input_snapshot_json={"drift": True}))
            await db.rollback()
    asyncio.run(run_with_database(database_url, run))


def test_real_proposal_approval_projection(database_url):
    async def run(factory):
        from test_m5_report import completed_interview, coach_response
        from interview_api.infrastructure.models.router import INTERVIEW_COACH_ROUTE
        from interview_api.modules.interview_report.service import InterviewReportService
        from interview_api.modules.memory.proposal_models import MemoryProposal
        from interview_api.modules.memory.proposal_service import MemoryProposalService
        _, uid, sid, evaluations = await completed_interview(factory)
        body = coach_response([e["id"] for e in evaluations])
        refs = [f"evaluation:{evaluations[0]['id']}"]
        body["memory_proposals"] = [dict(proposal_type=kind, content=f"DEMO 用户希望 {index} 练习缓存边界", supporting_evaluation_ids=refs)
            for index, kind in enumerate(("PREFERENCE", "GOAL", "PREFERENCE", "PREFERENCE"))]
        reports = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: ReplayModelProvider([body])}))
        report = await reports.generate(sid, uid)
        assert report["generation_status"] == "COMPLETED"
        async with factory() as db:
            proposals = (await db.scalars(select(MemoryProposal).where(MemoryProposal.report_id == report["id"]).order_by(MemoryProposal.id))).all()
            service = MemoryProposalService(db)
            approved = await service.decide(uid, proposals[0].id, "APPROVED")
            corrected = await service.decide(uid, proposals[1].id, "CORRECTED", content="DEMO 用户确认：准备下一次后端面试", reason="更正目标")
            await service.decide(uid, proposals[2].id, "REJECTED")
            await db.commit()
            before = await db.scalar(select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == uid))
            preferences = await approved_preferences(db, uid)
            assert {m["id"] for m in preferences["items"]} == {approved["memory_item_id"], corrected["memory_item_id"]}
            assert "准备下一次后端面试" in json.dumps(preferences, ensure_ascii=False)
            assert "练习缓存边界" in json.dumps(preferences, ensure_ascii=False)
            assert await db.scalar(select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == uid)) == before
            assert proposals[3].status == "PENDING" and proposals[3].memory_item_id is None
    asyncio.run(run_with_database(database_url, run))


def test_concurrent_run_creation_reuses_business_key(database_url):
    async def run(factory):
        core, _, uid, _, _, request, _ = await seed_matching(factory)
        request = request.model_copy(update={"request_key": uuid4().hex})
        results = await asyncio.gather(core.create(uid, request), core.create(uid, request))
        assert results[0]["id"] == results[1]["id"] and results[0]["snapshot"] == results[1]["snapshot"]
        async with factory() as db:
            assert await db.scalar(select(func.count(JobMatchRun.id)).where(JobMatchRun.user_id == uid, JobMatchRun.request_key == request.request_key)) == 1
    asyncio.run(run_with_database(database_url, run))
