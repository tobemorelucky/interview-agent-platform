"""Resume KB retrieval service.

Generates retrieval queries from structured resume, searches Milvus,
aggregates results, and determines fallback policy.
"""

import logging

from interview_api.core.config import settings

logger = logging.getLogger(__name__)


class ResumeRetrievalService:
    """Service for KB retrieval driven by resume-derived queries.

    Reuses the existing Phase 2 kb_chunks_current collection and
    EmbeddingProvider / VectorStoreProvider abstractions.
    """

    def __init__(self, embedding, vector_store):
        self.embedding = embedding
        self.vector_store = vector_store

    async def retrieve(
        self,
        queries: list[dict],
        top_k: int | None = None,
        min_score: float | None = None,
    ) -> dict:
        """Execute retrieval for each query and aggregate results.

        Args:
            queries: List of {"query": "...", "target": "..."} dicts.
            top_k: Top-K hits per query (default from settings).
            min_score: Minimum COSINE similarity threshold (default from settings).

        Returns:
            {
                "total_hits": <int>,
                "queries": [
                    {
                        "query": "Python FastAPI 面试题",
                        "target": "tech_stack",
                        "hit_count": 5,
                        "top_hits": [{chunk_id, doc_id, title, preview, score, source_type}, ...]
                    },
                    ...
                ]
            }
        """
        if top_k is None:
            top_k = settings.resume_retrieval_top_k
        if min_score is None:
            min_score = settings.resume_retrieval_min_score

        preview_chars = settings.rag_citation_preview_chars
        all_results: dict = {"total_hits": 0, "queries": []}

        for q in queries:
            query_text = q.get("query", "")
            target = q.get("target", "general")

            if not query_text:
                continue

            try:
                vec = await self.embedding.embed_query(query_text)
                hits = self.vector_store.search(
                    "kb_chunks_current",
                    vec,
                    top_k=top_k,
                    output_fields=["id", "doc_id", "chunk_id", "title", "content", "source_type"],
                )

                qualified = [h for h in hits if h.get("score", 0) >= min_score]
                top_hits = [
                    {
                        "chunk_id": h.get("id", h.get("chunk_id")),
                        "doc_id": h.get("doc_id"),
                        "title": h.get("title", ""),
                        "preview": (h.get("content", "") or "")[:preview_chars],
                        "score": round(h.get("score", 0), 4),
                        "source_type": h.get("source_type", ""),
                    }
                    for h in qualified
                ]

                all_results["total_hits"] += len(top_hits)
                all_results["queries"].append({
                    "query": query_text,
                    "target": target,
                    "hit_count": len(top_hits),
                    "top_hits": top_hits,
                })
            except Exception:
                logger.exception("KB retrieval failed for query: %s", query_text)
                all_results["queries"].append({
                    "query": query_text,
                    "target": target,
                    "hit_count": 0,
                    "top_hits": [],
                })

        return all_results

    @staticmethod
    def determine_fallback_policy(
        retrieved_context: dict,
        question_count: int | None = None,
    ) -> str:
        """Determine the fallback policy based on retrieval sufficiency.

        - KB_PREFERRED: rich KB hits (>= question_count)
        - KB_SUPPLEMENT: moderate KB hits (>= question_count / 2)
        - HIGH_FALLBACK: insufficient KB hits
        """
        if question_count is None:
            question_count = settings.resume_question_count

        total_hits = retrieved_context.get("total_hits", 0)

        if total_hits >= question_count:
            return "KB_PREFERRED"
        elif total_hits >= question_count / 2:
            return "KB_SUPPLEMENT"
        else:
            return "HIGH_FALLBACK"
