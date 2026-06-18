"""Audit log service."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.audit.models import AuditLog
from interview_api.modules.audit.repository import AuditLogRepository
from interview_api.modules.audit.schemas import AuditLogRead


class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AuditLogRepository(db)

    async def log_event(
        self,
        *,
        action: str,
        request_id: str | None = None,
        actor_user_id: int | None = None,
        actor_role: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        before_json: dict[str, Any] | None = None,
        after_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        status: str = "SUCCESS",
        error_message: str | None = None,
    ) -> AuditLog:
        item = AuditLog(
            request_id=request_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            before_json=before_json,
            after_json=after_json,
            metadata_json=metadata_json,
            status=status,
            error_message=error_message,
        )
        created = await self.repo.create(item)
        await self.db.commit()
        return created

    async def list_events(self, **filters) -> dict:
        items, total = await self.repo.list_events(**filters)
        return {
            "items": [AuditLogRead.model_validate(item).model_dump() for item in items],
            "total": total,
        }


def audit_request_metadata(request) -> dict:
    return {
        "request_id": getattr(request.state, "request_id", None),
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }
