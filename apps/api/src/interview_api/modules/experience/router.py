"""Phase 4: Experience admin API router."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.api.deps import require_admin
from interview_api.core.errors import ResourceLockedError
from interview_api.core.locks import redis_lock
from interview_api.core.rate_limit import (
    experience_task_limit,
    fetch_run_limit,
    search_run_limit,
)
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.modules.audit.service import AuditService, audit_request_metadata
from interview_api.modules.experience.schemas import (
    ExperienceCollectionTaskCreate,
    ExperienceKeywordPresetCreate,
    ExperienceKeywordPresetUpdate,
    ExperienceSourceFetchRequest,
    ExperienceTaskFetchRequest,
)
from interview_api.modules.experience.service import (
    ExperienceKeywordService,
    ExperienceTaskService,
)

router = APIRouter(prefix="/api/v1/admin/experience", tags=["admin_experience"])


def _kw_service(db: AsyncSession) -> ExperienceKeywordService:
    return ExperienceKeywordService(db)


def _task_service(db: AsyncSession) -> ExperienceTaskService:
    return ExperienceTaskService(db)


@router.get("/keywords")
async def list_keywords(
    preset_type: str | None = Query(None, description="COMPANY / JOB / PLATFORM"),
    enabled: bool | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List keyword presets, optionally filtered by type/enabled."""
    svc = _kw_service(db)
    try:
        result = await svc.list_keywords(preset_type, enabled, offset, limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return success(data=result)


@router.post("/keywords", status_code=status.HTTP_201_CREATED)
async def create_keyword(
    body: ExperienceKeywordPresetCreate,
    admin_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new keyword preset. preset_type + name must be unique."""
    svc = _kw_service(db)
    try:
        result = await svc.create_keyword(body.model_dump(), user_id=admin_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    await db.commit()
    return success(data=result, message="Keyword created")


@router.patch("/keywords/{keyword_id}")
async def update_keyword(
    keyword_id: int,
    body: ExperienceKeywordPresetUpdate,
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update a keyword preset. Partial updates supported."""
    svc = _kw_service(db)
    try:
        result = await svc.update_keyword(
            keyword_id, body.model_dump(exclude_unset=True)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return success(data=result, message="Keyword updated")


@router.delete("/keywords/{keyword_id}")
async def delete_keyword(
    keyword_id: int,
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a keyword preset."""
    svc = _kw_service(db)
    try:
        deleted = await svc.delete_keyword(keyword_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="Keyword not found")
    await db.commit()
    return success(message="Keyword deleted")


@router.get("/tasks")
async def list_tasks(
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    result = await svc.list_tasks(status=status, offset=offset, limit=limit)
    return success(data=result)


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(
    body: ExperienceCollectionTaskCreate,
    request: Request,
    _limit=Depends(experience_task_limit),
    admin_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    try:
        result = await svc.create_task(body.model_dump(), user_id=admin_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    await AuditService(db).log_event(
        action="experience.task.create",
        actor_user_id=admin_user.id,
        actor_role=admin_user.role,
        resource_type="experience_task",
        resource_id=str(result["id"]),
        after_json={
            "search_scope": result.get("search_scope"),
            "time_window_hours": result.get("time_window_hours"),
            "job_keyword_count": len(result.get("job_keywords_json") or []),
            "company_keyword_count": len(result.get("company_keywords_json") or []),
            "platforms": result.get("platforms_json") or [],
        },
        **audit_request_metadata(request),
    )
    return success(data=result, message="Task created")


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: int,
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    result = await svc.get_task(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return success(data=result)


@router.get("/tasks/{task_id}/fetch-stats")
async def get_task_fetch_stats(
    task_id: int,
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    try:
        result = await svc.get_fetch_stats(task_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return success(data=result)


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: int,
    request: Request,
    admin_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    audit = AuditService(db)
    try:
        deleted = await svc.delete_task(task_id)
    except LookupError as e:
        await audit.log_event(
            action="experience.task.delete",
            actor_user_id=admin_user.id,
            actor_role=admin_user.role,
            resource_type="experience_task",
            resource_id=str(task_id),
            status="FAILED",
            error_message=str(e),
            **audit_request_metadata(request),
        )
        raise HTTPException(status_code=404, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    await audit.log_event(
        action="experience.task.delete",
        actor_user_id=admin_user.id,
        actor_role=admin_user.role,
        resource_type="experience_task",
        resource_id=str(task_id),
        **audit_request_metadata(request),
    )
    return success(message="Task deleted")


@router.post("/tasks/{task_id}/search")
async def run_task_search(
    task_id: int,
    request: Request,
    _limit=Depends(search_run_limit),
    admin_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    audit = AuditService(db)
    try:
        async with redis_lock(f"task:{task_id}:search", 120):
            result = await svc.run_search(task_id)
    except ResourceLockedError as e:
        await audit.log_event(
            action="experience.task.search",
            actor_user_id=admin_user.id,
            actor_role=admin_user.role,
            resource_type="experience_task",
            resource_id=str(task_id),
            status="FAILED",
            error_message=e.message,
            **audit_request_metadata(request),
        )
        raise
    except LookupError as e:
        await audit.log_event(
            action="experience.task.search",
            actor_user_id=admin_user.id,
            actor_role=admin_user.role,
            resource_type="experience_task",
            resource_id=str(task_id),
            status="FAILED",
            error_message=str(e),
            **audit_request_metadata(request),
        )
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        await audit.log_event(
            action="experience.task.search",
            actor_user_id=admin_user.id,
            actor_role=admin_user.role,
            resource_type="experience_task",
            resource_id=str(task_id),
            status="FAILED",
            error_message=str(e),
            **audit_request_metadata(request),
        )
        raise HTTPException(status_code=400, detail=str(e))
    await audit.log_event(
        action="experience.task.search",
        actor_user_id=admin_user.id,
        actor_role=admin_user.role,
        resource_type="experience_task",
        resource_id=str(task_id),
        after_json={
            "query_count": result.get("query_count"),
            "query_failed_count": result.get("query_failed_count"),
            "raw_result_count": result.get("raw_result_count"),
            "accepted_count": result.get("accepted_count"),
            "filtered_count": result.get("filtered_count"),
            "duplicate_count": result.get("duplicate_count"),
            "found_url_count": result.get("found_url_count"),
        },
        **audit_request_metadata(request),
    )
    return success(data=result, message="Search completed")


@router.post("/tasks/{task_id}/fetch")
async def fetch_task_sources(
    task_id: int,
    request: Request,
    body: ExperienceTaskFetchRequest | None = None,
    _limit=Depends(fetch_run_limit),
    admin_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    audit = AuditService(db)
    payload = body or ExperienceTaskFetchRequest()
    try:
        async with redis_lock(f"experience:task:{task_id}:fetch", 600):
            result = await svc.fetch_task_sources(
                task_id,
                retry_failed=payload.retry_failed,
                limit=payload.limit,
            )
    except ResourceLockedError as e:
        await audit.log_event(
            action="experience.task.fetch",
            actor_user_id=admin_user.id,
            actor_role=admin_user.role,
            resource_type="experience_task",
            resource_id=str(task_id),
            metadata_json={
                "task_id": task_id,
                "retry_failed": payload.retry_failed,
                "limit": payload.limit,
            },
            status="FAILED",
            error_message=e.message,
            **audit_request_metadata(request),
        )
        raise
    except LookupError as e:
        await audit.log_event(
            action="experience.task.fetch",
            actor_user_id=admin_user.id,
            actor_role=admin_user.role,
            resource_type="experience_task",
            resource_id=str(task_id),
            metadata_json={
                "task_id": task_id,
                "retry_failed": payload.retry_failed,
                "limit": payload.limit,
            },
            status="FAILED",
            error_message=str(e),
            **audit_request_metadata(request),
        )
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        await audit.log_event(
            action="experience.task.fetch",
            actor_user_id=admin_user.id,
            actor_role=admin_user.role,
            resource_type="experience_task",
            resource_id=str(task_id),
            metadata_json={
                "task_id": task_id,
                "retry_failed": payload.retry_failed,
                "limit": payload.limit,
            },
            status="FAILED",
            error_message=str(e),
            **audit_request_metadata(request),
        )
        raise HTTPException(status_code=400, detail=str(e))
    await audit.log_event(
        action="experience.task.fetch",
        actor_user_id=admin_user.id,
        actor_role=admin_user.role,
        resource_type="experience_task",
        resource_id=str(task_id),
        metadata_json={
            "task_id": task_id,
            "total": result.get("total"),
            "fetched_count": result.get("fetched_count"),
            "failed_count": result.get("failed_count"),
            "retry_failed": payload.retry_failed,
            "limit": payload.limit,
        },
        **audit_request_metadata(request),
    )
    return success(data=result, message="Fetch completed")


@router.get("/tasks/{task_id}/sources")
async def list_task_sources(
    task_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    fetch_status: str | None = Query(None),
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    try:
        result = await svc.list_source_items(
            task_id,
            offset=offset,
            limit=limit,
            fetch_status=fetch_status,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return success(data=result)


@router.get("/sources/{source_id}/preview")
async def preview_source_text(
    source_id: int,
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    try:
        result = await svc.get_source_preview(source_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return success(data=result)


@router.post("/sources/{source_id}/fetch")
async def fetch_single_source(
    source_id: int,
    request: Request,
    body: ExperienceSourceFetchRequest | None = None,
    _limit=Depends(fetch_run_limit),
    admin_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    audit = AuditService(db)
    payload = body or ExperienceSourceFetchRequest()
    try:
        async with redis_lock(f"experience:source:{source_id}:fetch", 600):
            result = await svc.fetch_source_item(source_id, force=payload.force)
    except ResourceLockedError as e:
        await audit.log_event(
            action="experience.source.fetch",
            actor_user_id=admin_user.id,
            actor_role=admin_user.role,
            resource_type="experience_source_item",
            resource_id=str(source_id),
            metadata_json={"source_id": source_id, "force": payload.force},
            status="FAILED",
            error_message=e.message,
            **audit_request_metadata(request),
        )
        raise
    except LookupError as e:
        await audit.log_event(
            action="experience.source.fetch",
            actor_user_id=admin_user.id,
            actor_role=admin_user.role,
            resource_type="experience_source_item",
            resource_id=str(source_id),
            metadata_json={"source_id": source_id, "force": payload.force},
            status="FAILED",
            error_message=str(e),
            **audit_request_metadata(request),
        )
        raise HTTPException(status_code=404, detail=str(e))

    await audit.log_event(
        action="experience.source.fetch",
        actor_user_id=admin_user.id,
        actor_role=admin_user.role,
        resource_type="experience_source_item",
        resource_id=str(source_id),
        metadata_json={
            "source_id": source_id,
            "task_id": result.get("task_id"),
            "force": payload.force,
            "skipped": result.get("skipped"),
            "fetch_status": result.get("fetch_status"),
            "raw_text_char_count": result.get("raw_text_char_count"),
            "error_message": result.get("error_message"),
        },
        **audit_request_metadata(request),
    )
    return success(data=result, message="Source fetch completed")
