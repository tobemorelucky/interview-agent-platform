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
    BindResumeRequest,
    CreateSessionRequest,
    SendMessageRequest,
    SetTargetPositionRequest,
    InterviewSessionResponse,
    InterviewSessionDetailResponse,
)
from interview_api.modules.interview.service import InterviewService, InterviewChatService
from interview_api.modules.interview.repository import (
    InterviewSessionRepository,
    InterviewSessionQuestionRepository,
    InterviewMessageRepository,
)
from interview_api.modules.interview.memory import InterviewMemoryManager
from interview_api.modules.interview.prompt_builder import InterviewPromptBuilder
from interview_api.modules.resume.repository import ResumeRepository, ResumeReportRepository

router = APIRouter(prefix="/api/v1/interview", tags=["interview"])


def _build_service(db: AsyncSession) -> InterviewService:
    embedding = OpenAICompatibleEmbeddingProvider()
    llm = OpenAICompatibleLLMProvider()
    vector_store = MilvusVectorStoreProvider(embedding_dim=settings.embedding_dim)
    return InterviewService(db, embedding, vector_store, llm)


def _build_chat_service(db: AsyncSession) -> InterviewChatService:
    embedding = OpenAICompatibleEmbeddingProvider()
    llm = OpenAICompatibleLLMProvider()
    vector_store = MilvusVectorStoreProvider(embedding_dim=settings.embedding_dim)
    return InterviewChatService(
        db=db,
        llm=llm,
        embedding=embedding,
        vector_store=vector_store,
        session_repo=InterviewSessionRepository(db),
        msg_repo=InterviewMessageRepository(db),
        question_repo=InterviewSessionQuestionRepository(db),
        report_repo=ResumeReportRepository(db),
        resume_repo=ResumeRepository(db),
        prompt_builder=InterviewPromptBuilder(),
        memory_manager=InterviewMemoryManager(),
    )


# ── Session CRUD ──


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


# ── Target Position ──


@router.post("/sessions/{session_id}/target-position")
async def set_target_position(
    session_id: int,
    body: SetTargetPositionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set target position and trigger question generation."""
    service = _build_service(db)
    try:
        await service.set_target_position(
            session_id=session_id,
            user_id=current_user.id,
            target_position=body.target_position,
            interview_mode=body.interview_mode,
            question_count=body.question_count,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    # After confirming position, trigger question generation in background
    from interview_api.modules.interview.service import _generate_questions_background
    from interview_api.modules.interview.repository import InterviewSessionRepository
    import asyncio

    session_repo = InterviewSessionRepository(db)
    await session_repo.update_question_generation_status(session_id, "GENERATING")
    await db.commit()

    asyncio.create_task(
        _generate_questions_background(
            session_id=session_id,
            llm=OpenAICompatibleLLMProvider(),
            embedding=OpenAICompatibleEmbeddingProvider(),
            vector_store=MilvusVectorStoreProvider(embedding_dim=settings.embedding_dim),
            prompt_builder=InterviewPromptBuilder(),
            memory_manager=InterviewMemoryManager(),
        )
    )
    return success(data={
        "status": "ok",
        "message": "岗位已确认，题目生成已触发",
        "question_generation_status": "GENERATING",
    })


# ── Question-Driven Chat ──


@router.post("/sessions/{session_id}/messages/stream")
async def send_message_stream(
    session_id: int,
    body: SendMessageRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Question-driven interview chat with SSE streaming."""
    service = _build_chat_service(db)
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


# ── Question Management ──


@router.post("/sessions/{session_id}/start")
async def start_interview(
    session_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start the interview: return first question."""
    service = _build_chat_service(db)
    result = await service.start_interview(session_id, user_id=current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if "error" in result:
        sub_code = result.get("sub_code", "")
        if sub_code == "GENERATING":
            raise HTTPException(
                status_code=status.HTTP_202_ACCEPTED,
                detail=result["message"],
            )
        raise HTTPException(status_code=400, detail=result["message"])
    return success(data=result)


@router.post("/sessions/{session_id}/questions/generate")
async def generate_questions(
    session_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger question generation (runs in background, returns immediately)."""
    from interview_api.modules.interview.service import _generate_questions_background
    from interview_api.modules.interview.repository import (
        InterviewSessionRepository,
    )
    from interview_api.infrastructure.embedding.provider import OpenAICompatibleEmbeddingProvider
    from interview_api.infrastructure.llm.provider import OpenAICompatibleLLMProvider
    from interview_api.infrastructure.milvus.provider import MilvusVectorStoreProvider
    from interview_api.modules.interview.memory import InterviewMemoryManager
    from interview_api.modules.interview.prompt_builder import InterviewPromptBuilder
    import asyncio

    session_repo = InterviewSessionRepository(db)
    session = await session_repo.get_by_id_and_user(session_id, current_user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.resume_id is None:
        raise HTTPException(status_code=400, detail="请先绑定简历")

    # Set GENERATING and commit
    await session_repo.update_question_generation_status(session_id, "GENERATING")
    await db.commit()

    # Fire background task with fresh session
    asyncio.create_task(
        _generate_questions_background(
            session_id=session_id,
            llm=OpenAICompatibleLLMProvider(),
            embedding=OpenAICompatibleEmbeddingProvider(),
            vector_store=MilvusVectorStoreProvider(embedding_dim=settings.embedding_dim),
            prompt_builder=InterviewPromptBuilder(),
            memory_manager=InterviewMemoryManager(),
        )
    )
    return success(data={
        "status": "ok",
        "message": "题目生成已触发",
        "question_generation_status": "GENERATING",
    })


@router.get("/sessions/{session_id}/questions")
async def list_questions(
    session_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get question list (answers masked based on status)."""
    service = _build_chat_service(db)
    result = await service.get_question_list(session_id, user_id=current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return success(data=result)


@router.get("/sessions/{session_id}/questions/current")
async def get_current_question(
    session_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current interview question."""
    service = _build_chat_service(db)
    result = await service.get_current_question(session_id, user_id=current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["message"])
    return success(data=result)


@router.get("/sessions/{session_id}/questions/{question_id}")
async def get_question_detail(
    session_id: int,
    question_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get question detail including standard_answer (only if ASKED/ANSWERED)."""
    service = _build_chat_service(db)
    result = await service.reveal_answer(session_id, current_user.id, question_id)
    if result is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["message"])
    return success(data=result)


@router.post("/sessions/{session_id}/questions/{question_id}/skip")
async def skip_question(
    session_id: int,
    question_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Skip current question and advance to next."""
    service = _build_chat_service(db)
    result = await service.skip_question(session_id, current_user.id, question_id)
    if result is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    return success(data=result)


# ── Resume Binding ──


@router.post("/sessions/{session_id}/resume")
async def bind_resume(
    session_id: int,
    body: BindResumeRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bind a resume to the interview session. Triggers async question generation."""
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