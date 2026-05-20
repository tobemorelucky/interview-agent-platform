from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.api.deps import get_current_user
from interview_api.core.config import settings
from interview_api.core.exceptions import NotFoundError
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.infrastructure.embedding.provider import (
    OpenAICompatibleEmbeddingProvider,
)
from interview_api.infrastructure.llm.provider import OpenAICompatibleLLMProvider
from interview_api.infrastructure.milvus.provider import MilvusVectorStoreProvider
from interview_api.modules.qa.models import ChatSession
from interview_api.modules.qa.schemas import (
    ChatMessageResponse,
    ChatStreamRequest,
    CreateSessionRequest,
    SessionDetailResponse,
    SessionResponse,
)
from interview_api.modules.qa.service import QaService
from interview_api.modules.users.models import User

router = APIRouter(prefix="/api/v1/qa", tags=["qa"])


def _build_qa_service(db: AsyncSession) -> QaService:
    embedding = OpenAICompatibleEmbeddingProvider()
    llm = OpenAICompatibleLLMProvider()
    vector_store = MilvusVectorStoreProvider(
        embedding_dim=settings.embedding_dim
    )
    return QaService(db, embedding, vector_store, llm)


@router.post("/sessions", status_code=201)
async def create_session(
    body: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = QaService(db, None, None, None)
    session = await service.create_session(
        user_id=current_user.id, title=body.title
    )
    return success(data=SessionResponse.model_validate(session).model_dump())


@router.get("/sessions")
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = QaService(db, None, None, None)
    sessions = await service.get_sessions(user_id=current_user.id)
    return success(
        data=[
            SessionResponse.model_validate(s).model_dump()
            for s in sessions
        ]
    )


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = QaService(db, None, None, None)
    session = await service.get_session(session_id, user_id=current_user.id)
    if session is None:
        raise NotFoundError(message="Session not found")

    messages = await service.get_messages(session_id)
    detail = SessionDetailResponse.model_validate(session)
    detail.messages = [
        ChatMessageResponse.model_validate(m).model_dump() for m in messages
    ]
    return success(data=detail.model_dump())


@router.post("/chat/stream")
async def chat_stream(
    body: ChatStreamRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _build_qa_service(db)

    async def event_generator():
        async for event in service.chat_stream(
            session_id=body.session_id,
            user_id=current_user.id,
            message=body.message,
        ):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
