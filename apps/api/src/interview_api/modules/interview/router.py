"""Interview API router."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.api.deps import get_current_user
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.infrastructure.embedding.provider import (
    OpenAICompatibleEmbeddingProvider,
)
from interview_api.infrastructure.llm.provider import OpenAICompatibleLLMProvider
from interview_api.infrastructure.milvus.provider import MilvusVectorStoreProvider
from interview_api.core.config import settings
from interview_api.modules.interview.schemas import (
    CreateSessionRequest,
    BindResumeRequest,
    SendMessageRequest,
    InterviewSessionResponse,
    InterviewSessionDetailResponse,
)
from interview_api.modules.interview.service import InterviewService

router = APIRouter(prefix="/api/v1/interview", tags=["interview"])


def _build_service(db: AsyncSession) -> InterviewService:
    embedding = OpenAICompatibleEmbeddingProvider()
    llm = OpenAICompatibleLLMProvider()
    vector_store = MilvusVectorStoreProvider(embedding_dim=settings.embedding_dim)
    return InterviewService(db, embedding, vector_store, llm)


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    body: CreateSessionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _build_service(db)
    session = await service.create_session(
        user_id=current_user.id,
        title=body.title,
    )
    return success(data=InterviewSessionResponse.model_validate(session).model_dump())


@router.get("/sessions")
async def list_sessions(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _build_service(db)
    sessions = await service.list_sessions(user_id=current_user.id)
    return success(data=sessions)


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _build_service(db)
    result = await service.get_session(session_id, user_id=current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return success(data=result)


@router.post("/sessions/{session_id}/resume")
async def bind_resume(
    session_id: int,
    body: BindResumeRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _build_service(db)
    try:
        await service.bind_resume(
            session_id=session_id,
            user_id=current_user.id,
            resume_id=body.resume_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return success(data={"status": "ok"})


@router.post("/sessions/{session_id}/messages/stream")
async def send_message_stream(
    session_id: int,
    body: SendMessageRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _build_service(db)
    return StreamingResponse(
        service.chat_stream(
            session_id=session_id,
            user_id=current_user.id,
            content=body.content,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _build_service(db)
    deleted = await service.delete_session(
        session_id=session_id,
        user_id=current_user.id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return success(message="Session deleted")
