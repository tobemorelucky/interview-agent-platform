"""Admin audit log API."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.permissions import require_admin
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.modules.audit.service import AuditService

router = APIRouter(prefix="/api/v1/admin/audit", tags=["admin_audit"])


@router.get("/logs")
async def list_audit_logs(
    actor_user_id: int | None = Query(None),
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    request_id: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await AuditService(db).list_events(
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        offset=offset,
        limit=limit,
    )
    return success(data=result)
