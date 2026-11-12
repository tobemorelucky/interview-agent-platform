"""Private JD targets: honest extraction, owner scoping, frozen snapshots.

No model HTTP: extraction and every interview agent run on replay providers.
"""
import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.provider import ModelProvider, ModelProviderError, ModelResponse
from interview_api.infrastructure.models.router import EXTRACT_ROUTE, ModelRouter
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.models import InterviewContextSnapshot, InterviewSessionQuestion
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.job_target.models import CONFIRMED, PARSED, UserJobTarget
from interview_api.modules.job_target.schemas import CreateJobTargetRequest, RequirementInput
from interview_api.modules.job_target.service import JobTargetService
from interview_api.modules.public_career.library import published_jobs
from test_m3_candidate_profile import response as profile_response, model_router, seed as seed_resume

JD = """岗位职责：
1. 负责推荐系统在线服务的架构设计与开发，保障高并发场景下的稳定性。
2. 参与特征平台与模型服务化建设，持续优化线上推理延迟。
任职要求：
1. 熟练掌握 Python，熟悉 Kafka 与 Redis 的使用与调优；
2. 必须熟悉分布式系统，具备三年以上后端开发经验；
3. 有推荐或搜索业务经验者优先。
"""

EXTRACTION = {
    "company_name": None,
    "job_title": "推荐系统后端工程师",
    "responsibilities": ["推荐系统在线服务架构设计与开发"],
    "requirements": [
        {"statement": "熟练掌握 Python", "importance": "REQUIRED", "evidence": "熟练掌握 Python", "topic": "Python"},
        {"statement": "熟悉 Kafka 与 Redis", "importance": "UNKNOWN", "evidence": "熟悉 Kafka 与 Redis", "topic": "Kafka"},
        # Not present in the pasted text: must be dropped, never stored.
        {"statement": "五年以上 Go 语言经验", "importance": "REQUIRED", "evidence": "五年以上 Go 语言经验", "topic": "Go"},
    ],
}

COACH = {
    "overall_summary": "离线回放：围绕用户提供的 JD 做了三轮技术与项目追问。",
    "strengths": [],
    "weaknesses": [{"statement": "缓存一致性的取舍只在第一轮出现。",
                    "supporting_evaluation_ids": ["evaluation:1"]}],
    "recurring_issues": [], "job_alignment": [], "recommended_actions": ["复盘缓存一致性"],
    "practice_plan": ["演练并发更新"], "memory_proposals": [],
}


async def seed_user(factory):
    user_id, resume_id = await seed_resume(factory)
    router, _ = model_router([profile_response(resume_id)])
    profile = await ResumeProfileAgent(factory, router).run(resume_id)
    assert profile["status"] == "COMPLETED", profile
    return user_id


async def create_target(factory, user_id, *, parse=True, confirm=True):
    async with factory() as db, db.begin():
        service = JobTargetService(db)
        created = await service.create(user_id, CreateJobTargetRequest(
            raw_text=JD, company_name="某互联网公司", job_title="推荐系统后端工程师"))
    if parse:
        extraction, mode, error = await JobTargetService(None, ModelRouter(
            {EXTRACT_ROUTE: ExtractProvider()})).extract(JD, "某互联网公司", "推荐系统后端工程师")
        async with factory() as db, db.begin():
            await JobTargetService(db).apply_parse(user_id, created["id"], extraction, mode, error)
    if confirm:
        async with factory() as db, db.begin():
            await JobTargetService(db).confirm(user_id, created["id"])
    return created["id"]


class ExtractProvider(ModelProvider):
    provider_name, model_name = "replay", "PRIVATE_JD_EXTRACT"
    async def generate(self, messages, *, schema):
        assert schema["title"] == "ExtractedJobDescription"
        return ModelResponse(json.dumps(EXTRACTION, ensure_ascii=False), {"total_tokens": 42})


class UnavailableProvider(ModelProvider):
    provider_name, model_name = "replay", "PRIVATE_JD_UNAVAILABLE"
    async def generate(self, messages, *, schema):
        raise ModelProviderError("model_request_not_sent", "offline")


