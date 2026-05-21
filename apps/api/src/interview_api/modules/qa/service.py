"""QA service: sessions, retrieval, and streaming answer generation."""

import json
import logging
from pathlib import Path

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.config import settings
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

    async def retrieve(self, query: str, top_k: int | None = None) -> list[dict]:
        """Search Milvus for relevant chunks."""
        if top_k is None:
            top_k = settings.rag_retrieval_top_k
        query_vec = await self.embedding.embed_query(query)
        results = self.vector_store.search(
            "kb_chunks_current",
            query_vec,
            top_k=top_k,
        )
        return results

    # ---- Context building ----

    def _build_context(self, chunks: list[dict]) -> str:
        """Build LLM context from retrieved chunks, capped by rag_context_max_chars.

        Chunks are appended in search rank order.  When the accumulated length
        exceeds the cap, further chunks are dropped.  The last included chunk
        is truncated if only partial space remains (only kept when > 100 chars).
        """
        max_chars = settings.rag_context_max_chars
        parts: list[str] = []
        total = 0

        for i, c in enumerate(chunks):
            title = c.get("title", "Unknown")
            content = c.get("content", "")
            entry = f"[{i + 1}] ({title}) {content}"

            if total + len(entry) > max_chars:
                remaining = max_chars - total
                if remaining > 100:
                    parts.append(entry[:remaining])
                break

            parts.append(entry)
            total += len(entry)

        return "\n\n".join(parts)

    # ---- Prompt building ----

    def _load_prompt_template(self) -> str:
        return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

    def _build_prompt(self, question: str, context: str) -> str:
        template = self._load_prompt_template()
        return template.format(question=question, context=context)

    # ---- SSE helpers ----

    @staticmethod
    def _sse(event: str, data: dict | list) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    # ---- Streaming chat ----

    async def chat_stream(
        self,
        session_id: int,
        user_id: int,
        message: str,
    ):
        """Yields SSE events: status, retrieval, citation, token, done, error.

        Event order:
          1. status: analyzing_query
          2. status: embedding_query
          3. status: retrieving
          4. retrieval: {top_k, hit_count}
          5. citation: [...]  (preview-only, no full chunk content)
          6. status: generating
          7. token ... (streaming)
          8. done
        """
        # Validate session belongs to user
        session = await self.get_session(session_id, user_id)
        if session is None:
            yield self._sse("error", {"code": "QA_SESSION_NOT_FOUND", "message": "Session not found"})
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
            # --- RAG pipeline with progress events ---

            yield self._sse("status", {"stage": "analyzing_query"})
            yield self._sse("status", {"stage": "embedding_query"})
            yield self._sse("status", {"stage": "retrieving"})

            top_k = settings.rag_retrieval_top_k
            chunks = await self.retrieve(message, top_k=top_k)

            yield self._sse("retrieval", {"top_k": top_k, "hit_count": len(chunks)})

            # Build citations — preview only, full content omitted
            preview_len = settings.rag_citation_preview_chars
            citations = [
                {
                    "chunk_id": c.get("chunk_id"),
                    "doc_id": c.get("doc_id"),
                    "title": c.get("title", ""),
                    "source_type": c.get("source_type", ""),
                    "preview": (c.get("content", "") or "")[:preview_len],
                    "score": c.get("score"),
                }
                for c in chunks
            ]
            yield self._sse("citation", citations)

            # Build prompt (context capped by rag_context_max_chars)
            context = self._build_context(chunks)
            prompt = self._build_prompt(message, context)

            yield self._sse("status", {"stage": "generating"})

            # Stream LLM response
            llm_messages = [{"role": "user", "content": prompt}]
            full_content = ""

            async for token in self.llm.chat_stream(llm_messages):
                full_content += token
                yield self._sse("token", {"content": token})

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

            yield self._sse("done", {"message_id": assistant_msg.id})

        except Exception as e:
            logger.exception("QA chat stream error")
            yield self._sse("error", {"code": "QA_ERROR", "message": str(e)})
