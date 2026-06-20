"""Phase 4: Experience keyword presets repository."""

from sqlalchemy import case, select, update, delete, func, desc
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

    async def list_fetchable_source_items(
        self,
        task_id: int,
        *,
        retry_failed: bool = False,
        limit: int = 20,
    ) -> list[ExperienceSourceItem]:
        statuses = ["FETCH_FAILED"] if retry_failed else ["DISCOVERED"]
        result = await self.db.execute(
            select(ExperienceSourceItem)
            .where(
                ExperienceSourceItem.task_id == task_id,
                ExperienceSourceItem.fetch_status.in_(statuses),
            )
            .order_by(ExperienceSourceItem.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_fetch_stats_by_task(self, task_id: int) -> dict:
        result = await self.db.execute(
            select(
                func.count(ExperienceSourceItem.id),
                func.coalesce(
                    func.sum(case((ExperienceSourceItem.fetch_status == "DISCOVERED", 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((ExperienceSourceItem.fetch_status == "FETCHED", 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((ExperienceSourceItem.fetch_status == "FETCH_FAILED", 1), else_=0)),
                    0,
                ),
                func.avg(func.length(ExperienceSourceItem.raw_text)),
                func.max(func.length(ExperienceSourceItem.raw_text)),
                func.min(func.length(ExperienceSourceItem.raw_text)),
            ).where(ExperienceSourceItem.task_id == task_id)
        )
        row = result.one()
        return {
            "total": int(row[0] or 0),
            "discovered_count": int(row[1] or 0),
            "fetched_count": int(row[2] or 0),
            "failed_count": int(row[3] or 0),
            "avg_raw_text_chars": int(row[4] or 0),
            "max_raw_text_chars": int(row[5] or 0),
            "min_raw_text_chars": int(row[6] or 0),
        }

    async def list_failure_reasons_by_task(self, task_id: int) -> list[dict]:
        result = await self.db.execute(
            select(
                ExperienceSourceItem.error_message,
                func.count(ExperienceSourceItem.id),
            )
            .where(
                ExperienceSourceItem.task_id == task_id,
                ExperienceSourceItem.fetch_status == "FETCH_FAILED",
            )
            .group_by(ExperienceSourceItem.error_message)
            .order_by(desc(func.count(ExperienceSourceItem.id)))
        )
        return [
            {"reason": row[0] or "unknown", "count": int(row[1] or 0)}
            for row in result.all()
        ]

    async def list_platform_fetch_stats_by_task(self, task_id: int) -> list[dict]:
        platform_expr = func.coalesce(ExperienceSourceItem.platform, "通用搜索")
        result = await self.db.execute(
            select(
                platform_expr,
                func.count(ExperienceSourceItem.id),
                func.coalesce(
                    func.sum(case((ExperienceSourceItem.fetch_status == "FETCHED", 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((ExperienceSourceItem.fetch_status == "FETCH_FAILED", 1), else_=0)),
                    0,
                ),
            )
            .where(ExperienceSourceItem.task_id == task_id)
            .group_by(platform_expr)
            .order_by(platform_expr)
        )
        return [
            {
                "platform": row[0] or "通用搜索",
                "total": int(row[1] or 0),
                "fetched": int(row[2] or 0),
                "failed": int(row[3] or 0),
            }
            for row in result.all()
        ]

    async def get_source_item(self, source_id: int) -> ExperienceSourceItem | None:
        result = await self.db.execute(
            select(ExperienceSourceItem).where(ExperienceSourceItem.id == source_id)
        )
        return result.scalar_one_or_none()

    async def update_source_item_fetch_result(
        self,
        source_id: int,
        **values,
    ) -> ExperienceSourceItem | None:
        await self.db.execute(
            update(ExperienceSourceItem)
            .where(ExperienceSourceItem.id == source_id)
            .values(**values)
        )
        await self.db.flush()
        return await self.get_source_item(source_id)

    async def update_source_item_fetch_success(
        self,
        source_id: int,
        *,
        raw_text: str,
        content_hash: str,
        fetched_at,
        title: str | None = None,
    ) -> ExperienceSourceItem | None:
        values = {
            "raw_text": raw_text,
            "content_hash": content_hash,
            "fetched_at": fetched_at,
            "fetch_status": "FETCHED",
            "error_message": None,
        }
        if title:
            values["title"] = title
        return await self.update_source_item_fetch_result(source_id, **values)

    async def update_source_item_fetch_failed(
        self,
        source_id: int,
        *,
        fetched_at,
        error_message: str,
    ) -> ExperienceSourceItem | None:
        return await self.update_source_item_fetch_result(
            source_id,
            fetched_at=fetched_at,
            fetch_status="FETCH_FAILED",
            error_message=error_message,
        )

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
