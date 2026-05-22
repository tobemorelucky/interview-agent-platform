"""Celery tasks for knowledge base document processing."""

import time

from celery.utils.log import get_task_logger

from interview_worker._asyncio import run_async

from interview_api.core.config import settings
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.infrastructure.embedding.provider import (
    OpenAICompatibleEmbeddingProvider,
)
from interview_api.infrastructure.milvus.provider import MilvusVectorStoreProvider
from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
from interview_api.modules.kb.ingestion_service import KbIngestionService
from interview_api.modules.kb.repository import KbDocumentRepository
from interview_worker.celery_app import app

logger = get_task_logger(__name__)


@app.task(name="process_kb_document", bind=True)
def process_kb_document(self, document_id: int):
    """Process a KB document: chunk -> embed -> index into Milvus.

    Manages the full document lifecycle:
    1. Mark PROCESSING start
    2. Run the ingestion pipeline
    3. Mark INDEXED on success, FAILED on error (re-raises so Celery also records failure)
    """
    logger.info(
        "Task received: document_id=%s task_id=%s task_name=%s",
        document_id,
        self.request.id,
        self.name,
    )
    t0 = time.monotonic()
    try:
        run_async(_process(document_id))
    except Exception:
        elapsed = time.monotonic() - t0
        logger.exception(
            "Task FAILED: document_id=%s elapsed=%.2fs", document_id, elapsed
        )
        raise
    else:
        elapsed = time.monotonic() - t0
        logger.info(
            "Task finished: document_id=%s elapsed=%.2fs", document_id, elapsed
        )


async def _process(document_id: int):
    """Async body of process_kb_document.

    Lifecycle:
    - Mark PROCESSING (own commit so it's visible immediately)
    - Run pipeline (all work in one transaction)
    - On success: mark INDEXED + commit
    - On failure: rollback work, mark FAILED in a fresh session, re-raise
    """
    logger.info("[doc %s] Marking PROCESSING...", document_id)

    # Phase 0: mark PROCESSING in its own transaction so the frontend sees it
    async with async_session_factory() as db0:
        repo0 = KbDocumentRepository(db0)
        await repo0.mark_processing_started(document_id)
        await db0.commit()
    logger.info("[doc %s] Status -> PROCESSING", document_id)

    # Phase 1: run the actual pipeline
    chunk_count = 0
    async with async_session_factory() as db:
        repo = KbDocumentRepository(db)
        try:
            storage = MinioObjectStorageProvider()
            embedding = OpenAICompatibleEmbeddingProvider()
            vector_store = MilvusVectorStoreProvider(
                embedding_dim=settings.embedding_dim
            )
            service = KbIngestionService(db, storage, embedding, vector_store)

            logger.info("[doc %s] Starting pipeline...", document_id)
            chunk_count = await service.process_document(document_id)

            # Phase 2: mark INDEXED
            logger.info(
                "[doc %s] Pipeline done — marking INDEXED (%s chunks)",
                document_id,
                chunk_count,
            )
            await repo.mark_processing_finished(
                document_id, "INDEXED", chunk_count=chunk_count
            )
            await db.commit()
            logger.info(
                "[doc %s] SUCCESS — status INDEXED, %s chunks", document_id, chunk_count
            )

        except Exception:
            await db.rollback()
            logger.exception("[doc %s] Pipeline failed — marking FAILED", document_id)

            # Phase 3: mark FAILED in a fresh session (work session is rolled back)
            error_text = _format_error()
            async with async_session_factory() as db2:
                repo2 = KbDocumentRepository(db2)
                await repo2.mark_processing_finished(
                    document_id, "FAILED", error_message=error_text
                )
                await db2.commit()
            logger.info("[doc %s] Status -> FAILED", document_id)

            raise


def _format_error() -> str:
    """Return a one-line summary of the current exception for the DB."""
    import sys
    import traceback

    exc_type, exc_value, _ = sys.exc_info()
    msg = f"{exc_type.__name__}: {exc_value}" if exc_type else str(exc_value)
    # Cap at 2000 chars to fit in the TEXT column comfortably
    return msg[:2000]
