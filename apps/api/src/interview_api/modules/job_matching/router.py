from fastapi import APIRouter, Depends, Query
from interview_api.api.deps import get_current_user
from interview_api.core.response import success
from interview_api.core.rate_limit import interview_chat_limit
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.job_matching.schemas import MatchRequest
from interview_api.modules.job_matching.service import JobMatchingService
from interview_api.infrastructure.models.governance import check_generation_result

router = APIRouter(prefix="/api/v1/job-matches", tags=["job-matches"])


def build_core():
    return JobMatchingService(async_session_factory)


@router.get("")
async def history(user=Depends(get_current_user), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    return success(data=await build_core().history(user.id, limit, offset))


@router.post("")
async def create(request: MatchRequest, user=Depends(get_current_user), _limit=Depends(interview_chat_limit)):
    return success(data=await build_core().create(user.id, request))


@router.get("/{run_id}")
async def detail(run_id: int, user=Depends(get_current_user)):
    return success(data=await build_core().detail(user.id, run_id))


@router.post("/{run_id}/generate")
async def generate(run_id: int, user=Depends(get_current_user), _limit=Depends(interview_chat_limit)):
    return success(data=check_generation_result(await build_core().generate(user.id, run_id)))
