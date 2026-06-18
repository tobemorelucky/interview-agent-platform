"""User memory API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.api.deps import get_current_user
from interview_api.core.rate_limit import memory_write_limit
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.modules.audit.service import AuditService, audit_request_metadata
from interview_api.modules.memory.context_builder import MemoryContextBuilder
from interview_api.modules.memory.policies import (
    MEMORY_SCOPES,
    MEMORY_STATUSES,
    MEMORY_TYPES,
    MemoryActor,
    validate_choice,
)
from interview_api.modules.memory.schemas import (
    MemorySearchRequest,
    UserMemoryItemCreate,
    UserMemoryItemUpdate,
)
from interview_api.modules.memory.service import MemoryService
from interview_api.modules.users.models import User

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


def _service(db: AsyncSession) -> MemoryService:
    return MemoryService(db)


@router.get("/interview-context")
async def get_interview_memory_context(
    target_position: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    builder = MemoryContextBuilder(db)
    context = await builder.build_interview_memory_context(
        current_user.id,
        target_position=target_position,
    )
    return success(data=context.to_dict())


@router.get("/items")
async def list_memory_items(
    memory_type: str | None = Query(None),
    scope: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    keyword: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = _service(db)
    try:
        result = await svc.list_memories(
            current_user.id,
            memory_type=_optional_choice(memory_type, MEMORY_TYPES, "memory_type"),
            scope=_optional_choice(scope, MEMORY_SCOPES, "scope"),
            status=_optional_choice(status_filter, MEMORY_STATUSES, "status"),
            keyword=keyword,
            offset=offset,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return success(data=result)


@router.post("/items", status_code=status.HTTP_201_CREATED)
async def create_memory_item(
    body: UserMemoryItemCreate,
    request: Request,
    _limit=Depends(memory_write_limit),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = _service(db)
    result = await svc.create_memory(
        current_user.id,
        body.model_dump(),
        MemoryActor(actor_type="USER", actor_id=current_user.id),
    )
    await AuditService(db).log_event(
        action="memory.item.create",
        actor_user_id=current_user.id,
        actor_role=current_user.role,
        resource_type="memory_item",
        resource_id=str(result.get("id")),
        after_json={
            "memory_type": result.get("memory_type"),
            "scope": result.get("scope"),
            "key": result.get("key"),
            "content_length": len(result.get("content") or ""),
        },
        **audit_request_metadata(request),
    )
    return success(data=result, message="记忆已创建")


@router.patch("/items/{memory_id}")
async def update_memory_item(
    memory_id: int,
    body: UserMemoryItemUpdate,
    request: Request,
    _limit=Depends(memory_write_limit),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = _service(db)
    try:
        result = await svc.update_memory(
            current_user.id,
            memory_id,
            body.model_dump(exclude_unset=True),
            MemoryActor(actor_type="USER", actor_id=current_user.id),
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await AuditService(db).log_event(
        action="memory.item.update",
        actor_user_id=current_user.id,
        actor_role=current_user.role,
        resource_type="memory_item",
        resource_id=str(memory_id),
        after_json={
            "memory_type": result.get("memory_type"),
            "scope": result.get("scope"),
            "key": result.get("key"),
            "status": result.get("status"),
        },
        **audit_request_metadata(request),
    )
    return success(data=result, message="记忆已更新")


@router.delete("/items/{memory_id}")
async def delete_memory_item(
    memory_id: int,
    request: Request,
    _limit=Depends(memory_write_limit),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = _service(db)
    try:
        await svc.delete_memory(
            current_user.id,
            memory_id,
            MemoryActor(actor_type="USER", actor_id=current_user.id),
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await AuditService(db).log_event(
        action="memory.item.delete",
        actor_user_id=current_user.id,
        actor_role=current_user.role,
        resource_type="memory_item",
        resource_id=str(memory_id),
        after_json={"status": "DELETED"},
        **audit_request_metadata(request),
    )
    return success(message="记忆已删除")


@router.post("/search")
async def search_memory_items(
    body: MemorySearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = _service(db)
    result = await svc.search_memories(
        current_user.id,
        query=body.query,
        memory_types=body.memory_types,
        limit=body.limit,
    )
    return success(data=result)


@router.get("/skills")
async def list_skill_profiles(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = _service(db)
    result = await svc.list_skill_profiles(current_user.id)
    return success(data=result)


@router.get("/events")
async def list_memory_events(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = _service(db)
    result = await svc.list_memory_events(
        current_user.id,
        offset=offset,
        limit=limit,
    )
    return success(data=result)


def _optional_choice(
    value: str | None,
    allowed: set[str],
    field_name: str,
) -> str | None:
    if value is None:
        return None
    return validate_choice(value, allowed, field_name)
