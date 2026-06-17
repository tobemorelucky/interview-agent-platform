"""Repositories for user memory foundation."""

from datetime import datetime, timezone

from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.memory.models import (
    UserMemoryEvent,
    UserMemoryItem,
    UserSkillProfile,
)


class UserMemoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_memory_item(self, item: UserMemoryItem) -> UserMemoryItem:
        self.db.add(item)
        await self.db.flush()
        return item

    async def list_memory_items(
        self,
        user_id: int,
        *,
        memory_type: str | None = None,
        scope: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
        include_safety: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[UserMemoryItem], int]:
        query = select(UserMemoryItem).where(UserMemoryItem.user_id == user_id)
        count_query = select(func.count(UserMemoryItem.id)).where(UserMemoryItem.user_id == user_id)

        if status:
            query = query.where(UserMemoryItem.status == status)
            count_query = count_query.where(UserMemoryItem.status == status)
        else:
            query = query.where(UserMemoryItem.status != "DELETED")
            count_query = count_query.where(UserMemoryItem.status != "DELETED")

        if memory_type:
            query = query.where(UserMemoryItem.memory_type == memory_type)
            count_query = count_query.where(UserMemoryItem.memory_type == memory_type)
        elif not include_safety:
            query = query.where(UserMemoryItem.memory_type != "SAFETY")
            count_query = count_query.where(UserMemoryItem.memory_type != "SAFETY")

        if scope:
            query = query.where(UserMemoryItem.scope == scope)
            count_query = count_query.where(UserMemoryItem.scope == scope)

        if keyword:
            pattern = f"%{keyword}%"
            condition = or_(
                UserMemoryItem.key.ilike(pattern),
                UserMemoryItem.content.ilike(pattern),
                UserMemoryItem.summary.ilike(pattern),
            )
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = (await self.db.execute(count_query)).scalar() or 0
        result = await self.db.execute(
            query.order_by(desc(UserMemoryItem.importance), desc(UserMemoryItem.updated_at))
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), total

    async def get_memory_item(self, user_id: int, memory_id: int) -> UserMemoryItem | None:
        result = await self.db.execute(
            select(UserMemoryItem).where(
                UserMemoryItem.id == memory_id,
                UserMemoryItem.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def update_memory_item(
        self,
        user_id: int,
        memory_id: int,
        **values,
    ) -> UserMemoryItem | None:
        await self.db.execute(
            update(UserMemoryItem)
            .where(UserMemoryItem.id == memory_id, UserMemoryItem.user_id == user_id)
            .values(**values)
        )
        await self.db.flush()
        return await self.get_memory_item(user_id, memory_id)

    async def soft_delete_memory_item(self, user_id: int, memory_id: int) -> UserMemoryItem | None:
        return await self.update_memory_item(
            user_id,
            memory_id,
            status="DELETED",
            updated_at=datetime.now(timezone.utc),
        )

    async def search_memory_items(
        self,
        user_id: int,
        *,
        query_text: str,
        memory_types: list[str] | None = None,
        limit: int = 20,
    ) -> list[UserMemoryItem]:
        pattern = f"%{query_text}%"
        query = select(UserMemoryItem).where(
            UserMemoryItem.user_id == user_id,
            UserMemoryItem.status != "DELETED",
            or_(
                UserMemoryItem.key.ilike(pattern),
                UserMemoryItem.content.ilike(pattern),
                UserMemoryItem.summary.ilike(pattern),
            ),
        )
        if memory_types:
            query = query.where(UserMemoryItem.memory_type.in_(memory_types))
        else:
            query = query.where(UserMemoryItem.memory_type != "SAFETY")
        result = await self.db.execute(
            query.order_by(desc(UserMemoryItem.importance), desc(UserMemoryItem.updated_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create_memory_event(self, event: UserMemoryEvent) -> UserMemoryEvent:
        self.db.add(event)
        await self.db.flush()
        return event

    async def list_memory_events(
        self,
        user_id: int,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[UserMemoryEvent], int]:
        query = select(UserMemoryEvent).where(UserMemoryEvent.user_id == user_id)
        count_query = select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == user_id)
        total = (await self.db.execute(count_query)).scalar() or 0
        result = await self.db.execute(
            query.order_by(desc(UserMemoryEvent.created_at)).offset(offset).limit(limit)
        )
        return list(result.scalars().all()), total


class UserSkillProfileRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_skill_profiles(self, user_id: int) -> list[UserSkillProfile]:
        result = await self.db.execute(
            select(UserSkillProfile)
            .where(UserSkillProfile.user_id == user_id)
            .order_by(desc(UserSkillProfile.confidence), desc(UserSkillProfile.level_score))
        )
        return list(result.scalars().all())

    async def get_skill_profile(
        self,
        user_id: int,
        skill_name: str,
    ) -> UserSkillProfile | None:
        result = await self.db.execute(
            select(UserSkillProfile).where(
                UserSkillProfile.user_id == user_id,
                UserSkillProfile.skill_name == skill_name,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_skill_profile(
        self,
        user_id: int,
        *,
        skill_name: str,
        skill_category: str | None = None,
        level_score: float | None = None,
        confidence: float | None = None,
        evidence_count_delta: int = 0,
        weakness_summary: str | None = None,
        strength_summary: str | None = None,
        metadata_json: dict | None = None,
    ) -> UserSkillProfile:
        existing = await self.get_skill_profile(user_id, skill_name)
        now = datetime.now(timezone.utc)
        if existing:
            values = {
                "skill_category": skill_category if skill_category is not None else existing.skill_category,
                "level_score": level_score if level_score is not None else existing.level_score,
                "confidence": confidence if confidence is not None else existing.confidence,
                "evidence_count": existing.evidence_count + evidence_count_delta,
                "weakness_summary": weakness_summary if weakness_summary is not None else existing.weakness_summary,
                "strength_summary": strength_summary if strength_summary is not None else existing.strength_summary,
                "metadata_json": metadata_json if metadata_json is not None else existing.metadata_json,
                "last_evaluated_at": now,
                "updated_at": now,
            }
            await self.db.execute(
                update(UserSkillProfile)
                .where(UserSkillProfile.id == existing.id)
                .values(**values)
            )
            await self.db.flush()
            return await self.get_skill_profile(user_id, skill_name) or existing

        profile = UserSkillProfile(
            user_id=user_id,
            skill_name=skill_name,
            skill_category=skill_category,
            level_score=level_score or 0.0,
            confidence=confidence or 0.5,
            evidence_count=max(0, evidence_count_delta),
            weakness_summary=weakness_summary,
            strength_summary=strength_summary,
            last_evaluated_at=now,
            metadata_json=metadata_json,
        )
        self.db.add(profile)
        await self.db.flush()
        return profile
