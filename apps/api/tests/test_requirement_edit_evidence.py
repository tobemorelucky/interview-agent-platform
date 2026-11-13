"""Round 18 regression: editing one requirement must not erase the others' quotes.

The editor posts `statement` + `importance` only. Rebuilding the row list from
that payload used to clear `evidence` on *every* requirement, so one edit made the
whole confirmed version look user-authored and the Skill recommendation lost the
JD quotes it cites. The fix carries the previous evidence over for rows the user
did not change; a genuinely new row stays quote-less and is reported as such.
"""
import asyncio
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.modules.job_target.schemas import CreateJobTargetRequest, RequirementInput
from interview_api.modules.job_target.service import JobTargetService
from interview_api.modules.users.models import User

JD = """AI 应用工程师（合成测试用文本，非真实招聘要求）
岗位职责：
1、负责大模型应用与检索增强系统的服务端开发。
任职要求：
1、熟悉 Kafka 与 Redis 的使用与问题排查。
2、熟悉 Python 开发与 RAG 检索增强，能独立完成接口设计与实现。
3、有向量数据库使用经验者优先。
"""


def body(database_url, scenario):
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await scenario(factory)
        finally:
            await engine.dispose()

    asyncio.run(run())


async def seed_user(factory) -> int:
    async with factory() as db:
        user = User(email=f"r18.{uuid4().hex[:10]}@example.com",
                    username=f"r18{uuid4().hex[:8]}", password_hash="x", role="USER")
        db.add(user)
        await db.commit()
        return user.id


async def parsed_target(factory, user_id: int) -> int:
    """A target whose rows carry verbatim quotes, as a real parse would leave them."""
    async with factory() as db, db.begin():
        created = await JobTargetService(db).create(
            user_id, CreateJobTargetRequest(raw_text=JD, job_title="AI 应用工程师"))
    async with factory() as db, db.begin():
        await JobTargetService(db).update_requirements(user_id, created["id"], [
            RequirementInput(statement="熟悉 Kafka 与 Redis 的使用与问题排查",
                             importance="REQUIRED",
                             evidence="熟悉 Kafka 与 Redis 的使用与问题排查"),
            RequirementInput(statement="熟悉 Python 开发与 RAG 检索增强，能独立完成接口设计与实现",
                             importance="REQUIRED",
                             evidence="熟悉 Python 开发与 RAG 检索增强，能独立完成接口设计与实现"),
        ])
    async with factory() as db, db.begin():
        await JobTargetService(db).confirm(user_id, created["id"])
    return created["id"]


def test_editing_one_requirement_keeps_the_other_verbatim_quotes(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await parsed_target(factory, user_id)

        # Exactly what the page posts: every row as statement+importance, plus one new row.
        async with factory() as db, db.begin():
            saved = await JobTargetService(db).update_requirements(user_id, target_id, [
                RequirementInput(statement="熟悉 Kafka 与 Redis 的使用与问题排查"),
                RequirementInput(statement="熟悉 Python 开发与 RAG 检索增强，能独立完成接口设计与实现"),
                RequirementInput(statement="有向量数据库使用经验者优先"),
            ])
        rows = {row["statement"]: row for row in saved["requirements"]}
        kafka = next(row for key, row in rows.items() if key.startswith("熟悉 Kafka"))
        python = next(row for key, row in rows.items() if key.startswith("熟悉 Python"))
        vector = next(row for key, row in rows.items() if key.startswith("有向量数据库"))
        # Untouched rows keep their JD quotes (the round-18 fix).
        assert kafka["evidence"].startswith("熟悉 Kafka")
        assert python["evidence"].startswith("熟悉 Python")
        # A genuinely new row has no JD quote and must not pretend to have one.
        assert vector["evidence"] is None
        assert saved["parse_status"] == "PARSED"

    body(database_url, scenario)


def test_a_new_requirement_is_projected_as_user_added_and_changes_the_input_key(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await parsed_target(factory, user_id)
        async with factory() as db:
            before = await JobTargetService(db).skill_recommendations(user_id, target_id)
        assert before["status"] == "RECOMMENDED"

        async with factory() as db, db.begin():
            await JobTargetService(db).update_requirements(user_id, target_id, [
                RequirementInput(statement="熟悉 Kafka 与 Redis 的使用与问题排查"),
                RequirementInput(statement="熟悉 Python 开发与 RAG 检索增强，能独立完成接口设计与实现"),
                RequirementInput(statement="有 RAG 检索增强项目经验者优先"),
            ])
        async with factory() as db, db.begin():
            await JobTargetService(db).confirm(user_id, target_id)
        async with factory() as db:
            after = await JobTargetService(db).skill_recommendations(user_id, target_id)

        assert after["target_version"] == 2
        assert after["input_fingerprint"] != before["input_fingerprint"]
        statuses = {e["requirement_statement"]: e["evidence_status"]
                    for candidate in after["candidates"] for e in candidate["evidence"]
                    if e.get("field") == "requirements"}
        # The untouched rows still quote the JD; the added one says it cannot.
        assert statuses.get("有 RAG 检索增强项目经验者优先") == "USER_ADDED_NO_VERBATIM_QUOTE"
        # Round 19: "向量数据库" no longer counts as the bank rules' "数据库",
        # so a substring-only row is not projected as a reason for any Skill.
        assert any(status == "VERBATIM_FROM_JD" for status in statuses.values())

    body(database_url, scenario)


def test_an_edited_statement_drops_its_own_quote(database_url):
    """Changing the wording is a new claim: it must not inherit the old quote."""
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await parsed_target(factory, user_id)
        async with factory() as db, db.begin():
            saved = await JobTargetService(db).update_requirements(user_id, target_id, [
                RequirementInput(statement="精通 Kafka 事务与幂等设计"),
            ])
        row = saved["requirements"][0]
        assert row["statement"] == "精通 Kafka 事务与幂等设计"
        assert row["evidence"] is None

    body(database_url, scenario)
