"""Smoke test for controlled interview memory writes.

Usage:
    cd apps/api
    uv run python scripts/smoke_memory_interview_write.py
"""

import asyncio

from sqlalchemy import delete, func, select, update

import interview_api.modules.models  # noqa: F401
from interview_api.core.security import hash_password
from interview_api.infrastructure.db.engine import engine
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.interview.models import (
    InterviewMessage,
    InterviewSession,
    InterviewSessionQuestion,
)
from interview_api.modules.memory.interview_writer import InterviewMemoryWriter
from interview_api.modules.memory.models import (
    UserMemoryEvent,
    UserMemoryItem,
    UserSkillProfile,
)
from interview_api.modules.users.models import User


USER_EMAIL = "memory-write-smoke@test.local"
OTHER_EMAIL = "memory-write-other@test.local"


async def main() -> None:
    async with async_session_factory() as db:
        await _cleanup(db)

        user = User(
            email=USER_EMAIL,
            username="memory_write_smoke",
            password_hash=hash_password("123456"),
            role="USER",
        )
        other = User(
            email=OTHER_EMAIL,
            username="memory_write_other",
            password_hash=hash_password("123456"),
            role="USER",
        )
        db.add_all([user, other])
        await db.flush()

        session = InterviewSession(
            user_id=user.id,
            title="M3 smoke",
            target_position="Java 后端",
            target_position_confirmed=True,
            memory_summary="本场候选人回答了 Redis 和 MySQL 相关问题。",
            question_generation_status="READY",
        )
        other_session = InterviewSession(user_id=other.id, title="Other")
        db.add_all([session, other_session])
        await db.flush()

        db.add_all(
            [
                InterviewMessage(
                    session_id=session.id,
                    role="USER",
                    content="以后请严格追问我，不要直接给答案。我最近主要复习 Redis。",
                    turn_index=1,
                ),
                InterviewMessage(
                    session_id=session.id,
                    role="USER",
                    content="回答完请给我评分。",
                    turn_index=1,
                ),
                InterviewMessage(
                    session_id=session.id,
                    role="ASSISTANT",
                    content="回答较完整，但 MySQL 索引失效解释不够清楚，有遗漏。",
                    metadata_json={"type": "EVALUATION", "score": 3},
                    turn_index=1,
                ),
                InterviewMessage(
                    session_id=session.id,
                    role="USER",
                    content="我目标岗位是 Java 后端。",
                    turn_index=2,
                ),
            ]
        )
        db.add_all(
            [
                InterviewSessionQuestion(
                    session_id=session.id,
                    question_index=0,
                    question="Redis 缓存穿透怎么处理？",
                    dimension="Redis",
                    status="ANSWERED",
                    answer_summary="回答较完整",
                    evaluation_json={"score": 4, "risk_tip": "缓存异常场景还可以继续追问"},
                ),
                InterviewSessionQuestion(
                    session_id=session.id,
                    question_index=1,
                    question="MySQL 索引失效有哪些情况？",
                    dimension="MySQL",
                    status="ANSWERED",
                    missing_points_json=["没有说清楚 explain 和最左前缀"],
                    evaluation_json={"score": 3, "risk_tip": "索引执行计划解释薄弱"},
                ),
            ]
        )
        deleted = UserMemoryItem(
            user_id=user.id,
            memory_type="PREFERENCE",
            scope="INTERVIEW",
            key="interview_style_preference",
            content="以后请严格追问我，不要直接给答案。我最近主要复习 Redis。",
            status="DELETED",
            confidence=0.95,
            importance=0.7,
        )
        db.add(deleted)
        await db.commit()

        writer = InterviewMemoryWriter(db)
        result = await writer.consolidate_interview_session(user.id, session.id)
        assert result["episodic_memory_created"] is True
        assert result["preferences_created"] >= 1
        assert result["skills_updated"] >= 2
        assert result["events_created"] >= 3

        episodic_count = await _memory_count(db, user.id, "EPISODIC", f"interview_session:{session.id}")
        assert episodic_count == 1

        restored_deleted_pref_count = await _active_memory_content_count(
            db,
            user.id,
            "PREFERENCE",
            "interview_style_preference",
            "以后请严格追问我，不要直接给答案。我最近主要复习 Redis。",
        )
        assert restored_deleted_pref_count == 0, "deleted explicit preference should not be restored from old session"

        focus_count = await _active_memory_count(db, user.id, "SEMANTIC", "preparation_focus")
        target_count = await _active_memory_count(db, user.id, "SEMANTIC", "target_position")
        assert focus_count == 1
        assert target_count == 1

        repeat = await InterviewMemoryWriter(db).consolidate_interview_session(user.id, session.id)
        assert repeat["episodic_memory_created"] is False
        assert repeat["existing_memory_id"] == result["episodic_memory_id"]
        assert await _memory_count(db, user.id, "EPISODIC", f"interview_session:{session.id}") == 1

        await db.execute(
            update(InterviewSession).where(InterviewSession.id == session.id).values(memory_summary="force update summary")
        )
        await db.commit()
        forced = await InterviewMemoryWriter(db).consolidate_interview_session(user.id, session.id, force=True)
        assert forced["episodic_memory_updated"] is True
        assert await _memory_count(db, user.id, "EPISODIC", f"interview_session:{session.id}") == 1

        redis = await _skill(db, user.id, "Redis")
        mysql = await _skill(db, user.id, "MySQL")
        assert redis is not None and redis.evidence_count >= 1
        assert mysql is not None and mysql.evidence_count >= 1

        event_types = await _event_types(db, user.id)
        assert "CREATED" in event_types
        assert "UPDATED" in event_types

        try:
            await InterviewMemoryWriter(db).consolidate_interview_session(user.id, other_session.id)
            raise AssertionError("user should not consolidate another user's session")
        except LookupError:
            pass

        print("consolidate_result", result)
        print("repeat_result", repeat)
        print("forced_result", forced)
        print("skills", {"Redis": redis.level_score, "MySQL": mysql.level_score})

        await _cleanup(db)
        await db.commit()

    await engine.dispose()


