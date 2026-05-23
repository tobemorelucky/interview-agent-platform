"""Resume processing pipeline shared by API and Celery worker.

The pipeline deliberately keeps database transactions short. Slow external
work such as LLM calls, embedding, Milvus search, and object storage I/O must
not hold an ORM session or row lock open.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import time
from pathlib import Path

from interview_api.core.config import settings

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parents[4] / "prompt_templates"


@dataclass(frozen=True)
class ResumeSnapshot:
    id: int
    user_id: int
    storage_key: str
    file_type: str
    task_id: str | None


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


async def process_resume_async(resume_id: int, task_id: str | None = None) -> None:
    """Run the full resume processing pipeline.

    Args:
        resume_id: Resume row id.
        task_id: Optional Celery task id. When provided, stale queued tasks are
            ignored if the resume row already points at a newer task id.
    """
    t0 = time.monotonic()
    logger.info("[resume %s] Processing started", resume_id)

    try:
        if await _is_stale_task(resume_id, task_id):
            logger.warning(
                "[resume %s] Skip stale task task_id=%s", resume_id, task_id
            )
            return

        await _mark_processing_started(resume_id)
        logger.info("[resume %s] Status -> PROCESSING", resume_id)

        await _run_pipeline(resume_id)

        elapsed = time.monotonic() - t0
        logger.info("[resume %s] Processing SUCCESS elapsed=%.2fs", resume_id, elapsed)
    except Exception:
        elapsed = time.monotonic() - t0
        logger.exception("[resume %s] Processing FAILED elapsed=%.2fs", resume_id, elapsed)
        await _mark_failed_best_effort(resume_id, _format_error())
        raise


async def _run_pipeline(resume_id: int) -> None:
    from interview_api.infrastructure.embedding.provider import (
        OpenAICompatibleEmbeddingProvider,
    )
    from interview_api.infrastructure.llm.provider import OpenAICompatibleLLMProvider
    from interview_api.infrastructure.milvus.provider import MilvusVectorStoreProvider
    from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
    from interview_api.modules.resume.parser import ResumeParser
    from interview_api.modules.resume.retrieval import ResumeRetrievalService

    storage = MinioObjectStorageProvider()
    llm = OpenAICompatibleLLMProvider()
    embedding = OpenAICompatibleEmbeddingProvider()
    vector_store = MilvusVectorStoreProvider(embedding_dim=settings.embedding_dim)

    resume = await _get_resume_snapshot(resume_id)
    if resume is None:
        raise ValueError(f"Resume {resume_id} not found")

    logger.info("[resume %s] ========== PARSING_RESUME ==========", resume_id)
    await _update_stage(resume_id, "PARSING_RESUME", "Parsing resume file...")
    file_bytes = await storage.download(
        bucket_name=settings.minio_bucket,
        object_key=resume.storage_key,
    )
    raw_text = ResumeParser().parse(file_bytes, resume.file_type)
    await _save_raw_text(resume_id, raw_text)
    logger.info("[resume %s] Parsed %s chars of text", resume_id, len(raw_text))

    logger.info("[resume %s] ========== STRUCTURING_RESUME ==========", resume_id)
    await _update_stage(
        resume_id, "STRUCTURING_RESUME", "Extracting structured resume data..."
    )
    parse_prompt = _load_prompt("resume_parse_v1.md").format(resume_text=raw_text)
    logger.info(
        "[resume %s] LLM call: structured extraction (%s chars input)",
        resume_id,
        len(parse_prompt),
    )
    structured_raw = await llm.chat([{"role": "user", "content": parse_prompt}])
    logger.info("[resume %s] LLM response: %s chars", resume_id, len(structured_raw))
    structured_json = _parse_json_logged(structured_raw, resume_id)
    logger.info("[resume %s] Structured extraction done", resume_id)

    logger.info(
        "[resume %s] ========== GENERATING_RETRIEVAL_QUERIES ==========",
        resume_id,
    )
    await _update_stage(
        resume_id,
        "GENERATING_RETRIEVAL_QUERIES",
        "Generating knowledge-base retrieval queries...",
    )
    queries_prompt = _load_prompt("resume_retrieval_queries_v1.md").format(
        structured_resume=json.dumps(structured_json, ensure_ascii=False),
        query_count=settings.resume_retrieval_query_count,
    )
    logger.info("[resume %s] LLM call: retrieval query generation", resume_id)
    queries_raw = await llm.chat([{"role": "user", "content": queries_prompt}])
    logger.info("[resume %s] LLM response: %s chars", resume_id, len(queries_raw))
    queries_json = _parse_json_logged(queries_raw, resume_id)
    logger.info(
        "[resume %s] Generated %s retrieval queries",
        resume_id,
        len(queries_json.get("queries", [])),
    )

    if settings.resume_kb_retrieval_enabled:
        logger.info("[resume %s] ========== RETRIEVING_KB ==========", resume_id)
        await _update_stage(resume_id, "RETRIEVING_KB", "Retrieving KB context...")
        try:
            retrieval_service = ResumeRetrievalService(embedding, vector_store)
            retrieved_context = await retrieval_service.retrieve(
                queries=queries_json.get("queries", []),
            )
            fallback_policy = retrieval_service.determine_fallback_policy(
                retrieved_context,
                question_count=settings.resume_question_count,
            )
            logger.info(
                "[resume %s] KB retrieval done: %s total hits, policy=%s",
                resume_id,
                retrieved_context.get("total_hits", 0),
                fallback_policy,
            )
        except Exception:
            logger.warning(
                "[resume %s] KB retrieval failed; continuing without KB context",
                resume_id,
                exc_info=True,
            )
            retrieved_context = {"total_hits": 0, "queries": []}
            fallback_policy = "NO_KB"
    else:
        logger.info("[resume %s] KB retrieval disabled", resume_id)
        retrieved_context = {"total_hits": 0, "queries": []}
        fallback_policy = "NO_KB"

    logger.info(
        "[resume %s] ========== GENERATING_QUESTIONS (policy=%s) ==========",
        resume_id,
        fallback_policy,
    )
    await _update_stage(resume_id, "GENERATING_QUESTIONS", "Generating questions...")
    questions_prompt = _load_prompt("resume_interview_questions_v1.md").format(
        structured_resume=json.dumps(structured_json, ensure_ascii=False),
        retrieved_context=json.dumps(retrieved_context, ensure_ascii=False),
        fallback_policy=fallback_policy,
        question_count=settings.resume_question_count,
    )
    logger.info(
        "[resume %s] LLM call: interview question generation (%s chars input)",
        resume_id,
        len(questions_prompt),
    )
    questions_raw = await llm.chat([{"role": "user", "content": questions_prompt}])
    logger.info("[resume %s] LLM response: %s chars", resume_id, len(questions_raw))
    questions_json = _parse_json_logged(questions_raw, resume_id)
    logger.info(
        "[resume %s] Generated %s questions",
        resume_id,
        len(questions_json.get("questions", [])),
    )

    logger.info("[resume %s] ========== SAVING_REPORT ==========", resume_id)
    await _update_stage(resume_id, "SAVING_REPORT", "Saving analysis report...")
    await _save_report_and_complete(
        resume=resume,
        structured_json=structured_json,
        queries_json=queries_json,
        retrieved_context=retrieved_context,
        questions_json=questions_json,
    )
    logger.info("[resume %s] SUCCESS status COMPLETED", resume_id)


async def _is_stale_task(resume_id: int, task_id: str | None) -> bool:
    if not task_id:
        return False
    resume = await _get_resume_snapshot(resume_id)
    return resume is not None and resume.task_id is not None and resume.task_id != task_id


async def _get_resume_snapshot(resume_id: int) -> ResumeSnapshot | None:
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.modules.resume.repository import ResumeRepository

    async with async_session_factory() as db:
        resume = await ResumeRepository(db).get_by_id(resume_id)
        if resume is None:
            return None
        return ResumeSnapshot(
            id=resume.id,
            user_id=resume.user_id,
            storage_key=resume.storage_key,
            file_type=resume.file_type,
            task_id=resume.task_id,
        )


async def _mark_processing_started(resume_id: int) -> None:
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.modules.resume.repository import ResumeRepository

    async with async_session_factory() as db:
        repo = ResumeRepository(db)
        await repo.mark_processing_started(resume_id)
        await db.commit()


async def _save_raw_text(resume_id: int, raw_text: str) -> None:
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.modules.resume.repository import ResumeRepository

    async with async_session_factory() as db:
        await ResumeRepository(db).update_raw_text(resume_id, raw_text)
        await db.commit()


async def _update_stage(resume_id: int, stage: str, message: str = "") -> None:
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.modules.resume.repository import ResumeRepository

    async with async_session_factory() as db:
        await ResumeRepository(db).update_processing_stage(resume_id, stage, message)
        await db.commit()


async def _save_report_and_complete(
    *,
    resume: ResumeSnapshot,
    structured_json: dict,
    queries_json: dict,
    retrieved_context: dict,
    questions_json: dict,
) -> None:
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.modules.resume.models import ResumeReport
    from interview_api.modules.resume.repository import (
        ResumeRepository,
        ResumeReportRepository,
    )

    async with async_session_factory() as db:
        resume_repo = ResumeRepository(db)
        report_repo = ResumeReportRepository(db)
        await report_repo.delete_by_resume_id(resume.id)
        await report_repo.create(
            ResumeReport(
                resume_id=resume.id,
                user_id=resume.user_id,
                summary_json=structured_json,
                retrieval_queries_json=queries_json,
                retrieved_context_json=retrieved_context,
                questions_json=questions_json,
                suggestions_json=questions_json.get("overall_suggestions", {}),
            )
        )
        await resume_repo.mark_processing_finished(resume.id, "COMPLETED")
        await resume_repo.update_processing_stage(
            resume.id, "COMPLETED", "Resume analysis completed."
        )
        await db.commit()


async def _mark_failed_best_effort(resume_id: int, error_text: str) -> None:
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.modules.resume.repository import ResumeRepository

    try:
        async with async_session_factory() as db:
            repo = ResumeRepository(db)
            await repo.mark_processing_finished(
                resume_id, "FAILED", error_message=error_text
            )
            await repo.update_processing_stage(resume_id, "FAILED", error_text[:500])
            await db.commit()
        logger.info("[resume %s] Status -> FAILED", resume_id)
    except Exception:
        logger.exception("[resume %s] Failed to mark resume as FAILED", resume_id)


def _parse_json_logged(raw: str, resume_id: int) -> dict:
    try:
        return _parse_json_response(raw)
    except Exception:
        logger.error(
            "[resume %s] JSON parse failed. Raw (first 500 chars): %s",
            resume_id,
            raw[:500],
        )
        raise


def _parse_json_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


def _format_error() -> str:
    import sys

    exc_type, exc_value, _ = sys.exc_info()
    msg = f"{exc_type.__name__}: {exc_value}" if exc_type else str(exc_value)
    return msg[:2000]
