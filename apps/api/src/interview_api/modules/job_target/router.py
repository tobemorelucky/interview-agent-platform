"""Private JD target endpoints. Every call is owner-scoped server-side."""
from fastapi import APIRouter, Depends, Query

from interview_api.api.deps import get_current_user
from interview_api.core.rate_limit import interview_chat_limit
from interview_api.core.response import success
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.job_target.schemas import CreateJobTargetRequest, UpdateJobTargetRequest
from interview_api.modules.job_target.service import JobTargetService

router = APIRouter(prefix="/api/v1/job-targets", tags=["job-targets"])


@router.get("")
async def history(user=Depends(get_current_user), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    async with async_session_factory() as db:
        data = await JobTargetService(db).history(user.id, limit, offset)
    return success(data=data)


@router.post("", status_code=201)
async def create(request: CreateJobTargetRequest, user=Depends(get_current_user)):
    async with async_session_factory() as db, db.begin():
        data = await JobTargetService(db).create(user.id, request)
    return success(data=data)


@router.get("/{target_id}")
async def detail(target_id: int, user=Depends(get_current_user)):
    async with async_session_factory() as db:
        data = JobTargetService(db).serialize(await JobTargetService(db).read(user.id, target_id))
    return success(data=data)


@router.post("/{target_id}/parse")
async def parse(target_id: int, user=Depends(get_current_user), _limit=Depends(interview_chat_limit)):
    # Read, call the model without a transaction, then write the result.
    async with async_session_factory() as db:
        target = await JobTargetService(db).read(user.id, target_id)
        raw, company, title = target.raw_text, target.company_name, target.job_title
    extraction, mode, error = await JobTargetService(None).extract(raw, company, title)
    async with async_session_factory() as db, db.begin():
        data = await JobTargetService(db).apply_parse(user.id, target_id, extraction, mode, error)
    return success(data=data)


@router.patch("/{target_id}")
async def update(target_id: int, request: UpdateJobTargetRequest, user=Depends(get_current_user)):
    async with async_session_factory() as db, db.begin():
        data = await JobTargetService(db).update_requirements(user.id, target_id, request.requirements)
    return success(data=data)


@router.post("/{target_id}/confirm")
async def confirm(target_id: int, user=Depends(get_current_user)):
    async with async_session_factory() as db, db.begin():
        data = await JobTargetService(db).confirm(user.id, target_id)
    return success(data=data)


@router.get("/{target_id}/match")
async def match(target_id: int, user=Depends(get_current_user)):
    async with async_session_factory() as db:
        data = await JobTargetService(db).match_preview(user.id, target_id)
    return success(data=data)


@router.delete("/{target_id}")
async def delete(target_id: int, user=Depends(get_current_user)):
    async with async_session_factory() as db, db.begin():
        data = await JobTargetService(db).delete(user.id, target_id)
    return success(data=data)