class InterviewProvider(ModelProvider):
    provider_name, model_name = "replay", "PRIVATE_JD_INTERVIEW"
    def __init__(self):
        self.evaluations = 0

    async def generate(self, messages, *, schema):
        packet = json.loads(messages[-1]["content"])["input"]
        title = schema["title"]
        if title in ("AdaptiveSelection", "MainHarnessAction"):
            adaptive = packet["adaptive"]
            candidates = [c for c in adaptive["candidates"] if c["source"]["ref"].startswith("requirement:")]
            candidate = (candidates or adaptive["candidates"])[0]
            kid = candidate["target_knowledge_id"]
            target = (adaptive.get("targets") or {}).get(kid, {})
            topic = target.get("topic") or "技术"
            label = target.get("label") or topic
            value = dict(candidate_id=candidate["candidate_id"], strategy="NEXT", focus_gap_ids=[],
                supporting_refs=[candidate["source"]["ref"]] + (
                    [adaptive["current_answer_ref"]] if packet.get("history") and adaptive.get("current_answer_ref") else []),
                reason_tags=["COVERAGE_SWITCH"],
                proposed_question=dict(question=f"{label}：请说明具体实现与取舍",
                    expected_points=["原理", "实践"], dimension=topic,
                    difficulty=packet["desired_difficulty"]))
            if title == "MainHarnessAction":
                value = dict(kind="final", selection=value)
            return ModelResponse(json.dumps(value, ensure_ascii=False), {"total_tokens": 100})
        if title == "AdaptiveEvaluation":
            self.evaluations += 1
            knowledge_id = packet["adaptive"]["active_knowledge_id"]
            value = dict(zip(("technical_correctness", "coverage", "depth", "relevance", "communication"),
                             (4, 4, 4, 4, 4)), evaluation="离线回放：私有 JD 场景评价。",
                covered_points=["原理"], missing_points=[], factual_or_reasoning_issues=[],
                follow_up_focus=[], next_action_recommendation="NEXT",
                knowledge_observations=[dict(knowledge_id=knowledge_id, state="DEMONSTRATED_IN_SESSION",
                    answer_quotes=[packet["answer"]], missing_mechanisms=[], resolved_gap_ids=[])])
            return ModelResponse(json.dumps(value, ensure_ascii=False), {"total_tokens": 100})
        assert title == "CoachArtifact"
        # Cite the session's real evaluation ids; a fabricated ref is rejected.
        refs = [f"evaluation:{row['id']}" for row in (packet.get("evaluations") or [])[:1]]
        coach = {**COACH, "weaknesses": [{"statement": "缓存一致性的取舍只在第一轮出现。",
                                          "supporting_evaluation_ids": refs}] if refs else []}
        return ModelResponse(json.dumps(coach, ensure_ascii=False), {"total_tokens": 100})


def run(database_url, fn):
    async def main():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await fn(factory)
        finally:
            await engine.dispose()
    asyncio.run(main())


def test_extraction_drops_anything_the_pasted_text_does_not_support(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id, confirm=False)
        async with factory() as db:
            target = await db.get(UserJobTarget, target_id)
            rows = target.requirements_json
        statements = [row["statement"] for row in rows]
        assert statements == ["熟练掌握 Python", "熟悉 Kafka 与 Redis"]
        assert all(row["evidence"] for row in rows)
        assert rows[0]["importance"] == "REQUIRED" and rows[1]["importance"] == "UNKNOWN"
        assert target.parse_mode == "LLM" and target.parse_status == PARSED
        assert "Go" not in json.dumps(rows)
    run(database_url, scenario)


