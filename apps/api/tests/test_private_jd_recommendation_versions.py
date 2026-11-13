"""Round 17: one private JD, two confirmed versions, and what must not move.

The rule engine already ranks deterministically; this file asks the product
questions instead: does a genuinely different JD produce a different (and
correctly evidenced) direction, does an unchanged JD stay unchanged, and does a
new version stay out of sessions that were frozen against the old one?

Honest scope note: the API exposes requirement editing but no raw-text edit, so
the title/responsibilities of v2 are written on the row directly. That stands in
for "the user pasted a different JD" and still goes through the real
draft → confirm → version-2 path the product uses.
"""
import asyncio
import hashlib
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.skill_registry import ROOT
from interview_api.modules.job_target.models import UserJobTarget
from interview_api.modules.job_target.schemas import CreateJobTargetRequest, RequirementInput
from interview_api.modules.job_target.service import JobTargetService
from test_private_recheck import core_for
from test_job_target import seed_user

V1_TEXT = """示例金融科技公司 · 支付清算研发团队（合成测试 JD）
岗位职责：
1. 负责 Kafka 消息队列与 Redis 缓存的稳定性优化。
2. 参与支付清算服务的接口设计与容量评估。
任职要求：
1. 熟悉 Kafka 与 Redis。
2. 具备 Java 服务端研发经验，了解事务与幂等设计。
"""
V1 = dict(company_name="示例金融科技公司", job_title="Java 后端工程师",
          responsibilities=["负责 Kafka 消息队列与 Redis 缓存的稳定性优化", "参与支付清算服务的接口设计与容量评估"],
          requirements=[dict(statement="熟悉 Kafka 与 Redis", importance="REQUIRED",
                             evidence="熟悉 Kafka 与 Redis", topic="Kafka"),
                        dict(statement="具备 Java 服务端研发经验", importance="REQUIRED",
                             evidence="具备 Java 服务端研发经验", topic="Java")])

V2_TEXT = """示例金融科技公司 · 智能应用研发团队（合成测试 JD）
岗位职责：
1. 负责 RAG 检索增强与 Agent 智能体链路开发。
2. 参与大模型应用的效果评估与线上推理优化。
任职要求：
1. 熟悉 LangGraph、RAG 与 Agent 开发。
2. 熟悉大模型应用与模型部署。
"""
V2_TEXT_ALT = "熟悉向量数据库者优先"
V2 = dict(company_name="示例金融科技公司", job_title="大模型应用工程师",
          responsibilities=["负责 RAG 检索增强与 Agent 智能体链路开发", "参与大模型应用的效果评估与线上推理优化"],
          requirements=[dict(statement="熟悉 LangGraph、RAG 与 Agent 开发", importance="REQUIRED",
                             evidence="熟悉 LangGraph、RAG 与 Agent 开发", topic="RAG")])


def body(database_url, scenario):
    async def main():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await scenario(factory)
        finally:
            await engine.dispose()
    asyncio.run(main())


async def target_with(factory, user_id, text, extraction):
    async with factory() as db, db.begin():
        created = await JobTargetService(db).create(user_id, CreateJobTargetRequest(
            raw_text=text, company_name=extraction["company_name"], job_title=extraction["job_title"]))
    async with factory() as db, db.begin():
        await JobTargetService(db).apply_parse(user_id, created["id"], extraction, "LLM", None)
    async with factory() as db, db.begin():
        await JobTargetService(db).confirm(user_id, created["id"])
    return created["id"]


async def confirm_version(factory, user_id, target_id, *, raw_text, title, responsibilities, requirements):
    """Write the new JD body, then edit requirements and confirm the next version.

    The API exposes requirement editing but no raw-text edit, so the body is
    written on the row directly; everything after it (draft → confirm → version
    bump) goes through the real service.
    """
    async with factory() as db, db.begin():
        row = await db.get(UserJobTarget, target_id)
        row.raw_text = raw_text
        row.job_title = title
        row.structured_json = {"responsibilities": responsibilities}
    async with factory() as db, db.begin():
        await JobTargetService(db).update_requirements(user_id, target_id, [
            RequirementInput(statement=item["statement"], importance=item["importance"],
                             evidence=item.get("evidence")) for item in requirements])
    async with factory() as db, db.begin():
        return await JobTargetService(db).confirm(user_id, target_id)


async def rec(factory, user_id, target_id, **kwargs):
    async with factory() as db:
        return await JobTargetService(db).skill_recommendations(user_id, target_id, **kwargs)


def registry_bytes():
    return hashlib.sha256(b"".join(sorted(
        path.read_bytes() for path in ROOT.glob("*/*/manifest.json")))).hexdigest()


