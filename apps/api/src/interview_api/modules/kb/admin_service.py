"""Admin service for KB document lifecycle: delete and reindex."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.kb.repository import KbChunkRepository, KbDocumentRepository

logger = logging.getLogger(__name__)


class KbAdminService:
    """Orchestrates Milvus + PostgreSQL + MinIO + Celery for admin operations."""

    def __init__(
        self,
        db: AsyncSession,
        storage,
        vector_store,
    ):
        self.db = db
        self.storage = storage
        self.vector_store = vector_store
        self._kb_repo = KbDocumentRepository(db)
        self._chunk_repo = KbChunkRepository(db)

    # ------------------------------------------------------------------
    # delete_document
    # ------------------------------------------------------------------

    async def delete_document(self, document_id: int) -> None:
        """Hard-delete a document and all associated resources.

        Order: Milvus → PostgreSQL chunks → MinIO → PostgreSQL document.
        Milvus / MinIO failures are logged but do not block the deletion.
        """
        doc = await self._kb_repo.get_by_id(document_id)
        if doc is None:
            from interview_api.core.exceptions import NotFoundError
            raise NotFoundError(message="Document not found")

        # 1. Delete Milvus vectors (best-effort)
        self.vector_store.delete_by_doc_id("kb_chunks_current", document_id)

        # 2. Delete PostgreSQL chunks
        await self._chunk_repo.delete_by_document_id(document_id)

        # 3. Delete MinIO object (best-effort)
        if doc.storage_key:
            try:
                await self.storage.delete("interview-agent", doc.storage_key)
            except Exception:
                logger.warning(
                    "Failed to delete MinIO object for doc %s: %s",
                    document_id,
                    doc.storage_key,
                    exc_info=True,
                )

        # 4. Delete PostgreSQL document
        await self._kb_repo.delete(document_id)

    # ------------------------------------------------------------------
    # reindex_document
    # ------------------------------------------------------------------

    async def reindex_document(self, document_id: int) -> dict:
        """Clean existing chunks/vectors, reset status, and dispatch Celery reprocessing.

        Returns the document's current state after reset.
        """
        doc = await self._kb_repo.get_by_id(document_id)
        if doc is None:
            from interview_api.core.exceptions import NotFoundError
            raise NotFoundError(message="Document not found")

        # 1. Delete Milvus vectors (best-effort)
        self.vector_store.delete_by_doc_id("kb_chunks_current", document_id)

        # 2. Delete PostgreSQL chunks
        await self._chunk_repo.delete_by_document_id(document_id)

        # 3. Reset document status
        await self._kb_repo.reset_status(document_id)

        # 4. Dispatch Celery processing task (via broker, not direct import)
        try:
            from interview_api.infrastructure.tasks.celery_client import (
                dispatch_process_kb_document,
            )
            dispatch_process_kb_document(document_id)
        except Exception:
            logger.exception(
                "Failed to dispatch Celery task for reindex of doc %s", document_id
            )
            await self._kb_repo.update_status(
                document_id,
                "FAILED",
                error_message="Celery dispatch failed during reindex",
            )

        # Return current document state
        doc = await self._kb_repo.get_by_id(document_id)
        return {
            "id": doc.id,
            "title": doc.title,
            "source_type": doc.source_type,
            "status": doc.status,
            "chunk_count": doc.chunk_count,
            "error_message": doc.error_message,
            "uploaded_by": doc.uploaded_by,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "indexed_at": doc.indexed_at.isoformat() if doc.indexed_at else None,
        }