async def _cleanup(db) -> None:
    result = await db.execute(select(User.id).where(User.email.in_([USER_EMAIL, OTHER_EMAIL])))
    user_ids = [row[0] for row in result.all()]
    if user_ids:
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


async def _memory_count(db, user_id: int, memory_type: str, key: str) -> int:
    result = await db.execute(
        select(func.count(UserMemoryItem.id)).where(
            UserMemoryItem.user_id == user_id,
            UserMemoryItem.memory_type == memory_type,
            UserMemoryItem.key == key,
        )
    )
    return int(result.scalar() or 0)


async def _active_memory_count(db, user_id: int, memory_type: str, key: str) -> int:
    result = await db.execute(
        select(func.count(UserMemoryItem.id)).where(
            UserMemoryItem.user_id == user_id,
            UserMemoryItem.memory_type == memory_type,
            UserMemoryItem.key == key,
            UserMemoryItem.status == "ACTIVE",
        )
    )
    return int(result.scalar() or 0)


async def _active_memory_content_count(
    db,
    user_id: int,
    memory_type: str,
    key: str,
    content: str,
) -> int:
    result = await db.execute(
        select(func.count(UserMemoryItem.id)).where(
            UserMemoryItem.user_id == user_id,
            UserMemoryItem.memory_type == memory_type,
            UserMemoryItem.key == key,
            UserMemoryItem.content == content,
            UserMemoryItem.status == "ACTIVE",
        )
    )
    return int(result.scalar() or 0)


async def _skill(db, user_id: int, skill_name: str) -> UserSkillProfile | None:
    result = await db.execute(
        select(UserSkillProfile).where(
            UserSkillProfile.user_id == user_id,
            UserSkillProfile.skill_name == skill_name,
        )
    )
    return result.scalar_one_or_none()


async def _event_types(db, user_id: int) -> set[str]:
    result = await db.execute(
        select(UserMemoryEvent.event_type).where(UserMemoryEvent.user_id == user_id)
    )
    return {row[0] for row in result.all()}


if __name__ == "__main__":
    asyncio.run(main())
