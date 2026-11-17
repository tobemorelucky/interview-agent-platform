from typing import Literal
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from interview_api.api.deps import get_current_user
from interview_api.core.rate_limit import normal_user_api_limit
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.infrastructure.models.governance_models import ModelAdmission
from .library import PublicCareerLibrary

router = APIRouter(prefix="/api/v1/public-career", tags=["public-career"], dependencies=[Depends(normal_user_api_limit)])


@router.get("/jobs")
async def jobs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db=Depends(get_db)):
    return success(data=await PublicCareerLibrary(db).jobs(limit, offset))


@router.get("/job-search")
async def job_search(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                     q: str | None = Query(None, max_length=100),
                     company_id: int | None = Query(None),
                     direction: str | None = Query(None, max_length=40),
                     db=Depends(get_db)):
    """Search the whole published library, not just the current page."""
    return success(data=await PublicCareerLibrary(db).search(limit, offset, q, company_id, direction))


@router.get("/companies")
async def companies(db=Depends(get_db)):
    return success(data=await PublicCareerLibrary(db).companies())


@router.get("/jobs/{job_id}")
async def job(job_id: int, db=Depends(get_db)):
    return success(data=await PublicCareerLibrary(db).job(job_id))


@router.get("/experiences")
async def experiences(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db=Depends(get_db)):
    return success(data=await PublicCareerLibrary(db).experiences(limit, offset))


@router.get("/experiences/{experience_id}")
async def experience(experience_id: int, db=Depends(get_db)):
    return success(data=await PublicCareerLibrary(db).experiences(experience_id=experience_id))


class SourceRequest(BaseModel):
    source_url: str = Field(min_length=8, max_length=2000)
    document_type: Literal["job_jd", "interview_experience"]


@router.post("/source-requests")
async def request_source(body: SourceRequest, db=Depends(get_db)):
    return success(data=await PublicCareerLibrary(db).request_source(body.source_url, body.document_type))


@router.get("/my-model-usage")
async def usage(user=Depends(get_current_user), db=Depends(get_db)):
    from interview_api.core.config import settings
    now = await db.scalar(select(func.now()))
    calls, tokens = (await db.execute(select(func.count(), func.coalesce(func.sum(ModelAdmission.charged_tokens), 0)).where(
        ModelAdmission.owner_key == f"user:{user.id}", ModelAdmission.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0)))).one()
    return success(data=dict(calls=calls, charged_or_reserved_tokens=tokens,
        daily_calls=settings.model_user_daily_calls, daily_tokens=settings.model_user_daily_tokens,
        concurrency=settings.model_user_concurrency, accounting="application_quota_not_provider_billing"))
