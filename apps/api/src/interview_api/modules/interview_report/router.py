from fastapi import APIRouter, Depends, Query
from interview_api.api.deps import get_current_user
from interview_api.core.response import success
from interview_api.core.rate_limit import interview_chat_limit
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.interview_report.schemas import ReportGenerationRequest
from interview_api.modules.interview_report.service import InterviewReportService
from interview_api.infrastructure.models.governance import check_generation_result

router = APIRouter(prefix="/api/v1/interview-reports", tags=["interview-reports"])


def build_core():
    return InterviewReportService(async_session_factory)


@router.get("")
async def list_reports(user=Depends(get_current_user), limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return success(data=await build_core().list_reports(user.id, limit=limit, offset=offset))


@router.get("/sessions/{session_id}")
async def session_report(session_id: int, user=Depends(get_current_user)):
    return success(data=await build_core().session_detail(session_id, user.id))


@router.post("/sessions/{session_id}/generate")
async def generate_report(session_id: int, request: ReportGenerationRequest, _limit=Depends(interview_chat_limit), user=Depends(get_current_user)):
    return success(data=check_generation_result(await build_core().generate(session_id, user.id, regenerate=request.regenerate, request_key=request.request_key)))


@router.get("/{report_id}")
async def report_detail(report_id: int, user=Depends(get_current_user)):
    return success(data=await build_core().detail(report_id, user.id))
