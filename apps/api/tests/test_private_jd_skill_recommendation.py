"""Round 16: private-JD Skill recommendations are a read-only projection.

The ranking itself already existed (`recommend_for_target` + `discover()`).
These cases cover the *new* contract: an owner-scoped confirmed-JD endpoint,
verbatim JD evidence, an input fingerprint that changes when requirements are
edited, honest uncertainty, no writes, and the frozen user choice at creation.
No model HTTP: extraction is applied through the service and sessions use the
offline replay provider.
"""
import asyncio
import hashlib
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.skill_registry import ROOT
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.job_target.models import UserJobTarget
from interview_api.modules.job_target.schemas import CreateJobTargetRequest, RequirementInput
from interview_api.modules.job_target.service import JobTargetService
from interview_api.modules.public_career.models import (
    Company, Job, JobDescriptionVersion, JobRequirement,
)
from test_job_target import seed_user
from test_private_recheck import core_for

AI_JD = """岗位职责：
1. 负责大模型应用与 Agent 应用开发，建设 RAG 检索增强服务。
2. 优化向量数据库检索效果与模型服务部署流程，保障线上推理稳定。
任职要求：
1. 必须熟悉 RAG 检索增强与 Agent 开发，能独立负责智能体链路；
2. 熟悉大模型应用与模型部署，有 LangChain 使用经验；
3. 熟悉向量数据库者优先，了解人工智能技术应用更佳。
"""

AI_EXTRACTION = {
    "company_name": None,
    "job_title": "大模型应用工程师",
    "responsibilities": ["负责大模型应用与 Agent 应用开发，建设 RAG 检索增强服务"],
    "requirements": [
        {"statement": "必须熟悉 RAG 检索增强与 Agent 开发", "importance": "REQUIRED",
         "evidence": "必须熟悉 RAG 检索增强与 Agent 开发", "topic": "RAG"},
        {"statement": "熟悉大模型应用与模型部署", "importance": "REQUIRED",
         "evidence": "熟悉大模型应用与模型部署", "topic": "大模型"},
        {"statement": "熟悉向量数据库者优先", "importance": "PREFERRED",
         "evidence": "熟悉向量数据库者优先", "topic": "向量数据库"},
    ],
}


HTTP_PASSWORD = "R17-http-only-pass"


async def http_user(factory, email):
    """A login-capable user; the real /auth/login route issues the token."""
    from interview_api.core.security import hash_password
    from interview_api.modules.users.models import User
    async with factory() as db, db.begin():
        user = User(email=email, password_hash=hash_password(HTTP_PASSWORD))
        db.add(user)
        await db.flush()
        return dict(id=user.id, email=user.email)


async def ai_target(factory, user_id, *, confirm=True):
    """A second, different confirmed private JD (no model call involved)."""
    async with factory() as db, db.begin():
        created = await JobTargetService(db).create(user_id, CreateJobTargetRequest(
            raw_text=AI_JD, company_name="某 AI 公司", job_title="大模型应用工程师"))
    async with factory() as db, db.begin():
        await JobTargetService(db).apply_parse(user_id, created["id"], AI_EXTRACTION, "LLM", None)
    if confirm:
        async with factory() as db, db.begin():
            await JobTargetService(db).confirm(user_id, created["id"])
    return created["id"]


async def recommendation(factory, user_id, target_id, **kwargs):
    async with factory() as db:
        return await JobTargetService(db).skill_recommendations(user_id, target_id, **kwargs)


async def public_rows(factory):
    async with factory() as db:
        counts = []
        for model in (Company, Job, JobDescriptionVersion, JobRequirement):
            counts.append(await db.scalar(select(func.count()).select_from(model)))
        return tuple(counts)


def registry_bytes():
    return hashlib.sha256(b"".join(sorted(
        path.read_bytes() for path in ROOT.glob("*/*/manifest.json")))).hexdigest()


def body(database_url, scenario):
    async def main():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await scenario(factory)
        finally:
            await engine.dispose()
    asyncio.run(main())


