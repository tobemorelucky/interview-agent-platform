"""Phase 4: Experience keyword presets repository."""

from sqlalchemy import select, update, delete, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.experience.models import (
    ExperienceKeywordPreset,
    ExperienceCollectionTask,
    ExperienceSourceItem,
)


class ExperienceKeywordPresetRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_type(
        self,
        preset_type: str | None = None,
        enabled: bool | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[ExperienceKeywordPreset], int]:
        query = select(ExperienceKeywordPreset)
        count_query = select(func.count(ExperienceKeywordPreset.id))

        if preset_type:
            query = query.where(ExperienceKeywordPreset.preset_type == preset_type)
            count_query = count_query.where(ExperienceKeywordPreset.preset_type == preset_type)
        if enabled is not None:
            query = query.where(ExperienceKeywordPreset.enabled == enabled)
            count_query = count_query.where(ExperienceKeywordPreset.enabled == enabled)

        total = (await self.db.execute(count_query)).scalar() or 0
        result = await self.db.execute(
            query.order_by(ExperienceKeywordPreset.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), total

    async def get_by_id(self, preset_id: int) -> ExperienceKeywordPreset | None:
        result = await self.db.execute(
            select(ExperienceKeywordPreset).where(ExperienceKeywordPreset.id == preset_id)
        )
        return result.scalar_one_or_none()

    async def get_by_type_and_name(
        self, preset_type: str, name: str
    ) -> ExperienceKeywordPreset | None:
        result = await self.db.execute(
            select(ExperienceKeywordPreset).where(
                ExperienceKeywordPreset.preset_type == preset_type,
                ExperienceKeywordPreset.name == name,
            )
        )
        return result.scalar_one_or_none()

    async def create(self, preset: ExperienceKeywordPreset) -> ExperienceKeywordPreset:
        self.db.add(preset)
        await self.db.flush()
        return preset

    async def update(
        self, preset_id: int, **values
    ) -> ExperienceKeywordPreset | None:
        await self.db.execute(
            update(ExperienceKeywordPreset)
            .where(ExperienceKeywordPreset.id == preset_id)
            .values(**values)
        )
        await self.db.flush()
        return await self.get_by_id(preset_id)

    async def delete(self, preset_id: int) -> bool:
        result = await self.db.execute(
            delete(ExperienceKeywordPreset).where(
                ExperienceKeywordPreset.id == preset_id
            )
        )
        await self.db.flush()
        return result.rowcount > 0


class ExperienceCollectionTaskRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, task: ExperienceCollectionTask) -> ExperienceCollectionTask:
        self.db.add(task)
        await self.db.flush()
        return task

    async def list_tasks(
        self,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ExperienceCollectionTask], int]:
        query = select(ExperienceCollectionTask)
        count_query = select(func.count(ExperienceCollectionTask.id))
        if status:
            query = query.where(ExperienceCollectionTask.status == status)
            count_query = count_query.where(ExperienceCollectionTask.status == status)
        total = (await self.db.execute(count_query)).scalar() or 0
        result = await self.db.execute(
            query.order_by(desc(ExperienceCollectionTask.created_at))
            .offset(offset).limit(limit)
        )
        return list(result.scalars().all()), total

    async def get_by_id(self, task_id: int) -> ExperienceCollectionTask | None:
        result = await self.db.execute(
            select(ExperienceCollectionTask).where(ExperienceCollectionTask.id == task_id)
        )
        return result.scalar_one_or_none()

    async def update(
        self, task_id: int, **values
    ) -> ExperienceCollectionTask | None:
        await self.db.execute(
            update(ExperienceCollectionTask)
            .where(ExperienceCollectionTask.id == task_id)
            .values(**values)
        )
        await self.db.flush()
        return await self.get_by_id(task_id)

    async def delete(self, task_id: int) -> bool:
        result = await self.db.execute(
            delete(ExperienceCollectionTask).where(ExperienceCollectionTask.id == task_id)
        )
        await self.db.flush()
        return result.rowcount > 0


class ExperienceSourceItemRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_source_item(
        self, item: ExperienceSourceItem
    ) -> ExperienceSourceItem:
        self.db.add(item)
        await self.db.flush()
        return item

    async def bulk_create_source_items(
        self, items: list[ExperienceSourceItem]
    ) -> list[ExperienceSourceItem]:
        self.db.add_all(items)
        await self.db.flush()
        return items

    async def exists_by_task_and_url_hash(
        self, task_id: int, normalized_url_hash: str
    ) -> bool:
        result = await self.db.execute(
            select(ExperienceSourceItem.id).where(
                ExperienceSourceItem.task_id == task_id,
                ExperienceSourceItem.normalized_url_hash == normalized_url_hash,
            )
        )
        return result.first() is not None

    async def list_source_items_by_task(
        self,
        task_id: int,
        *,
        offset: int = 0,
        limit: int = 50,
        fetch_status: str | None = None,
    ) -> tuple[list[ExperienceSourceItem], int]:
        query = select(ExperienceSourceItem).where(ExperienceSourceItem.task_id == task_id)
        count_query = select(func.count(ExperienceSourceItem.id)).where(
            ExperienceSourceItem.task_id == task_id
        )
        if fetch_status:
            query = query.where(ExperienceSourceItem.fetch_status == fetch_status)
            count_query = count_query.where(ExperienceSourceItem.fetch_status == fetch_status)

        total = (await self.db.execute(count_query)).scalar() or 0
        result = await self.db.execute(
            query.order_by(desc(ExperienceSourceItem.created_at))
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), total

    async def count_source_items_by_task(
        self,
        task_id: int,
        *,
        fetch_status: str | None = None,
    ) -> int:
        query = select(func.count(ExperienceSourceItem.id)).where(
            ExperienceSourceItem.task_id == task_id
        )
        if fetch_status:
            query = query.where(ExperienceSourceItem.fetch_status == fetch_status)
        return (await self.db.execute(query)).scalar() or 0
