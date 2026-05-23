import logging

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.api.deps import get_current_user
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.modules.resume.repository import ResumeRepository
from interview_api.modules.resume.service import ResumeService
from interview_api.modules.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/resumes", tags=["resumes"])


@router.post("/upload", status_code=201)
async def upload_resume(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a resume file (PDF/DOCX/TXT) and dispatch processing."""
    service = ResumeService(db)

    file_bytes = await file.read()
    filename = file.filename or "untitled"

    resume_data = await service.upload(
        user_id=current_user.id,
        filename=filename,
        file_bytes=file_bytes,
    )

    await db.commit()

    resume_id = resume_data["id"]
    repo = ResumeRepository(db)

    try:
        from interview_api.infrastructure.tasks.celery_client import (
            dispatch_process_resume,
        )

        task_id = dispatch_process_resume(resume_id)
        await repo.update_task_id(resume_id, task_id)
        resume_data["task_id"] = task_id
    except Exception as e:
        logger.exception("Failed to dispatch resume task for resume %s", resume_id)
        await repo.mark_processing_finished(
            resume_id,
            "FAILED",
            error_message=f"Celery dispatch failed: {e}",
        )
        await repo.update_processing_stage(
            resume_id, "FAILED", "Resume analysis dispatch failed. Please retry later."
        )
        await db.commit()
        resume_data["status"] = "FAILED"
        resume_data["error_message"] = f"Celery dispatch failed: {e}"
        resume_data["processing_stage"] = "FAILED"
        resume_data["stage_message"] = "Resume analysis dispatch failed. Please retry later."
        return success(data=resume_data)

    await repo.update_processing_stage(
        resume_id, "QUEUED", "Resume analysis task queued."
    )
    await db.commit()
    resume_data["processing_stage"] = "QUEUED"
    resume_data["stage_message"] = "Resume analysis task queued."

    logger.info(
        "Upload resume_id=%s filename=%s -> Celery task_id=%s",
        resume_id,
        filename,
        resume_data["task_id"],
    )

    return success(data=resume_data)


@router.get("")
async def list_resumes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List current user's resumes, newest first."""
    service = ResumeService(db)
    data = await service.list_resumes(current_user.id, page=page, page_size=page_size)
    return success(data=data)


@router.get("/{resume_id}")
async def get_resume(
    resume_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single resume detail (own resume only)."""
    service = ResumeService(db)
    data = await service.get_resume(resume_id, current_user.id)
    return success(data=data)


@router.get("/{resume_id}/report")
async def get_resume_report(
    resume_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the full report for a completed resume (own resume only)."""
    service = ResumeService(db)
    data = await service.get_report(resume_id, current_user.id)
    return success(data=data)


@router.delete("/{resume_id}")
async def delete_resume(
    resume_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a resume and its report (own resume only)."""
    service = ResumeService(db)
    await service.delete_resume(resume_id, current_user.id)
    await db.commit()
    return success(message="Resume deleted")
