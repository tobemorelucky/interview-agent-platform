"""Round 23 / L3: purge request endpoints.

Intake and status are user-facing. The actual execution is dispatched only
when the server-side capability check passes; the UI has no execution switch.
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.api.deps import get_current_user
from interview_api.core.config import settings
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.modules.users.models import User
from . import service
from .constants import RESOURCE_TYPES
from .models import PurgeRequest

router = APIRouter(prefix="/api/v1/purges", tags=["purges"])


class PurgeRequestBody(BaseModel):
    resource_type: str = Field(pattern=r"^(RESUME|PRIVATE_JD)$")
    resource_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=128)


@router.get("/capability")
async def capability():
    """Front-end reads this; it is informational, never a switch."""
    return success(data=service.execution_capability())


@router.get("")
async def list_requests(limit: int = Query(50, ge=1, le=200),
                        db: AsyncSession = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    rows = await service.list_mine(db, current_user.id, limit)
    return success(data=[service.serialize(r) for r in rows])


@router.get("/{request_id}")
async def get_request(request_id: int, db: AsyncSession = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    return success(data=service.serialize(await service.get_mine(db, current_user.id, request_id)))


@router.post("", status_code=201)
async def create_request(body: PurgeRequestBody, db: AsyncSession = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    """Accept a purge request: establishes the persistent fence immediately.

    Idempotent for the same (user, resource); a reused key on another resource
    is refused. Execution is a separate server-side decision.
    """
    row, created = await service.request_purge(
        db, current_user.id, body.resource_type, body.resource_id, body.idempotency_key)

    dispatched = False
    gate_note = ""
    if created:
        db_name = make_url(settings.database_url).database or ""
        allowed, gate_note = service.can_execute(current_user.id, db_name)
        if allowed:
            from interview_api.infrastructure.tasks.celery_client import dispatch_purge
            dispatch_purge(row.id)
            dispatched = True
    return success(data={**service.serialize(row), "created": created,
                         "execution_dispatched": dispatched,
                         "execution_gate": gate_note or ("ENABLED" if dispatched else "NOT_EVALUATED")})
