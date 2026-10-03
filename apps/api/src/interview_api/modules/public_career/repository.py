"""Persistence helpers for public-data refresh tasks."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.public_career.models import PublicDataRefreshTask


class PublicDataRefreshTaskRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, task_id: int) -> PublicDataRefreshTask | None:
        return await self.db.get(PublicDataRefreshTask, task_id)

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> PublicDataRefreshTask | None:
        return await self.db.scalar(
            select(PublicDataRefreshTask).where(
                PublicDataRefreshTask.idempotency_key == idempotency_key
            )
        )

    async def create(
        self,
        *,
        target_type: str,
        target_ref: str | None,
        reason: str | None,
        idempotency_key: str | None = None,
    ) -> PublicDataRefreshTask:
        task = PublicDataRefreshTask(
            target_type=target_type,
            target_ref=target_ref,
            reason=reason,
            idempotency_key=idempotency_key,
            status="PENDING",
        )
        self.db.add(task)
        await self.db.flush()
        return task
