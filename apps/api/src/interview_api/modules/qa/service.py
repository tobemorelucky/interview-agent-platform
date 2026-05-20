"""QA service: sessions, retrieval, and streaming answer generation."""

import json
import logging
from pathlib import Path

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.qa.models import ChatMessage, ChatSession

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE_PATH = (
    Path(__file__).parent.parent.parent.parent.parent
    / "prompt_templates"
    / "qa_knowledge_answer_v1.md"
)


class QaService:
    def __init__(
        self,
        db: AsyncSession,
        embedding,
        vector_store,
        llm,
        reranker=None,
    ):
        self.db = db
        self.embedding = embedding
        self.vector_store = vector_store
        self.llm = llm
        self.reranker = reranker

    # ---- Session management ----

    async def create_session(self, user_id: int, title: str | None = None) -> ChatSession:
        session = ChatSession(user_id=user_id, title=title)
        self.db.add(session)
        await self.db.flush()
        return session

    async def get_sessions(self, user_id: int) -> list[ChatSession]:
        result = await self.db.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(desc(ChatSession.updated_at))
        )
        return list(result.scalars().all())

    async def get_session(self, session_id: int, user_id: int) -> ChatSession | None:
        result = await self.db.execute(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_messages(self, session_id: int) -> list[ChatMessage]:
        result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        return list(result.scalars().all())

    # ---- Retrieval ----

    async def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """Search Milvus for relevant chunks."""
        query_vec = await self.embedding.embed_query(query)
        results = self.vector_store.search(
            "kb_chunks_current",
            query_vec,
            top_k=top_k,
        )
        return results

    # ---- Answer generation (streaming) ----

    def _build_context(self, chunks: list[dict]) -> str:
        lines = []
        for i, c in enumerate(chunks):
            title = c.get("title", "Unknown")
            content = c.get("content", "")
            lines.append(f"[{i + 1}] ({title}) {content}")
        return "\n\n".join(lines)

    def _load_prompt_template(self) -> str:
        return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

    def _build_prompt(self, question: str, context: str) -> str:
        template = self._load_prompt_template()
        return template.format(question=question, context=context)

    async def chat_stream(
        self,
        session_id: int,
        user_id: int,
        message: str,
    ):
        """Yields SSE event strings: citation, token, done, error."""
        # Validate session belongs to user
        session = await self.get_session(session_id, user_id)
        if session is None:
            yield 'event: error\ndata: {"code":"QA_SESSION_NOT_FOUND","message":"Session not found"}\n\n'
            return

        # Save user message
        user_msg = ChatMessage(
            session_id=session_id,
            role="user",
            content=message,
        )
        self.db.add(user_msg)
        await self.db.flush()

        try:
            # Retrieve relevant chunks
            chunks = await self.retrieve(message)

            # Build citations
            citations = [
                {
                    "chunk_id": c.get("chunk_id"),
                    "doc_id": c.get("doc_id"),
                    "title": c.get("title", ""),
                    "content": c.get("content", "")[:300],
                }
                for c in chunks
            ]

            # Send citations first
            yield f"event: citation\ndata: {json.dumps(citations, ensure_ascii=False)}\n\n"

            # Build prompt
            context = self._build_context(chunks)
            prompt = self._build_prompt(message, context)

            # Stream LLM response
            messages = [{"role": "user", "content": prompt}]
            full_content = ""

            async for token in self.llm.chat_stream(messages):
                full_content += token
                yield f"event: token\ndata: {json.dumps({'content': token}, ensure_ascii=False)}\n\n"

            # Save assistant message
            assistant_msg = ChatMessage(
                session_id=session_id,
                role="assistant",
                content=full_content,
                citations_json=citations,
            )
            self.db.add(assistant_msg)
            await self.db.flush()

            # Update session title if first message
            if session.title is None:
                session.title = message[:100]

            # Send done
            yield f"event: done\ndata: {json.dumps({'message_id': assistant_msg.id})}\n\n"

        except Exception as e:
            logger.exception("QA chat stream error")
            yield f'event: error\ndata: {json.dumps({"code":"QA_ERROR","message":str(e)})}\n\n'
