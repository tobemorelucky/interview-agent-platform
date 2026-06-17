"""Service layer for user memory foundation."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.memory.models import UserMemoryEvent, UserMemoryItem
from interview_api.modules.memory.policies import (
    MemoryActor,
    ensure_explicit_safety_update,
    ensure_user_actor,
)
from interview_api.modules.memory.repository import (
    UserMemoryRepository,
    UserSkillProfileRepository,
)


class MemoryService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.memory_repo = UserMemoryRepository(db)
        self.skill_repo = UserSkillProfileRepository(db)

    async def create_memory(
        self,
        user_id: int,
        payload: dict,
        actor: MemoryActor,
    ) -> dict:
        ensure_user_actor(actor)
        item = UserMemoryItem(
            user_id=user_id,
            memory_type=payload["memory_type"],
            scope=payload.get("scope", "INTERVIEW"),
            key=_strip_or_none(payload.get("key")),
            content=payload["content"].strip(),
            summary=_strip_or_none(payload.get("summary")),
            metadata_json=payload.get("metadata_json"),
            confidence=payload.get("confidence", 0.8),
            importance=payload.get("importance", 0.5),
            source_type=payload.get("source_type"),
            source_id=payload.get("source_id"),
            visibility=payload.get("visibility", "PRIVATE"),
            expires_at=payload.get("expires_at"),
        )
        created = await self.memory_repo.create_memory_item(item)
        await self._write_event(
            user_id=user_id,
            memory_item_id=created.id,
            event_type="CREATED",
            actor=actor,
            before=None,
            after=self._item_to_dict(created),
            reason="manual_create",
        )
        await self.db.commit()
        return self._item_to_dict(created)

    async def list_memories(
        self,
        user_id: int,
        *,
        memory_type: str | None = None,
        scope: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        include_safety = memory_type == "SAFETY"
        items, total = await self.memory_repo.list_memory_items(
            user_id,
            memory_type=memory_type,
            scope=scope,
            status=status,
            keyword=keyword,
            include_safety=include_safety,
            offset=offset,
            limit=limit,
        )
        if items:
            await self._touch_read(user_id, [item.id for item in items])
            await self.db.commit()
        return {
            "items": [self._item_to_dict(item) for item in items],
            "total": total,
        }

    async def update_memory(
        self,
        user_id: int,
        memory_id: int,
        payload: dict,
        actor: MemoryActor,
    ) -> dict:
        ensure_user_actor(actor)
        item = await self.memory_repo.get_memory_item(user_id, memory_id)
        if not item or item.status == "DELETED":
            raise LookupError("记忆不存在")
        ensure_explicit_safety_update(item.memory_type, payload.get("memory_type"))

        before = self._item_to_dict(item)
        values = {
            key: value
            for key, value in payload.items()
            if key
            in {
                "memory_type",
                "scope",
                "key",
                "content",
                "summary",
                "metadata_json",
                "confidence",
                "importance",
                "source_type",
                "source_id",
                "status",
                "visibility",
                "expires_at",
            }
        }
        if "key" in values:
            values["key"] = _strip_or_none(values["key"])
        if "content" in values and isinstance(values["content"], str):
            values["content"] = values["content"].strip()
        if "summary" in values:
            values["summary"] = _strip_or_none(values["summary"])
        if not values:
            raise ValueError("没有可更新的字段")
        values["updated_at"] = datetime.now(timezone.utc)

        updated = await self.memory_repo.update_memory_item(user_id, memory_id, **values)
        if not updated:
            raise LookupError("记忆不存在")
        await self._write_event(
            user_id=user_id,
            memory_item_id=updated.id,
            event_type="UPDATED",
            actor=actor,
            before=before,
            after=self._item_to_dict(updated),
            reason="manual_update",
        )
        await self.db.commit()
        return self._item_to_dict(updated)

    async def delete_memory(
        self,
        user_id: int,
        memory_id: int,
        actor: MemoryActor,
    ) -> bool:
        ensure_user_actor(actor)
        item = await self.memory_repo.get_memory_item(user_id, memory_id)
        if not item or item.status == "DELETED":
            raise LookupError("记忆不存在")
        before = self._item_to_dict(item)
        deleted = await self.memory_repo.soft_delete_memory_item(user_id, memory_id)
        if not deleted:
            raise LookupError("记忆不存在")
        await self._write_event(
            user_id=user_id,
            memory_item_id=memory_id,
            event_type="DELETED",
            actor=actor,
            before=before,
            after=self._item_to_dict(deleted),
            reason="soft_delete",
        )
        await self.db.commit()
        return True

    async def search_memories(
        self,
        user_id: int,
        *,
        query: str,
        memory_types: list[str] | None = None,
        limit: int = 20,
    ) -> dict:
        items = await self.memory_repo.search_memory_items(
            user_id,
            query_text=query.strip(),
            memory_types=memory_types,
            limit=limit,
        )
        if items:
            await self._touch_read(user_id, [item.id for item in items])
            await self.db.commit()
        return {
            "items": [self._item_to_dict(item) for item in items],
            "total": len(items),
        }

    async def list_skill_profiles(self, user_id: int) -> dict:
        items = await self.skill_repo.list_skill_profiles(user_id)
        return {"items": [self._skill_to_dict(item) for item in items], "total": len(items)}

    async def upsert_skill_profile(
        self,
        user_id: int,
        *,
        skill_name: str,
        score_delta: float = 0.0,
        evidence: dict | None = None,
    ) -> dict:
        existing = await self.skill_repo.get_skill_profile(user_id, skill_name)
        base_score = existing.level_score if existing else 0.0
        level_score = max(0.0, min(1.0, base_score + score_delta))
        profile = await self.skill_repo.upsert_skill_profile(
            user_id,
            skill_name=skill_name,
            level_score=level_score,
            evidence_count_delta=1 if evidence else 0,
            metadata_json=evidence,
        )
        await self.db.commit()
        return self._skill_to_dict(profile)

    async def list_memory_events(
        self,
        user_id: int,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        items, total = await self.memory_repo.list_memory_events(
            user_id,
            offset=offset,
            limit=limit,
        )
        return {"items": [self._event_to_dict(item) for item in items], "total": total}

    async def _touch_read(self, user_id: int, memory_ids: list[int]) -> None:
        now = datetime.now(timezone.utc)
        for memory_id in memory_ids:
            await self.memory_repo.update_memory_item(
                user_id,
                memory_id,
                last_accessed_at=now,
            )
            await self._write_event(
                user_id=user_id,
                memory_item_id=memory_id,
                event_type="READ",
                actor=MemoryActor(actor_type="USER", actor_id=user_id),
                before=None,
                after=None,
                reason="api_read",
            )

    async def _write_event(
        self,
        *,
        user_id: int,
        memory_item_id: int | None,
        event_type: str,
        actor: MemoryActor,
        before: dict | None,
        after: dict | None,
        reason: str | None,
    ) -> None:
        await self.memory_repo.create_memory_event(
            UserMemoryEvent(
                user_id=user_id,
                memory_item_id=memory_item_id,
                event_type=event_type,
                actor_type=actor.actor_type,
                actor_id=actor.actor_id,
                before_json=before,
                after_json=after,
                reason=reason,
            )
        )

    @staticmethod
    def _item_to_dict(item: UserMemoryItem) -> dict:
        return {
            "id": item.id,
            "user_id": item.user_id,
            "memory_type": item.memory_type,
            "scope": item.scope,
            "key": item.key,
            "content": item.content,
            "summary": item.summary,
            "metadata_json": item.metadata_json,
            "confidence": item.confidence,
            "importance": item.importance,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "status": item.status,
            "visibility": item.visibility,
            "created_at": _iso(item.created_at),
            "updated_at": _iso(item.updated_at),
            "last_accessed_at": _iso(item.last_accessed_at),
            "expires_at": _iso(item.expires_at),
        }

    @staticmethod
    def _skill_to_dict(item: Any) -> dict:
        return {
            "id": item.id,
            "user_id": item.user_id,
            "skill_name": item.skill_name,
            "skill_category": item.skill_category,
            "level_score": item.level_score,
            "confidence": item.confidence,
            "evidence_count": item.evidence_count,
            "weakness_summary": item.weakness_summary,
            "strength_summary": item.strength_summary,
            "last_evaluated_at": _iso(item.last_evaluated_at),
            "metadata_json": item.metadata_json,
            "created_at": _iso(item.created_at),
            "updated_at": _iso(item.updated_at),
        }

    @staticmethod
    def _event_to_dict(item: UserMemoryEvent) -> dict:
        return {
            "id": item.id,
            "user_id": item.user_id,
            "memory_item_id": item.memory_item_id,
            "event_type": item.event_type,
            "actor_type": item.actor_type,
            "actor_id": item.actor_id,
            "before_json": item.before_json,
            "after_json": item.after_json,
            "reason": item.reason,
            "created_at": _iso(item.created_at),
        }


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