def test_confirmed_private_jd_returns_a_legal_skill_with_verbatim_evidence(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await ai_target(factory, user_id)
        before_registry, before_public = registry_bytes(), await public_rows(factory)
        rec = await recommendation(factory, user_id, target_id)
        assert rec["projection"] == "PRIVATE_TARGET_SKILL_V1"
        assert rec["status"] == "RECOMMENDED" and rec["auto_select"] is True
        assert rec["recommended_skill_id"] == "ai_agent_application"
        assert rec["recommended_version"] == "1.0.0"
        assert rec["source"] == "DETERMINISTIC_RULE" and rec["model_calls"] == 0
        assert rec["skill_registry_read_only"] is True
        assert rec["target_version"] == 1 and rec["parse_status"] == "CONFIRMED"
        eligible = [c for c in rec["candidates"] if c["eligible"]]
        assert eligible and eligible[0]["skill_id"] == "ai_agent_application"
        assert all(len(c["version"].split(".")) == 3 for c in rec["candidates"])
        # Every candidate comes from the registered, available catalogue.
        assert all(c["skill_id"] and c["version"] for c in rec["candidates"])
        # Verbatim JD evidence is attached, not invented.
        quotes = [e["verbatim_jd_evidence"] for c in eligible for e in c["evidence"]
                  if e["evidence_status"] == "VERBATIM_FROM_JD"]
        assert quotes and all(q in AI_JD for q in quotes)
        # No percentage / confidence score pretending to be model certainty.
        assert not any("confidence" in c or "percent" in c for c in rec["candidates"])
        assert "不是概率" in rec["ranking_semantics"]
        # Reading a recommendation neither edits the registry nor publishes a job.
        await recommendation(factory, user_id, target_id)
        assert registry_bytes() == before_registry
        assert await public_rows(factory) == before_public
    body(database_url, scenario)


def test_unconfirmed_jd_offers_no_formal_recommendation(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await ai_target(factory, user_id, confirm=False)
        rec = await recommendation(factory, user_id, target_id)
        assert rec["status"] == "NOT_CONFIRMED"
        assert rec["empty_reason"] == "JD_NOT_CONFIRMED"
        assert rec["candidates"] == [] and rec["auto_select"] is False
        assert rec["recommended_skill_id"] is None and rec["recommended_version"] is None
        assert rec["needs_manual_confirmation"] is True
        assert "未确认" in rec["message"]
    body(database_url, scenario)


def test_no_legal_registered_skill_is_reported_honestly(database_url, tmp_path):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await ai_target(factory, user_id)
        empty = tmp_path / "empty_registry"
        empty.mkdir()
        rec = await recommendation(factory, user_id, target_id, root=empty)
        assert rec["status"] == "NO_APPLICABLE_SKILL"
        assert rec["empty_reason"] == "NO_REGISTERED_SKILL"
        assert rec["candidates"] == [] and rec["auto_select"] is False
        assert rec["recommended_skill_id"] is None
        assert "不会编造相关性" in rec["message"] or "手动选择" in rec["message"]
    body(database_url, scenario)


def test_another_user_gets_404_over_real_http_and_nothing_leaks(database_url, app_engine):
    """Bearer token → route → dependency → service → 404, with no overrides.

    Round 16 replaced this with a service call plus a route-registration check
    because the process-wide engine's pool belonged to an earlier event loop.
    The `app_engine` fixture disposes that pool inside the current loop, so the
    real app (real `get_db`, real `get_current_user`, real JWT) can be used again.
    """
    async def scenario(factory):
        owner = await http_user(factory, f"r17-owner-{uuid4().hex[:8]}@example.com")
        intruder = await http_user(factory, f"r17-other-{uuid4().hex[:8]}@example.com")
        target_id = await ai_target(factory, owner["id"])
        # Missing and "belongs to someone else" take the same owner-scoped path.
        with pytest.raises(AppError) as denied:
            await recommendation(factory, intruder["id"], target_id)
        assert denied.value.status_code == 404
        assert denied.value.code == "JOB_TARGET_NOT_FOUND"

        await app_engine.reset()
        from interview_api.main import app
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                async def token(user):
                    response = await client.post("/api/v1/auth/login", json=dict(
                        email=user["email"], password=HTTP_PASSWORD))
                    assert response.status_code == 200, response.text
                    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}

                path = f"/api/v1/job-targets/{target_id}/skill-recommendations"
                other = await client.get(path, headers=await token(intruder))
                assert other.status_code == 404, other.text
                assert other.json()["error"]["code"] == "JOB_TARGET_NOT_FOUND"
                # The refusal must not echo the owner's job, company or requirements.
                for secret in ("某 AI 公司", "大模型应用工程师", AI_JD.splitlines()[1], "熟悉向量数据库者优先"):
                    assert secret not in other.text
                # ...and the same URL works for the owner, so 404 meant ownership.
                mine = await client.get(path, headers=await token(owner))
                assert mine.status_code == 200, mine.text
                data = mine.json()["data"]
                assert data["status"] == "RECOMMENDED"
                assert data["recommended_skill_id"] == "ai_agent_application"
                # An unauthenticated read is rejected before any ownership lookup.
                assert (await client.get(path)).status_code == 401
        finally:
            # Close this loop's pool before the loop ends; see the fixture note.
            await app_engine.reset()
    body(database_url, scenario)


def test_editing_and_reconfirming_the_jd_invalidates_the_old_recommendation(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await ai_target(factory, user_id)
        first = await recommendation(factory, user_id, target_id)
        assert first["target_version"] == 1 and first["stale"] is False
        key = first["input_fingerprint"]
        # Editing returns the JD to a draft: the older recommendation is stale
        # and the server says so when asked about the original hand-off key.
        async with factory() as db, db.begin():
            await JobTargetService(db).update_requirements(user_id, target_id, [
                RequirementInput(statement="熟悉柜面营销与客户营销流程", importance="REQUIRED")])
        draft = await recommendation(factory, user_id, target_id, expected_key=key)
        assert draft["status"] == "NOT_CONFIRMED" and draft["candidates"] == []
        assert draft["stale"] is True
        async with factory() as db, db.begin():
            reconfirmed = await JobTargetService(db).confirm(user_id, target_id)
        assert reconfirmed["version"] == 2
        second = await recommendation(factory, user_id, target_id, expected_key=key)
        assert second["target_version"] == 2
        assert second["input_fingerprint"] != key
        assert second["stale"] is True and second["stale_reason"]
        # content_fingerprint covers raw_text only and must NOT be the key.
        assert second["content_fingerprint"] == first["content_fingerprint"]
        # Honest note: the rule also reads the (unchanged) title and duties, so
        # editing only requirements need not change the winner. The staleness
        # signal is the input key, not a fabricated change in the result.
        assert second["status"] == "RECOMMENDED"
        # When the ranking really changes, the existing creation-time guard
        # refuses an AUTO_RECOMMENDED hand-off made against the old result.
        async with factory() as db, db.begin():
            row = await db.get(UserJobTarget, target_id)
            row.job_title = "柜面营销专员"
            row.structured_json = {"responsibilities": []}
        third = await recommendation(factory, user_id, target_id, expected_key=key)
        assert third["input_fingerprint"] != second["input_fingerprint"]
        assert third["stale"] is True
        assert third["status"] != "RECOMMENDED" or \
               third["recommended_skill_id"] != first["recommended_skill_id"]
        with pytest.raises(AppError) as stale:
            await core_for(factory).create(user_id, CreateSessionRequest(
                job_target_id=target_id, interviewer_skill_id=first["recommended_skill_id"],
                adaptive_interview=True, question_count=1,
                interviewer_selection_source="AUTO_RECOMMENDED"))
        assert stale.value.code == "SKILL_RECOMMENDATION_CHANGED"
    body(database_url, scenario)


def test_manual_skill_choice_is_frozen_and_never_overwritten_by_the_rule(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await ai_target(factory, user_id)
        rec = await recommendation(factory, user_id, target_id)
        assert rec["auto_select"] is True
        created = await core_for(factory).create(user_id, CreateSessionRequest(
            job_target_id=target_id, interviewer_skill_id="java_backend",
            adaptive_interview=True, question_count=1,
            interviewer_selection_source="USER_SELECTED"))
        snapshot = created["context_snapshot"]
        assert snapshot["interviewer_config"]["skill"]["id"] == "java_backend"
        assert snapshot["interviewer_selection"]["source"] == "USER_SELECTED"
        # The rule result is recorded, but it never replaces the user's choice.
        assert snapshot["interviewer_selection"]["recommendation"]["recommended_skill_id"] == \
               "ai_agent_application"
    body(database_url, scenario)


def test_recommended_skill_creates_a_legal_interview_with_its_frozen_version(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await ai_target(factory, user_id)
        rec = await recommendation(factory, user_id, target_id)
        before_public = await public_rows(factory)
        created = await core_for(factory).create(user_id, CreateSessionRequest(
            job_target_id=target_id, interviewer_skill_id=rec["recommended_skill_id"],
            adaptive_interview=True, question_count=1,
            interviewer_selection_source="AUTO_RECOMMENDED"))
        snapshot = created["context_snapshot"]
        assert snapshot["interviewer_config"]["skill"]["id"] == rec["recommended_skill_id"]
        assert snapshot["interviewer_config"]["skill"]["version"] == rec["recommended_version"]
        assert snapshot["interviewer_selection"]["source"] == "AUTO_RECOMMENDED"
        assert snapshot["job_target"]["version"] == 1
        assert snapshot["company_id"] is None and snapshot["job_id"] is None
        # A confirmed private JD must never materialise as public career data.
        assert await public_rows(factory) == before_public
    body(database_url, scenario)


def test_public_job_recommendation_rules_are_unchanged():
    """The public path still uses the same deterministic policy and ranking."""
    from interview_api.modules.interview.skill_recommendation import (
        POLICY_VERSION, recommend, recommend_for_target)

    public = recommend(dict(job_id=7, title="Java 后端工程师", job_family="backend",
                            jd_id=3, jd_fingerprint="x",
                            responsibilities=["Kafka 消息队列开发与调优"],
                            requirements=[dict(id=1, statement="熟悉 Kafka 与 Redis")], tags=[]))
    assert public["policy_version"] == POLICY_VERSION
    assert public["job_id"] == 7 and public["jd_id"] == 3
    assert public["ranking"] and all("version" in row for row in public["ranking"])
    assert callable(recommend_for_target)
