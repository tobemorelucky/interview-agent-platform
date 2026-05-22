"""Unified ingestion service for KB documents.

Used by both admin upload API and offline import script.

IMPORTANT: process_document() does NOT manage document status transitions.
Status management (PROCESSING / INDEXED / FAILED) is the caller's responsibility
(typically the Worker task). This keeps the pipeline focused on data transformation.
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone

from interview_api.core.config import settings
from interview_api.modules.kb.chunking import chunk_text
from interview_api.modules.kb.repository import (
    KbChunkRepository,
    KbDocumentRepository,
)
from interview_api.modules.kb.models import KbDocument

logger = logging.getLogger(__name__)


class KbIngestionService:
    def __init__(self, db, storage, embedding=None, vector_store=None):
        self.db = db
        self.storage = storage
        self.embedding = embedding
        self.vector_store = vector_store
        self.kb_repo = KbDocumentRepository(db)
        self.chunk_repo = KbChunkRepository(db)

    @staticmethod
    def compute_hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def is_duplicate(self, existing: KbDocument | None) -> bool:
        """Check if a document is a duplicate that should be skipped.

        Returns True if the existing document is in UPLOADED / PROCESSING / INDEXED.
        Returns False if None or FAILED (allows retry).
        """
        if existing is None:
            return False
        return existing.status != "FAILED"

    async def upload_and_create_document(
        self,
        filename: str,
        file_bytes: bytes,
        source_type: str,
        uploaded_by: int | None = None,
    ) -> KbDocument:
        """Upload file to MinIO, check duplicates, create KB document record."""
        content_hash = self.compute_hash(file_bytes)
        existing = await self.kb_repo.get_by_content_hash(content_hash)
        if self.is_duplicate(existing):
            from interview_api.core.exceptions import AppError
            raise AppError(
                code="DUPLICATE_DOCUMENT",
                message=f"Document already exists with status '{existing.status}'",
                status_code=409,
            )

        storage_key = f"kb_documents/{content_hash}/{filename}"
        await self.storage.ensure_bucket("interview-agent")
        await self.storage.upload(
            "interview-agent",
            storage_key,
            file_bytes,
            "text/markdown" if source_type == "markdown" else "text/plain",
        )

        doc = await self.kb_repo.create(
            title=filename,
            source_type=source_type,
            storage_key=storage_key,
            content_hash=content_hash,
            uploaded_by=uploaded_by,
        )
        return doc

    async def _clean_existing_data(self, document_id: int) -> None:
        """Remove old chunks and Milvus vectors for a document (idempotent).

        Called before (re-)processing so that retries or reindexes don't
        produce duplicate chunks or orphaned vectors.
        """
        # Delete old DB chunks
        await self.chunk_repo.delete_by_document_id(document_id)

        # Delete old Milvus vectors (best-effort, runs in thread to avoid
        # blocking the event loop)
        if self.vector_store is not None:
            await asyncio.to_thread(
                self.vector_store.delete_by_doc_id,
                "kb_chunks_current",
                document_id,
            )

    async def process_document(self, document_id: int) -> int:
        """Execute the full processing pipeline for a document.

        Does NOT manage document status — the caller must do that.
        Cleans existing chunks/vectors before processing (idempotent).

        Returns:
            Number of chunks created.

        Raises:
            RuntimeError: if embedding or vector_store not configured.
            ValueError: if document not found or no content extracted.
            Any exception from downstream services (storage, embedding, Milvus).
        """
        if self.embedding is None or self.vector_store is None:
            raise RuntimeError(
                "KbIngestionService.process_document requires embedding and vector_store"
            )

        doc = await self.kb_repo.get_by_id(document_id)
        if doc is None:
            raise ValueError(f"Document {document_id} not found")

        # Clean old data (idempotent — safe to call even if no old data exists)
        logger.info("[doc %s] Cleaning old chunks and vectors...", document_id)
        await self._clean_existing_data(document_id)

        # 1. Download and parse
        logger.info(
            "[doc %s] Downloading from MinIO: %s", document_id, doc.storage_key
        )
        file_bytes = await self.storage.download("interview-agent", doc.storage_key)
        text = file_bytes.decode("utf-8")
        logger.info("[doc %s] Downloaded %s bytes", document_id, len(file_bytes))

        # 2. Chunk
        logger.info("[doc %s] Chunking text...", document_id)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("No content extracted from document")
        logger.info("[doc %s] Chunked into %s pieces", document_id, len(chunks))

        # 3. Save chunks to PostgreSQL
        chunk_dicts = [
            {
                "document_id": document_id,
                "chunk_index": i,
                "content": c,
                "token_count": len(c) // 2,
            }
            for i, c in enumerate(chunks)
        ]
        chunk_entities = await self.chunk_repo.create_batch(chunk_dicts)
        chunk_ids = [c.id for c in chunk_entities]
        logger.info(
            "[doc %s] Saved %s chunks to PostgreSQL", document_id, len(chunk_entities)
        )

        # 4. Embed
        logger.info(
            "[doc %s] Embedding %s chunks (batch_size=%s)...",
            document_id,
            len(chunks),
            settings.kb_embedding_batch_size,
        )
        chunk_texts = [c["content"] for c in chunk_dicts]
        embeddings = []
        batch_size = settings.kb_embedding_batch_size
        for i in range(0, len(chunk_texts), batch_size):
            batch = chunk_texts[i : i + batch_size]
            emb = await self.embedding.embed_texts(batch)
            embeddings.extend(emb)
        logger.info(
            "[doc %s] Embedding finished — %s vectors", document_id, len(embeddings)
        )

        # 5. Insert to Milvus
        milvus_entities = []
        now_ts = int(time.time())
        for i, chunk_entity in enumerate(chunk_entities):
            milvus_entities.append(
                {
                    "id": chunk_entity.id,
                    "doc_id": document_id,
                    "chunk_id": chunk_entity.id,
                    "title": doc.title or "",
                    "content": chunk_entity.content,
                    "source_type": doc.source_type,
                    "dense_vector": embeddings[i],
                    "created_at_ts": now_ts,
                }
            )

        logger.info(
            "[doc %s] Inserting %s vectors into Milvus...",
            document_id,
            len(milvus_entities),
        )
        self.vector_store.insert("kb_chunks_current", milvus_entities)
        logger.info("[doc %s] Milvus insert done", document_id)

        # 6. Mark chunks as indexed
        await self.chunk_repo.mark_indexed(chunk_ids)

        logger.info("[doc %s] Pipeline complete — %s chunks indexed", document_id, len(chunk_entities))
        return len(chunk_entities)

    async def retry_failed(self, document_id: int) -> None:
        """Reset a FAILED document to UPLOADED and reprocess.

        Deletes existing chunks before reprocessing to avoid duplicates
        from a previous failed attempt.
        """
        doc = await self.kb_repo.get_by_id(document_id)
        if doc is None or doc.status != "FAILED":
            return
        await self.chunk_repo.delete_by_document_id(document_id)
        await self.kb_repo.update_status(document_id, "UPLOADED")
        await self.process_document(document_id)