def test_model_unavailable_falls_back_to_rules_and_says_so(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        async with factory() as db, db.begin():
            created = await JobTargetService(db).create(user_id, CreateJobTargetRequest(raw_text=JD))
        extraction, mode, error = await JobTargetService(None, ModelRouter(
            {EXTRACT_ROUTE: UnavailableProvider()})).extract(JD, None, None)
        assert mode == "RULE" and error.startswith("model_unavailable")
        async with factory() as db, db.begin():
            data = await JobTargetService(db).apply_parse(user_id, created["id"], extraction, mode, error)
        assert data["parse_mode"] == "RULE" and data["parse_status"] == PARSED
        assert all(row["source"] == "RULE" for row in data["requirements"])
        # A rule result never claims more than the text says.
        assert {row["importance"] for row in data["requirements"]} <= {"REQUIRED", "PREFERRED", "UNKNOWN"}
    run(database_url, scenario)


def test_a_target_is_owner_scoped_and_never_enters_the_public_library(database_url):
    async def scenario(factory):
        owner = await seed_user(factory)
        other = await seed_user(factory)
        from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion
        async with factory() as db:
            before = [await db.scalar(select(func.count(c.id))) for c in (Company, Job, JobDescriptionVersion)]
        target_id = await create_target(factory, owner)
        async with factory() as db:
            after = [await db.scalar(select(func.count(c.id))) for c in (Company, Job, JobDescriptionVersion)]
        assert before == after  # the private path never writes a public row
        async with factory() as db:
            with pytest.raises(AppError) as failure:
                await JobTargetService(db).read(other, target_id)
            assert failure.value.code == "JOB_TARGET_NOT_FOUND" and failure.value.status_code == 404
            assert await JobTargetService(db).history(other, 20, 0) == []
            assert len(await JobTargetService(db).history(owner, 20, 0)) == 1
            titles = [row.Job.canonical_title for row in (await db.execute(published_jobs())).all()]
            assert "推荐系统后端工程师" not in titles
            with pytest.raises(AppError):
                await JobTargetService(db).delete(other, target_id)
    run(database_url, scenario)


def test_unconfirmed_target_cannot_become_interview_context(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id, confirm=False)
        async with factory() as db:
            with pytest.raises(AppError) as failure:
                await build_context_(db, user_id, target_id)
        assert failure.value.code == "JOB_TARGET_NOT_CONFIRMED"
    run(database_url, scenario)


async def build_context_(db, user_id, target_id):
    from interview_api.modules.interview.context import build_context
    return await build_context(db, user_id, CreateSessionRequest(
        job_target_id=target_id, question_count=3, interviewer_skill_id="ai_agent_application",
        adaptive_interview=True))


def test_session_snapshot_freezes_the_target_and_outlives_edits(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        async with factory() as db:
            context, _, fingerprint = await build_context_(db, user_id, target_id)
        assert context["company_id"] is None and context["job_id"] is None
        assert context["job_description_version_id"] is None
        assert context["job_target"]["id"] == target_id and context["job_target"]["version"] == 1
        assert context["company_name"] == "某互联网公司"
        assert [row["statement"] for row in context["requirements"]] == ["熟练掌握 Python", "熟悉 Kafka 与 Redis"]
        background = context["interviewer_config"]["company_background"]
        assert background["mode"] == "USER_PROVIDED_JD_CONTEXT"
        assert background["source"]["source_type"] == "USER_SUPPLIED"
        assert "未经企业核实" in background["instruction"]

        core = MockInterviewService(factory, ModelRouter({}))
        created = await core.create(user_id, CreateSessionRequest(job_target_id=target_id, question_count=3,
            interviewer_skill_id="ai_agent_application", adaptive_interview=True))
        frozen = created["context_snapshot"]
        assert frozen["job_target"]["version"] == 1
        # Editing the target afterwards must not rewrite the frozen snapshot.
        async with factory() as db, db.begin():
            await JobTargetService(db).update_requirements(user_id, target_id, [
                RequirementInput(statement="只保留一条修改后的要求", importance="REQUIRED")])
        async with factory() as db:
            target = await db.get(UserJobTarget, target_id)
            assert target.parse_status == PARSED and target.confirmed_at is None
            assert target.version == 1
        assert [row["statement"] for row in context["requirements"]] == ["熟练掌握 Python", "熟悉 Kafka 与 Redis"]
        assert context["job_target"]["version"] == 1
        reread = (await core.detail(created["id"], user_id))["context_snapshot"]
        assert reread == frozen
        assert [row["statement"] for row in reread["requirements"]] == ["熟练掌握 Python", "熟悉 Kafka 与 Redis"]
    run(database_url, scenario)


def test_private_target_runs_main_evaluation_coach_and_memory(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        provider = InterviewProvider()
        router = ModelRouter({route: provider for route in ("interview_main", "answer_evaluation", "interview_coach")})
        core = MockInterviewService(factory, router)
        created = await core.create(user_id, CreateSessionRequest(job_target_id=target_id, question_count=3,
            interviewer_skill_id="ai_agent_application", adaptive_interview=True))
        session_id = created["id"]
        assert created["context_snapshot"]["job_target"]["id"] == target_id
        await core.start(session_id, user_id)
        for index in (1, 2, 3):
            question = (await core.detail(session_id, user_id))["current_question"]
            events = await core.answer(session_id, user_id, question["question_id"], f"private-jd-{index}",
                                       f"离线回答 {index}：先讲实现，再讲取舍与并发边界。")
            assert [event[0] for event in events][0] == "evaluation"
        final = await core.detail(session_id, user_id)
        assert final["status"] == "COMPLETED"
        from interview_api.modules.interview.models import InterviewEvaluation
        async with factory() as db:
            assert await db.scalar(select(func.count(InterviewEvaluation.id)).where(
                InterviewEvaluation.session_id == session_id)) == 3
        async with factory() as db:
            # Private requirement ids are local indices, never public FKs.
            rows = (await db.scalars(select(InterviewSessionQuestion).where(
                InterviewSessionQuestion.session_id == session_id))).all()
            assert len(rows) == 3 and all(row.job_requirement_id is None for row in rows)
            # Requirement-sourced questions keep a local ref, never a public FK.
            assert any((row.candidate_refs_json or {}).get("job_target_requirement") for row in rows)

        from interview_api.modules.interview_report.service import InterviewReportService
        report = await InterviewReportService(factory, core.router).generate(session_id, user_id)
        assert report["generation_status"] == "COMPLETED", report
        assert report["statistics"]["evaluated_count"] == 3

        from interview_api.modules.memory.capability_service import CapabilityMemoryService
        async with factory() as db:
            states = await CapabilityMemoryService(db).list(user_id)
        assert states  # observations came from the private-JD session, not fixtures
        planned = await core.create(user_id, CreateSessionRequest(job_target_id=target_id, question_count=3,
            interviewer_skill_id="ai_agent_application", adaptive_interview=True))
        assert planned["context_snapshot"]["memory_guided_plan"]["items"] is not None
    run(database_url, scenario)


def test_confirmed_target_cannot_be_deleted_while_a_session_uses_it(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        core = MockInterviewService(factory, ModelRouter({}))
        created = await core.create(user_id, CreateSessionRequest(job_target_id=target_id, question_count=3,
            interviewer_skill_id="ai_agent_application", adaptive_interview=True))
        assert created["context_snapshot"]["job_target"]["id"] == target_id
        async with factory() as db:
            with pytest.raises(AppError) as failure:
                await JobTargetService(db).delete(user_id, target_id)
                await db.commit()
            assert failure.value.code == "JOB_TARGET_IN_USE"
    run(database_url, scenario)


def test_match_preview_cites_evidence_and_never_scores(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        async with factory() as db:
            view = await JobTargetService(db).match_preview(user_id, target_id)
        assert view["target_id"] == target_id
        assert "score" not in json.dumps(view).lower()
        assert view["counts"]["NOT_APPLICABLE"] == 0
        assert len(view["rows"]) == 2
        for row in view["rows"]:
            assert row["status"] in ("SUPPORTED", "PARTIAL", "NO_EVIDENCE")
            if row["status"] == "NO_EVIDENCE":
                assert row["supports"] == []
    run(database_url, scenario)
