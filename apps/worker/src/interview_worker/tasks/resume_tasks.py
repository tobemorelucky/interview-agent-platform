"""Celery tasks for resume processing.

Pipeline:
  1. Download & parse resume file (PDF/DOCX/TXT)
  2. LLM structured extraction (resume_parse_v1)
  3. LLM retrieval query generation (resume_retrieval_queries_v1)
  4. KB retrieval (Milvus kb_chunks_current)
  5. LLM question generation with KB context (resume_interview_questions_v1)
  6. Save report
"""

import json
import time
from pathlib import Path

from celery.utils.log import get_task_logger

from interview_worker._asyncio import run_async

from interview_api.core.config import settings
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.infrastructure.embedding.provider import (
    OpenAICompatibleEmbeddingProvider,
)
from interview_api.infrastructure.llm.provider import OpenAICompatibleLLMProvider
from interview_api.infrastructure.milvus.provider import MilvusVectorStoreProvider
from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
from interview_api.modules.resume.models import ResumeReport
from interview_api.modules.resume.parser import ResumeParser
from interview_api.modules.resume.repository import ResumeRepository, ResumeReportRepository
from interview_api.modules.resume.retrieval import ResumeRetrievalService
from interview_worker.celery_app import app

logger = get_task_logger(__name__)

# Prompt templates live in apps/api/prompt_templates/
# This file is at apps/worker/src/interview_worker/tasks/resume_tasks.py
# Walk up 6 parents (to repo root), then into apps/api/prompt_templates/
_PROMPT_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent.parent
    / "apps" / "api" / "prompt_templates"
)


def _load_prompt(name: str) -> str:
    """Load a prompt template from the api prompt_templates directory."""
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


@app.task(name="process_resume", bind=True)
def process_resume(self, resume_id: int):
    """Process a resume: parse -> extract -> KB retrieval -> question generation.

    Lifecycle:
    1. Mark PROCESSING
    2. Run pipeline
    3. Mark COMPLETED on success, FAILED on error
    """
    logger.info(
        "Task received: resume_id=%s task_id=%s",
        resume_id,
        self.request.id,
    )
    t0 = time.monotonic()
    try:
        run_async(_process(resume_id))
    except Exception:
        elapsed = time.monotonic() - t0
        logger.exception(
            "Task FAILED: resume_id=%s elapsed=%.2fs", resume_id, elapsed
        )
        raise
    else:
        elapsed = time.monotonic() - t0
        logger.info(
            "Task finished: resume_id=%s elapsed=%.2fs", resume_id, elapsed
        )


async def _process(resume_id: int):
    """Async body of process_resume."""

    # Phase 0: mark PROCESSING in its own transaction
    async with async_session_factory() as db0:
        repo0 = ResumeRepository(db0)
        await repo0.mark_processing_started(resume_id)
        await db0.commit()
    logger.info("[resume %s] Status -> PROCESSING", resume_id)

    # Phase 1: run pipeline
    async with async_session_factory() as db:
        repo = ResumeRepository(db)
        report_repo = ResumeReportRepository(db)
        try:
            storage = MinioObjectStorageProvider()
            llm = OpenAICompatibleLLMProvider()
            embedding = OpenAICompatibleEmbeddingProvider()
            vector_store = MilvusVectorStoreProvider(embedding_dim=settings.embedding_dim)

            # 1. Download & parse
            logger.info("[resume %s] Downloading and parsing...", resume_id)
            resume = await repo.get_by_id(resume_id)
            if resume is None:
                raise ValueError(f"Resume {resume_id} not found")

            file_bytes = await storage.download(
                bucket_name=settings.minio_bucket,
                object_key=resume.storage_key,
            )
            parser = ResumeParser()
            raw_text = parser.parse(file_bytes, resume.file_type)
            await repo.update_raw_text(resume_id, raw_text)
            logger.info("[resume %s] Parsed %s chars of text", resume_id, len(raw_text))

            # 2. Structured extraction
            logger.info("[resume %s] LLM structured extraction...", resume_id)
            parse_prompt = _load_prompt("resume_parse_v1.md").format(resume_text=raw_text)
            structured_raw = await llm.chat([{"role": "user", "content": parse_prompt}])
            structured_json = _parse_json_response(structured_raw)
            logger.info("[resume %s] Structured extraction done", resume_id)

            # 3. Generate retrieval queries
            logger.info("[resume %s] Generating retrieval queries...", resume_id)
            queries_prompt = _load_prompt("resume_retrieval_queries_v1.md").format(
                structured_resume=json.dumps(structured_json, ensure_ascii=False),
                query_count=settings.resume_retrieval_query_count,
            )
            queries_raw = await llm.chat([{"role": "user", "content": queries_prompt}])
            queries_json = _parse_json_response(queries_raw)
            logger.info("[resume %s] Generated %s retrieval queries", resume_id, len(queries_json.get("queries", [])))

            # 4. KB Retrieval
            if settings.resume_kb_retrieval_enabled:
                logger.info("[resume %s] Running KB retrieval...", resume_id)
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
            else:
                logger.info("[resume %s] KB retrieval disabled", resume_id)
                retrieved_context = {"total_hits": 0, "queries": []}
                fallback_policy = "NO_KB"

            # 5. Generate interview questions
            logger.info("[resume %s] Generating interview questions (policy=%s)...", resume_id, fallback_policy)
            questions_prompt = _load_prompt("resume_interview_questions_v1.md").format(
                structured_resume=json.dumps(structured_json, ensure_ascii=False),
                retrieved_context=json.dumps(retrieved_context, ensure_ascii=False),
                fallback_policy=fallback_policy,
                question_count=settings.resume_question_count,
            )
            questions_raw = await llm.chat([{"role": "user", "content": questions_prompt}])
            questions_json = _parse_json_response(questions_raw)
            logger.info(
                "[resume %s] Generated %s questions",
                resume_id,
                len(questions_json.get("questions", [])),
            )

            # 6. Save report
            logger.info("[resume %s] Saving report...", resume_id)
            report = ResumeReport(
                resume_id=resume_id,
                user_id=resume.user_id,
                summary_json=structured_json,
                retrieval_queries_json=queries_json,
                retrieved_context_json=retrieved_context,
                questions_json=questions_json,
                suggestions_json=questions_json.get("overall_suggestions", {}),
            )
            await report_repo.create(report)

            await repo.mark_processing_finished(resume_id, "COMPLETED")
            await db.commit()
            logger.info("[resume %s] SUCCESS — status COMPLETED", resume_id)

        except Exception:
            await db.rollback()
            logger.exception("[resume %s] Pipeline failed — marking FAILED", resume_id)

            error_text = _format_error()
            async with async_session_factory() as db2:
                repo2 = ResumeRepository(db2)
                await repo2.mark_processing_finished(
                    resume_id, "FAILED", error_message=error_text
                )
                await db2.commit()
            logger.info("[resume %s] Status -> FAILED", resume_id)

            raise


def _parse_json_response(raw: str) -> dict:
    """Parse JSON from LLM response, stripping markdown code fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        # Remove closing fence
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


def _format_error() -> str:
    """Return a one-line summary of the current exception."""
    import sys

    exc_type, exc_value, _ = sys.exc_info()
    msg = f"{exc_type.__name__}: {exc_value}" if exc_type else str(exc_value)
    return msg[:2000]
