"""Question retrieval service — abstracts the question source for interview generation.

Phase 3.3: uses kb_chunks_current via ResumeRetrievalService.
Phase 4: can be swapped to interview_questions / interview_experiences collections.
"""

import logging

from interview_api.core.config import settings
from interview_api.modules.resume.retrieval import ResumeRetrievalService

logger = logging.getLogger(__name__)


class QuestionRetrievalService:
    """Retrieves interview question candidates from knowledge base.

    Delegates to ResumeRetrievalService internally for embed → search →
    filter → dedup logic, avoiding code duplication.
    """

    def __init__(self, embedding, vector_store):
        self._resume_retrieval = ResumeRetrievalService(embedding, vector_store)

    async def retrieve_by_dimensions(
        self,
        dimensions: list[dict],
        top_k: int | None = None,
        min_score: float | None = None,
    ) -> dict[str, list[dict]]:
        """Retrieve KB chunks per dimension, returning deduped results.

        Args:
            dimensions: List of {"dimension": str, "search_queries": [str], ...}
            top_k: Top-K hits per query.
            min_score: Minimum COSINE similarity threshold.

        Returns:
            {dimension_name: [deduped_hits]}
        """
        if top_k is None:
            top_k = settings.interview_question_retrieval_top_k
        if min_score is None:
            min_score = settings.interview_question_retrieval_min_score

        # Collect all queries with their dimension label
        queries: list[dict] = []
        for dim in dimensions:
            dim_name = dim.get("dimension", "未命名维度")
            for q_text in dim.get("search_queries", []):
                if q_text:
                    queries.append({
                        "query": q_text,
                        "target": dim_name,
                    })

        if not queries:
            return {}

        # Delegate to ResumeRetrievalService for embed + search + filter
        try:
            result = await self._resume_retrieval.retrieve(
                queries, top_k=top_k, min_score=min_score
            )
        except Exception:
            logger.exception("Question retrieval failed")
            return {}

        # Group hits by dimension and deduplicate across dimensions by chunk_id
        seen_chunk_ids: set[int] = set()
        grouped: dict[str, list[dict]] = {}

        for query_result in result.get("queries", []):
            dim_name = query_result.get("target", "未命名维度")
            if dim_name not in grouped:
                grouped[dim_name] = []

            for hit in query_result.get("top_hits", []):
                chunk_id = hit.get("chunk_id")
                if chunk_id is not None and chunk_id in seen_chunk_ids:
                    continue
                if chunk_id is not None:
                    seen_chunk_ids.add(chunk_id)
                grouped[dim_name].append(hit)

        return grouped
