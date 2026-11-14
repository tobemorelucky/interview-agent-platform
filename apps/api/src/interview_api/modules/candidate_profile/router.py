from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.api.deps import get_current_user
from interview_api.core.exceptions import AppError
from interview_api.core.response import success
from interview_api.core.rate_limit import interview_chat_limit
from interview_api.infrastructure.db.session import get_db
from interview_api.modules.users.models import User
from interview_api.modules.resume.models import Resume, ResumeReport
from interview_api.modules.candidate_profile.models import CandidateProfile, CandidateProfileVersion
from interview_api.modules.candidate_profile.schemas import CorrectionRequest, GenerationRequest
from interview_api.modules.candidate_profile.service import CandidateProfileService

router = APIRouter(prefix="/api/v1/candidate-profile", tags=["candidate-profile"])


@router.get("")
async def current_profile(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    service = CandidateProfileService(db)
    return success(data={"current": await service.serialize(user.id), "generations": await service.generations(user.id)})


@router.get("/versions")
async def versions(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (await db.scalars(select(CandidateProfileVersion).join(CandidateProfile, CandidateProfile.id == CandidateProfileVersion.candidate_profile_id)
        .where(CandidateProfile.user_id == user.id).order_by(CandidateProfileVersion.version_number.desc()))).all()
    return success(data=[dict(id=row.id, version_number=row.version_number, producer=row.producer, created_at=row.created_at.isoformat()) for row in rows])


@router.get("/versions/{version_id}")
async def version(version_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    row = await db.scalar(select(CandidateProfileVersion).join(CandidateProfile, CandidateProfile.id == CandidateProfileVersion.candidate_profile_id)
        .where(CandidateProfile.user_id == user.id, CandidateProfileVersion.id == version_id))
    if not row:
        raise AppError("PROFILE_NOT_FOUND", "画像版本不存在", 404)
    return success(data=await CandidateProfileService(db).serialize(user.id, row))


@router.post("/corrections", status_code=201)
async def correct(request: CorrectionRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    data = await CandidateProfileService(db).correct(user.id, request)
    await db.commit()
    return success(data=data)


@router.post("/generate", status_code=202, dependencies=[Depends(interview_chat_limit)])
async def generate(request: GenerationRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    resume = await db.scalar(select(Resume).where(Resume.id == request.resume_id, Resume.user_id == user.id))
    if not resume:
        raise AppError("RESUME_NOT_FOUND", "简历不存在", 404)
    if resume.status != "COMPLETED" or not await db.scalar(select(ResumeReport.id).where(ResumeReport.resume_id == resume.id)):
        raise AppError("RESUME_REPORT_NOT_READY", "简历报告尚未完成", 409)
    if resume.archived_at is not None:
        raise AppError("RESUME_ARCHIVED", "该简历已归档，不能生成新的画像", 409)
    # Round 23 (L3): a purge request outranks the archive flag.
    from interview_api.modules.purge.fence import assert_resume_usable
    await assert_resume_usable(db, resume.id)
    token = uuid4().hex if request.regenerate else None
    from interview_api.infrastructure.tasks.celery_client import dispatch_generate_candidate_profile
    try:
        task_id = dispatch_generate_candidate_profile(resume.id, token)
    except Exception:
        raise AppError("PROFILE_DISPATCH_FAILED", "画像任务暂时无法入队，请重试", 503)
    return success(data={"task_id": task_id, "regeneration_token": token, "mode": "configured_model"})
