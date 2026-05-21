"""Celery tasks for knowledge base document processing."""

import asyncio

from celery.utils.log import get_task_logger

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


@app.task(name="process_kb_document")
def process_kb_document(document_id: int):
    """Process a KB document: chunk → embed → index into Milvus."""
    asyncio.run(_process(document_id))


async def _process(document_id: int):
    logger.info("Processing kb document %s", document_id)

    async with async_session_factory() as db:
        try:
            storage = MinioObjectStorageProvider()
            embedding = OpenAICompatibleEmbeddingProvider()
            vector_store = MilvusVectorStoreProvider(
                embedding_dim=settings.embedding_dim
            )
            service = KbIngestionService(db, storage, embedding, vector_store)
            await service.process_document(document_id)

            await db.commit()
            logger.info("Document %s processed successfully", document_id)
        except Exception:
            await db.rollback()
            logger.exception("Document %s processing failed", document_id)
            raise
