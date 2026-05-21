"""Celery tasks for knowledge base document processing."""

import asyncio
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
from interview_worker.celery_app import app

logger = get_task_logger(__name__)


@app.task(name="process_kb_document", bind=True)
def process_kb_document(self, document_id: int):
    """Process a KB document: chunk -> embed -> index into Milvus."""
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
        logger.exception("Task FAILED: document_id=%s", document_id)
        raise
    finally:
        elapsed = time.monotonic() - t0
        logger.info("Task finished: document_id=%s elapsed=%.2fs", document_id, elapsed)


async def _process(document_id: int):
    logger.info("[doc %s] Loading document from PostgreSQL...", document_id)

    async with async_session_factory() as db:
        try:
            storage = MinioObjectStorageProvider()
            embedding = OpenAICompatibleEmbeddingProvider()
            vector_store = MilvusVectorStoreProvider(
                embedding_dim=settings.embedding_dim
            )
            service = KbIngestionService(db, storage, embedding, vector_store)

            logger.info("[doc %s] Starting process_document...", document_id)
            await service.process_document(document_id)

            await db.commit()
            logger.info("[doc %s] SUCCESS — status updated to INDEXED", document_id)
        except Exception:
            await db.rollback()
            logger.exception("[doc %s] FAILED — see traceback above", document_id)
            raise