def test_two_confirmed_versions_change_the_direction_and_stay_evidenced(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await target_with(factory, user_id, V1_TEXT, V1)
        before = registry_bytes()
        first = await rec(factory, user_id, target_id)
        assert first["target_version"] == 1
        assert first["recommended_skill_id"] == "java_backend"
        v1_quotes = [e["verbatim_jd_evidence"] for c in first["candidates"] if c["eligible"]
                     for e in c["evidence"] if e["evidence_status"] == "VERBATIM_FROM_JD"]
        assert v1_quotes and all(quote in V1_TEXT for quote in v1_quotes)

        # Reading twice is deterministic: no invented drift.
        again = await rec(factory, user_id, target_id)
        assert again["input_fingerprint"] == first["input_fingerprint"]
        assert again["recommended_skill_id"] == first["recommended_skill_id"]

        # A session frozen against v1 must keep v1's skill and JD version.
        frozen = await core_for(factory).create(user_id, CreateSessionRequest(
            job_target_id=target_id, interviewer_skill_id=first["recommended_skill_id"],
            adaptive_interview=True, question_count=1,
            interviewer_selection_source="AUTO_RECOMMENDED"))
        snapshot = frozen["context_snapshot"]
        assert snapshot["interviewer_config"]["skill"]["id"] == "java_backend"
        assert snapshot["interviewer_config"]["skill"]["version"] == "1.0.0"
        assert snapshot["job_target"]["version"] == 1

        reconfirmed = await confirm_version(factory, user_id, target_id,
            raw_text=V2_TEXT + V2_TEXT_ALT, title=V2["job_title"],
            responsibilities=V2["responsibilities"], requirements=V2["requirements"])
        assert reconfirmed["version"] == 2
        second = await rec(factory, user_id, target_id, expected_key=first["input_fingerprint"])
        assert second["target_version"] == 2
        assert second["input_fingerprint"] != first["input_fingerprint"]
        assert second["recommended_skill_id"] == "ai_agent_application"
        assert second["stale"] is True and second["stale_reason"]
        v2_quotes = [e["verbatim_jd_evidence"] for c in second["candidates"] if c["eligible"]
                     for e in c["evidence"] if e["evidence_status"] == "VERBATIM_FROM_JD"]
        assert v2_quotes and all(quote in V2_TEXT + V2_TEXT_ALT for quote in v2_quotes)
        assert not set(v2_quotes) & set(v1_quotes)

        # The frozen session is untouched by the new version.
        async with factory() as db:
            from interview_api.modules.interview.models import (
                InterviewContextSnapshot, InterviewSession)
            stored = await db.get(InterviewSession, frozen["id"])
            snapshot_row = await db.get(InterviewContextSnapshot, stored.context_snapshot_id)
        assert snapshot_row.job_target_id == target_id
        assert snapshot_row.context_json["interviewer_config"]["skill"]["id"] == "java_backend"
        assert snapshot_row.context_json["job_target"]["version"] == 1

        # A stale AUTO_RECOMMENDED hand-off for the old direction is refused.
        try:
            await core_for(factory).create(user_id, CreateSessionRequest(
                job_target_id=target_id, interviewer_skill_id="java_backend",
                adaptive_interview=True, question_count=1,
                interviewer_selection_source="AUTO_RECOMMENDED"))
            raise AssertionError("a stale auto-recommendation must be refused")
        except AppError as refused:
            assert refused.code == "SKILL_RECOMMENDATION_CHANGED"

        # A manual choice is still honoured after the version change.
        manual = await core_for(factory).create(user_id, CreateSessionRequest(
            job_target_id=target_id, interviewer_skill_id="bank_it",
            adaptive_interview=True, question_count=1,
            interviewer_selection_source="USER_SELECTED"))
        assert manual["context_snapshot"]["interviewer_config"]["skill"]["id"] == "bank_it"
        assert manual["context_snapshot"]["job_target"]["version"] == 2
        # Reading a recommendation never edits the registry.
        assert registry_bytes() == before
    body(database_url, scenario)


def test_requirement_only_edits_change_the_key_and_are_labelled_stale(database_url):
    """Documented limitation: the rule also reads title/duties.

    Changing only requirement text can leave the winner unchanged. The product
    must then say "this hand-off key is stale" rather than pretend the result
    changed — and it must not invent a new direction from nothing.
    """
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await target_with(factory, user_id, V1_TEXT, V1)
        first = await rec(factory, user_id, target_id)
        await confirm_version(factory, user_id, target_id,
            raw_text=V1_TEXT + "3. 熟悉 Kafka、Redis 与 MySQL。\n", title=V1["job_title"],
            responsibilities=V1["responsibilities"],
            requirements=[dict(statement="熟悉 Kafka、Redis 与 MySQL", importance="REQUIRED",
                               evidence="熟悉 Kafka、Redis 与 MySQL")])
        second = await rec(factory, user_id, target_id, expected_key=first["input_fingerprint"])
        assert second["target_version"] == 2
        assert second["input_fingerprint"] != first["input_fingerprint"]
        assert second["stale"] is True
        if second["recommended_skill_id"] == first["recommended_skill_id"]:
            # The honest outcome: same direction, but a different key.
            assert second["status"] == "RECOMMENDED"
        assert second["source"] == "DETERMINISTIC_RULE" and second["model_calls"] == 0
    body(database_url, scenario)
