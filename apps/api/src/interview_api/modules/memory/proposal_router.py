"""Owner-only, explicit decisions for interview Memory proposals."""
from fastapi import APIRouter, Depends, Query, Request

from interview_api.api.deps import get_current_user
from interview_api.core.rate_limit import memory_write_limit
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.modules.audit.service import audit_request_metadata
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.audit.repository import AuditLogRepository
from interview_api.modules.memory.proposal_schemas import ProposalDecisionRequest, ProposalCorrectionRequest
from interview_api.modules.memory.proposal_service import MemoryProposalService


router = APIRouter(prefix="/api/v1/memory-proposals", tags=["memory-proposals"])


@router.get("")
async def proposals(report_id: int | None = Query(None), limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0), current_user=Depends(get_current_user), db=Depends(get_db)):
    return success(data=await MemoryProposalService(db).list(current_user.id, report_id, limit=limit, offset=offset))


async def _decision(db, current_user, proposal_id, decision, body, request):
    result = await MemoryProposalService(db).decide(current_user.id, proposal_id, decision,
        content=getattr(body, "content", None), reason=body.reason)
    await AuditLogRepository(db).create(AuditLog(action=f"memory.proposal.{decision.lower()}", actor_user_id=current_user.id,
        actor_role=current_user.role, resource_type="memory_proposal", resource_id=str(proposal_id),
        after_json=dict(status=result["status"], memory_item_id=result["memory_item_id"], report_id=result["report_id"]),
        **audit_request_metadata(request)))
    await db.commit()
    return success(data=result)


@router.post("/{proposal_id}/approve")
async def approve(proposal_id: int, body: ProposalDecisionRequest, request: Request,
        _limit=Depends(memory_write_limit), current_user=Depends(get_current_user), db=Depends(get_db)):
    return await _decision(db, current_user, proposal_id, "APPROVED", body, request)


@router.post("/{proposal_id}/reject")
async def reject(proposal_id: int, body: ProposalDecisionRequest, request: Request,
        _limit=Depends(memory_write_limit), current_user=Depends(get_current_user), db=Depends(get_db)):
    return await _decision(db, current_user, proposal_id, "REJECTED", body, request)


@router.post("/{proposal_id}/correct")
async def correct(proposal_id: int, body: ProposalCorrectionRequest, request: Request,
        _limit=Depends(memory_write_limit), current_user=Depends(get_current_user), db=Depends(get_db)):
    return await _decision(db, current_user, proposal_id, "CORRECTED", body, request)
