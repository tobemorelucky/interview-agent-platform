"""Celery tasks for knowledge base document processing."""

import asyncio
import os

from celery import Task
from celery.utils.log import get_task_logger

from interview_api.infrastructure.db.session import async_session_factory
from interview_api.infrastructure.embedding.provider import (
    OpenAICompatibleEmbeddingProvider,
)
from interview_api.infrastructure.milvus.provider import MilvusVectorStoreProvider
from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
from interview_api.modules.kb.ingestion_service import KbIngestionService

logger = get_task_logger(__name__)


class KbDocumentTask(Task):
    name = "process_kb_document"

    def run(self, document_id: int):
        asyncio.run(self._process(document_id))

    async def _process(self, document_id: int):
        logger.info(f"Processing kb document {document_id}")

        async with async_session_factory() as db:
            try:
                storage = MinioObjectStorageProvider()
                embedding = OpenAICompatibleEmbeddingProvider()
                vector_store = MilvusVectorStoreProvider(
                    embedding_dim=int(os.getenv("EMBEDDING_DIM", "768"))
                )
                service = KbIngestionService(db, storage, embedding, vector_store)
                await service.process_document(document_id)

                await db.commit()
                logger.info(f"Document {document_id} processed successfully")
            except Exception:
                await db.rollback()
                logger.exception(f"Document {document_id} processing failed")
                raise


process_kb_document = KbDocumentTask()
