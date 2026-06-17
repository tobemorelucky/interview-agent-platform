"""Smoke test for read-only interview memory context.

Usage:
    cd apps/api
    uv run python scripts/smoke_memory_interview_context.py
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

import interview_api.modules.models  # noqa: F401
from interview_api.core.security import hash_password
from interview_api.infrastructure.db.engine import engine
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.memory.context_builder import MemoryContextBuilder
from interview_api.modules.memory.models import (
    UserMemoryEvent,
    UserMemoryItem,
    UserSkillProfile,
)
from interview_api.modules.users.models import User


SMOKE_EMAIL = "memory-interview-context-smoke@test.local"
NO_MEMORY_EMAIL = "memory-interview-context-empty@test.local"


async def main() -> None:
    async with async_session_factory() as db:
        await db.execute(delete(User).where(User.email == SMOKE_EMAIL))
        await db.execute(delete(User).where(User.email == NO_MEMORY_EMAIL))
        await db.commit()

        no_memory_user = User(
            email=NO_MEMORY_EMAIL,
            username="memory_interview_context_empty",
            password_hash=hash_password("123456"),
            role="USER",
        )
        db.add(no_memory_user)
        await db.flush()
        empty_context = await MemoryContextBuilder(db).build_interview_memory_context(
            no_memory_user.id,
            target_position="后端开发",
        )
        assert empty_context.injected is False
        assert empty_context.to_prompt_text() == ""
        await db.execute(delete(User).where(User.id == no_memory_user.id))
        await db.flush()

        user = User(
            email=SMOKE_EMAIL,
            username="memory_interview_context_smoke",
            password_hash=hash_password("123456"),
            role="USER",
        )
        db.add(user)
        await db.flush()

        now = datetime.now(timezone.utc)
        db.add_all(
            [
                UserMemoryItem(
                    user_id=user.id,
                    memory_type="SEMANTIC",
                    scope="INTERVIEW",
                    key="target",
                    content="用户历史目标岗位曾经是算法工程师。",
                    summary="历史目标岗位曾经是算法工程师。",
                    confidence=0.9,
                    importance=0.8,
                    status="ACTIVE",
                ),
                UserMemoryItem(
                    user_id=user.id,
                    memory_type="PREFERENCE",
                    scope="INTERVIEW",
                    key="style",
                    content="用户偏好严格追问，不要太早给标准答案。",
                    summary="偏好严格追问。",
                    confidence=0.9,
                    importance=0.9,
                    status="ACTIVE",
                ),
                UserMemoryItem(
                    user_id=user.id,
                    memory_type="SAFETY",
                    scope="SYSTEM",
                    key="privacy",
                    content="不要在面试回复里透露用户隐私信息。",
                    summary="尊重隐私，不暴露个人信息。",
                    confidence=1.0,
                    importance=1.0,
                    status="ACTIVE",
                ),
                UserMemoryItem(
                    user_id=user.id,
                    memory_type="PREFERENCE",
                    scope="INTERVIEW",
                    key="deleted",
                    content="这条废弃偏好不应进入 prompt。",
                    confidence=1.0,
                    importance=1.0,
                    status="DELETED",
                ),
            ]
        )
        db.add_all(
            [
                UserSkillProfile(
                    user_id=user.id,
                    skill_name="Redis",
                    skill_category="backend",
                    level_score=0.78,
                    confidence=0.9,
                    evidence_count=3,
                    strength_summary="缓存设计回答较完整。",
                    last_evaluated_at=now,
                ),
                UserSkillProfile(
                    user_id=user.id,
                    skill_name="数据库索引",
                    skill_category="backend",
                    level_score=0.35,
                    confidence=0.85,
                    evidence_count=2,
                    weakness_summary="索引失效和执行计划解释不够稳定。",
                    last_evaluated_at=now,
                ),
            ]
        )
        await db.flush()

        before_events = await _event_count(db, user.id)
        context = await MemoryContextBuilder(db).build_interview_memory_context(
            user.id,
            target_position="后端开发",
        )
        after_events = await _event_count(db, user.id)
        prompt_text = context.to_prompt_text()

        assert context.injected is True
        assert "后端开发" in context.semantic_summary
        assert "严格追问" in context.preference_summary
        assert "Redis" in context.skill_summary
        assert "索引" in context.weakness_summary
        assert "隐私" in context.safety_summary
        assert "废弃偏好" not in prompt_text
        assert "用户长期记忆与能力画像" not in prompt_text
        assert before_events == after_events == 0

        print("memory_context_injected", context.injected)
        print("counts", context.counts)
        print("prompt_preview", prompt_text[:240].replace("\n", " | "))

        await db.execute(delete(User).where(User.id == user.id))
        await db.commit()

    await engine.dispose()


async def _event_count(db, user_id: int) -> int:
    result = await db.execute(
        select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == user_id)
    )
    return int(result.scalar() or 0)


if __name__ == "__main__":
    asyncio.run(main())
