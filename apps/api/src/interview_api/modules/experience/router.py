"""Phase 4: Experience admin API router."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.api.deps import require_admin
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.modules.experience.schemas import (
    ExperienceKeywordPresetCreate,
    ExperienceKeywordPresetUpdate,
    ExperienceCollectionTaskCreate,
)
from interview_api.modules.experience.service import ExperienceKeywordService, ExperienceTaskService

router = APIRouter(prefix="/api/v1/admin/experience", tags=["admin_experience"])


def _kw_service(db: AsyncSession) -> ExperienceKeywordService:
    return ExperienceKeywordService(db)


def _task_service(db: AsyncSession) -> ExperienceTaskService:
    return ExperienceTaskService(db)


# ── Keywords CRUD ──


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
    return success(data=result, message="关键词已创建")


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
    return success(data=result, message="关键词已更新")


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
        raise HTTPException(status_code=404, detail="关键词不存在")
    await db.commit()
    return success(message="关键词已删除")


# ── Collection Tasks ──


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
    admin_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    try:
        result = await svc.create_task(body.model_dump(), user_id=admin_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return success(data=result, message="任务已创建")


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: int,
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    result = await svc.get_task(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return success(data=result)


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: int,
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    try:
        deleted = await svc.delete_task(task_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在")
    return success(message="任务已删除")


@router.post("/tasks/{task_id}/search")
async def run_task_search(
    task_id: int,
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = _task_service(db)
    try:
        result = await svc.run_search(task_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return success(data=result, message="搜索执行完成")


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
