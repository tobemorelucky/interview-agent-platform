"""Audit log repository."""

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.audit.models import AuditLog


class AuditLogRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, item: AuditLog) -> AuditLog:
        self.db.add(item)
        await self.db.flush()
        return item

    async def list_events(
        self,
        *,
        actor_user_id: int | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        request_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[AuditLog], int]:
        query = select(AuditLog)
        count_query = select(func.count(AuditLog.id))
        filters = []
        if actor_user_id is not None:
            filters.append(AuditLog.actor_user_id == actor_user_id)
        if action:
            filters.append(AuditLog.action == action)
        if resource_type:
            filters.append(AuditLog.resource_type == resource_type)
        if resource_id:
            filters.append(AuditLog.resource_id == resource_id)
        if request_id:
            filters.append(AuditLog.request_id == request_id)
        if filters:
            query = query.where(*filters)
            count_query = count_query.where(*filters)
        total = (await self.db.execute(count_query)).scalar() or 0
        result = await self.db.execute(
            query.order_by(desc(AuditLog.created_at)).offset(offset).limit(limit)
        )
        return list(result.scalars().all()), int(total)
