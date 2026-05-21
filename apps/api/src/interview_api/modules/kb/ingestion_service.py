"""Unified ingestion service for KB documents.

Used by both admin upload API and offline import script.
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

    async def process_document(self, document_id: int) -> None:
        """Full processing pipeline: download -> parse -> chunk -> embed -> index."""
        if self.embedding is None or self.vector_store is None:
            raise RuntimeError(
                "KbIngestionService.process_document requires embedding and vector_store"
            )

        doc = await self.kb_repo.get_by_id(document_id)
        if doc is None:
            logger.error(f"Document {document_id} not found")
            return

        try:
            await self.kb_repo.update_status(document_id, "PROCESSING")

            # Download and parse
            file_bytes = await self.storage.download("interview-agent", doc.storage_key)
            text = file_bytes.decode("utf-8")

            # Chunk
            chunks = chunk_text(text)
            if not chunks:
                raise ValueError("No content extracted from document")

            # Save chunks to PostgreSQL
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

            # Embed
            chunk_texts = [c["content"] for c in chunk_dicts]
            embeddings = []
            batch_size = settings.kb_embedding_batch_size
            for i in range(0, len(chunk_texts), batch_size):
                batch = chunk_texts[i : i + batch_size]
                emb = await self.embedding.embed_texts(batch)
                embeddings.extend(emb)

            # Insert to Milvus
            milvus_entities = []
            now_ts = int(time.time())
            for i, chunk_entity in enumerate(chunk_entities):
                milvus_entities.append({
                    "id": chunk_entity.id,
                    "doc_id": document_id,
                    "chunk_id": chunk_entity.id,
                    "title": doc.title or "",
                    "content": chunk_entity.content,
                    "source_type": doc.source_type,
                    "dense_vector": embeddings[i],
                    "created_at_ts": now_ts,
                })

            self.vector_store.insert("kb_chunks_current", milvus_entities)

            # Mark chunks as indexed
            await self.chunk_repo.mark_indexed(chunk_ids)

            # Update document status
            await self.kb_repo.update_status(
                document_id,
                "INDEXED",
                chunk_count=len(chunk_entities),
            )
            logger.info(f"Document {document_id} indexed successfully")

        except Exception as e:
            logger.exception(f"Document {document_id} processing failed")
            await self.kb_repo.update_status(
                document_id,
                "FAILED",
                error_message=str(e),
            )

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
