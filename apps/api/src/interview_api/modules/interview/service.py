"""Interview service: session CRUD, resume binding, chat streaming with memory."""

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.config import settings
from interview_api.modules.interview.models import InterviewSession, InterviewMessage
from interview_api.modules.interview.repository import (
    InterviewSessionRepository,
    InterviewMessageRepository,
)
from interview_api.modules.interview.memory import InterviewMemoryManager
from interview_api.modules.interview.prompt_builder import InterviewPromptBuilder
from interview_api.modules.resume.repository import ResumeRepository, ResumeReportRepository

logger = logging.getLogger(__name__)


class InterviewService:
    def __init__(self, db: AsyncSession, embedding, vector_store, llm):
        self.db = db
        self.embedding = embedding
        self.vector_store = vector_store
        self.llm = llm
        self.session_repo = InterviewSessionRepository(db)
        self.msg_repo = InterviewMessageRepository(db)
        self.resume_repo = ResumeRepository(db)
        self.report_repo = ResumeReportRepository(db)
        self.memory_manager = InterviewMemoryManager()
        self.prompt_builder = InterviewPromptBuilder()

    # ── Session CRUD ──

    async def create_session(
        self, user_id: int, title: str | None = None
    ) -> InterviewSession:
        session = await self.session_repo.create(user_id=user_id, title=title)
        await self.db.commit()
        return session

    async def list_sessions(self, user_id: int) -> list[dict]:
        sessions = await self.session_repo.list_by_user(user_id)
        result = []
        for s in sessions:
            item = self._session_to_dict(s)
            # Attach resume filename and status if resume is bound
            if s.resume_id:
                resume = await self.resume_repo.get_by_id(s.resume_id)
                if resume:
                    item["resume_filename"] = resume.filename
                    item["resume_status"] = resume.status
            result.append(item)
        return result

    async def get_session(
        self, session_id: int, user_id: int
    ) -> dict | None:
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            return None
        item = self._session_to_dict(session)
        if session.resume_id:
            resume = await self.resume_repo.get_by_id(session.resume_id)
            if resume:
                item["resume_filename"] = resume.filename
                item["resume_status"] = resume.status
        messages = await self.msg_repo.get_by_session_id(session_id)
        item["messages"] = [self._msg_to_dict(m) for m in messages]
        return item

    async def delete_session(self, session_id: int, user_id: int) -> bool:
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            return False
        await self.session_repo.delete(session_id)
        await self.db.commit()
        return True

    # ── Resume Binding ──

    async def bind_resume(
        self, session_id: int, user_id: int, resume_id: int
    ) -> None:
        """Bind a processed resume to an interview session.

        Raises ValueError with a user-facing message on failure.
        """
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            raise ValueError("会话不存在")

        if session.resume_id is not None:
            raise ValueError("该会话已绑定简历，每个会话仅限绑定一份简历")

        resume = await self.resume_repo.get_by_id(resume_id)
        if resume is None or resume.user_id != user_id:
            raise ValueError("简历不存在")

        if resume.status != "COMPLETED":
            raise ValueError(
                f"简历尚未处理完成（当前状态: {resume.status}），请等待处理完成后再绑定"
            )

        await self.session_repo.bind_resume(session_id, resume_id)

        # Auto-generate welcome message from assistant
        welcome = (
            "我已读取你的简历，可以开始模拟面试。\n\n"
            "你可以输入「开始面试」让我根据你的简历进行针对性提问，"
            "也可以先聊聊你感兴趣的方向或想重点准备的内容。"
        )
        await self.msg_repo.create(
            session_id=session_id,
            role="ASSISTANT",
            content=welcome,
            metadata_json={"source": "LLM_GENERATED"},
            turn_index=0,
        )

        if session.title is None:
            await self.session_repo.update_title(
                session_id, f"面试练习 - {resume.filename}"
            )

        await self.db.commit()

    # ── Chat Streaming ──

    async def chat_stream(
        self,
        session_id: int,
        user_id: int,
        content: str,
    ):
        """Yields SSE events for the interview chat flow."""
        # Validate session
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            yield self._sse("error", {"code": "SESSION_NOT_FOUND", "message": "会话不存在"})
            return

        # Check resume binding
        if session.resume_id is None:
            yield self._sse("error", {"code": "NO_RESUME", "message": "请先上传并绑定一份简历"})
            return

        # Verify resume still exists and is complete
        resume = await self.resume_repo.get_by_id(session.resume_id)
        if resume is None or resume.status != "COMPLETED":
            yield self._sse(
                "error",
                {"code": "RESUME_NOT_READY", "message": "简历已被删除或未处理完成，请重新上传"},
            )
            return

        # Get resume structured data
        report = await self.report_repo.get_by_resume_id(session.resume_id)
        structured = report.summary_json if report else None
        raw_text = resume.raw_text or ""

        # Save user message
        new_turn = session.turn_count + 1
        user_msg = await self.msg_repo.create(
            session_id=session_id,
            role="USER",
            content=content,
            turn_index=new_turn,
        )
        await self.db.flush()

        try:
            # ── KB Retrieval ──
            yield self._sse("status", {"stage": "retrieving_kb"})

            recent_msgs = await self.msg_repo.get_recent_messages(
                session_id, limit=20
            )
            retrieval_query = self.prompt_builder.build_retrieval_query(
                resume_structured=structured,
                current_message=content,
                recent_messages=recent_msgs,
            )

            retrieved_chunks: list[dict] = []
            try:
                query_vec = await self.embedding.embed_query(retrieval_query)
                retrieved_chunks = self.vector_store.search(
                    "kb_chunks_current",
                    query_vec,
                    top_k=settings.interview_retrieval_top_k,
                )
                retrieved_chunks = [
                    c for c in retrieved_chunks
                    if c.get("score", 0) >= settings.interview_retrieval_min_score
                ]
            except Exception:
                logger.exception("KB retrieval failed, continuing without KB context")

            source = self.prompt_builder.extract_source_label(retrieved_chunks)

            yield self._sse(
                "retrieval",
                {"hit_count": len(retrieved_chunks), "source": source},
            )

            # Build citations (preview only)
            citations = [
                {
                    "chunk_id": c.get("chunk_id"),
                    "doc_id": c.get("doc_id"),
                    "title": c.get("title", ""),
                    "source_type": c.get("source_type", ""),
                    "preview": (c.get("content", "") or "")[:200],
                    "score": c.get("score"),
                }
                for c in retrieved_chunks[:8]
            ]
            yield self._sse("citation", citations)

            # ── Build prompt ──
            yield self._sse("status", {"stage": "generating"})

            # Get messages for context (last 10 turns = ~20 messages)
            context_msgs = await self.msg_repo.get_recent_messages(
                session_id, limit=20
            )

            system_prompt = self.prompt_builder.build_system_prompt(
                resume_raw_text=raw_text,
                resume_structured=structured,
                memory_summary=session.memory_summary,
                recent_messages=context_msgs,
                retrieved_context=retrieved_chunks,
            )

            # ── Stream LLM response ──
            llm_messages = [{"role": "user", "content": system_prompt}]
            full_content = ""

            async for token in self.llm.chat_stream(llm_messages):
                full_content += token
                yield self._sse("token", {"content": token})

            # ── Save assistant message ──
            assistant_msg = await self.msg_repo.create(
                session_id=session_id,
                role="ASSISTANT",
                content=full_content,
                metadata_json={
                    "retrieval_queries": [retrieval_query],
                    "retrieved_context": [
                        {
                            "chunk_id": c.get("chunk_id"),
                            "title": c.get("title", ""),
                            "preview": (c.get("content", "") or "")[:200],
                            "score": c.get("score"),
                            "source_type": c.get("source_type", ""),
                        }
                        for c in retrieved_chunks[:5]
                    ],
                    "source": source,
                    "evidence": citations[:5],
                    "compressed": False,
                },
                turn_index=new_turn,
            )

            # Update session turn count
            await self.session_repo.increment_turn(session_id)

            # ── Memory compression check ──
            compressed = False
            if self.memory_manager.should_compress(
                new_turn, session.last_compressed_turn
            ):
                try:
                    all_msgs = await self.msg_repo.get_by_session_id(session_id)
                    to_compress, _ = self.memory_manager.partition_messages(
                        all_msgs, session.last_compressed_turn, new_turn
                    )
                    if to_compress:
                        new_summary = await self.memory_manager.compress(
                            self.llm,
                            to_compress,
                            session.memory_summary,
                        )
                        new_last = new_turn - self.memory_manager.recent_keep_count
                        await self.session_repo.update_memory_summary(
                            session_id, new_summary, max(new_last, 0)
                        )
                        compressed = True
                        yield self._sse(
                            "compressed",
                            {
                                "compressed_turns": len(to_compress),
                                "new_last_compressed_turn": max(new_last, 0),
                            },
                        )
                except Exception:
                    logger.exception("Memory compression failed, continuing")

            await self.db.commit()

            yield self._sse(
                "done",
                {
                    "message_id": assistant_msg.id,
                    "turn_index": new_turn,
                    "source": source,
                    "compressed": compressed,
                },
            )

        except Exception as e:
            await self.db.rollback()
            logger.exception("Interview chat stream error")
            yield self._sse("error", {"code": "INTERVIEW_ERROR", "message": str(e)})

    # ── Helpers ──

    @staticmethod
    def _sse(event: str, data: dict | list) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    @staticmethod
    def _session_to_dict(session: InterviewSession) -> dict:
        return {
            "id": session.id,
            "user_id": session.user_id,
            "resume_id": session.resume_id,
            "title": session.title,
            "status": session.status,
            "memory_summary": session.memory_summary,
            "turn_count": session.turn_count,
            "last_compressed_turn": session.last_compressed_turn,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    @staticmethod
    def _msg_to_dict(msg: InterviewMessage) -> dict:
        return {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "metadata_json": msg.metadata_json,
            "turn_index": msg.turn_index,
            "created_at": msg.created_at,
        }
