"""Existing REST/SSE surface, M4 core; legacy sessions are explicitly read-only."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from interview_api.api.deps import get_current_user
from interview_api.core.exceptions import AppError
from interview_api.core.errors import ResourceLockedError
from interview_api.core.locks import redis_lock
from interview_api.core.rate_limit import interview_chat_limit, memory_write_limit
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db, async_session_factory
from interview_api.modules.interview.schemas import CreateSessionRequest, SendMessageRequest, BindResumeRequest, SetTargetPositionRequest, ConsolidateMemoryRequest
from interview_api.modules.interview.models import InterviewSession, InterviewSessionQuestion
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion
from interview_api.modules.memory.interview_writer import InterviewMemoryWriter
from interview_api.modules.audit.service import AuditService, audit_request_metadata

router = APIRouter(prefix="/api/v1/interview", tags=["interview"])


def build_core():
    return MockInterviewService(async_session_factory)


@router.get("/targets")
async def targets(current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Company, Job).join(Job, Job.company_id == Company.id)
        .where(Company.status == "ACTIVE", Job.status == "ACTIVE",
               Job.id.in_(select(JobDescriptionVersion.job_id).where(JobDescriptionVersion.publication_status == "PUBLISHED")))
        .order_by(Company.canonical_name, Job.canonical_title))).all()
    return success(data=[dict(company_id=c.id, company_name=c.canonical_name, job_id=j.id, job_title=j.canonical_title) for c, j in rows])


@router.post("/sessions", status_code=201)
async def create_session(body: CreateSessionRequest, current_user=Depends(get_current_user)):
    return success(data=await build_core().create(current_user.id, body))


@router.get("/sessions")
async def list_sessions(current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    sessions = (await db.scalars(select(InterviewSession).where(InterviewSession.user_id == current_user.id)
        .order_by(InterviewSession.updated_at.desc()))).all()
    return success(data=[{**{c.name: getattr(s, c.name) for c in InterviewSession.__table__.columns},
                         "legacy": s.workflow_mode != "m4"} for s in sessions])


@router.get("/sessions/{session_id}")
async def get_session(session_id: int, current_user=Depends(get_current_user)):
    return success(data=await build_core().detail(session_id, current_user.id))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    session = await db.scalar(select(InterviewSession).where(InterviewSession.id == session_id, InterviewSession.user_id == current_user.id))
    if not session:
        raise HTTPException(404, "会话不存在")
    # Report provenance must survive; the 0023 FK also protects racing deletes.
    from interview_api.modules.interview_report.models import FinalInterviewReport
    if await db.scalar(select(FinalInterviewReport.id).where(FinalInterviewReport.session_id == session.id).limit(1)):
        raise AppError("INTERVIEW_REPORT_REFERENCED", "该面试已有报告生成历史，不能删除其来源；请保留历史记录", 409)
    try:
        await db.execute(delete(InterviewSession).where(InterviewSession.id == session.id))
        await db.commit()
    except IntegrityError as exc:
        # A report may commit after the read check. FK protection wins the race.
        await db.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23503":
            raise AppError("INTERVIEW_REPORT_REFERENCED", "面试来源已被引用，不能删除历史记录", 409) from exc
        raise
    return success(message="Session deleted")


@router.post("/sessions/{session_id}/start")
async def start_interview(session_id: int, current_user=Depends(get_current_user)):
    return success(data=await build_core().start(session_id, current_user.id))


@router.post("/sessions/{session_id}/messages/stream")
async def send_message_stream(session_id: int, body: SendMessageRequest, _limit=Depends(interview_chat_limit), current_user=Depends(get_current_user)):
    return StreamingResponse(build_core().stream(session_id, current_user.id, body), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@router.get("/sessions/{session_id}/questions")
async def list_questions(session_id: int, current_user=Depends(get_current_user)):
    detail = await build_core().detail(session_id, current_user.id)
    return success(data=dict(questions=[dict(q, id=q["question_id"]) for q in detail["questions"]],
        total=detail["total_questions"], current_question_index=detail["current_question_index"],
        active_question_id=detail["active_question_id"], question_generation_status=detail["question_generation_status"]))


@router.get("/sessions/{session_id}/questions/current")
async def get_current_question(session_id: int, current_user=Depends(get_current_user)):
    return success(data=(await build_core().detail(session_id, current_user.id))["current_question"])


@router.get("/sessions/{session_id}/questions/{question_id}")
async def get_question_detail(session_id: int, question_id: int, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    session = await db.scalar(select(InterviewSession).where(InterviewSession.id == session_id, InterviewSession.user_id == current_user.id))
    q = await db.scalar(select(InterviewSessionQuestion).where(InterviewSessionQuestion.session_id == session_id, InterviewSessionQuestion.id == question_id))
    if not session or not q:
        raise HTTPException(404, "题目不存在")
    if q.status == "PENDING":
        raise HTTPException(409, "该题尚未提问")
    return success(data=dict(question_id=q.id, question=q.question, standard_answer=q.standard_answer,
                             source=q.source, evidence=q.candidate_refs_json or q.evidence_json))


@router.post("/sessions/{session_id}/questions/{question_id}/skip")
async def skip_question(session_id: int, question_id: int, current_user=Depends(get_current_user)):
    return success(data=await build_core().skip(session_id, current_user.id, question_id))


async def frozen_mutation(session_id, user_id, db):
    session = await db.scalar(select(InterviewSession).where(InterviewSession.id == session_id, InterviewSession.user_id == user_id))
    if not session:
        raise HTTPException(404, "会话不存在")
    raise AppError("LEGACY_READ_ONLY" if session.workflow_mode == "legacy" else "CONTEXT_PINNED",
                   "历史会话只读；M4 在创建时固定画像和岗位，请新建面试", 409)


@router.post("/sessions/{session_id}/target-position")
async def set_target_position(session_id: int, body: SetTargetPositionRequest, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await frozen_mutation(session_id, current_user.id, db)


@router.post("/sessions/{session_id}/resume")
async def bind_resume(session_id: int, body: BindResumeRequest, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await frozen_mutation(session_id, current_user.id, db)


@router.post("/sessions/{session_id}/questions/generate")
async def generate_questions(session_id: int, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await frozen_mutation(session_id, current_user.id, db)


# Explicit user-authorized Memory endpoint is preserved; never called by M4.
@router.post("/sessions/{session_id}/memory/consolidate")
async def consolidate_interview_memory(session_id: int, body: ConsolidateMemoryRequest, request: Request,
        _limit=Depends(memory_write_limit), current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    writer = InterviewMemoryWriter(db)
    try:
        async with redis_lock(f"interview:{session_id}:memory_consolidate", 60):
            result = await writer.consolidate_interview_session(user_id=current_user.id, session_id=session_id, force=body.force)
    except (ResourceLockedError, LookupError) as exc:
        await AuditService(db).log_event(action="memory.interview.consolidate", actor_user_id=current_user.id,
            actor_role=current_user.role, resource_type="interview_session", resource_id=str(session_id), status="FAILED",
            error_message=str(exc), **audit_request_metadata(request))
        if isinstance(exc, LookupError):
            raise HTTPException(404, "会话不存在") from exc
        raise
    await AuditService(db).log_event(action="memory.interview.consolidate", actor_user_id=current_user.id,
        actor_role=current_user.role, resource_type="interview_session", resource_id=str(session_id),
        after_json={key: result.get(key) for key in ("episodic_memory_created", "episodic_memory_updated", "preferences_created", "skills_updated")},
        **audit_request_metadata(request))
    return success(data=result)
