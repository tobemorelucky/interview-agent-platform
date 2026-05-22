"""Manually process a single KB document outside of Celery.

Useful for diagnosing stuck documents or reprocessing after Worker issues.
This script handles the full lifecycle (mark PROCESSING → pipeline → INDEXED/FAILED)
since it's not going through the Celery Worker.

Usage:
    cd apps/api
    uv run python scripts/process_kb_document_once.py <doc_id>
    uv run python scripts/process_kb_document_once.py <doc_id> --force

    --force: reprocess even if already INDEXED (cleans old data first)
"""

import argparse
import asyncio
import sys

# Ensure all ORM models are registered before any DB access
import interview_api.modules.models  # noqa: F401

from interview_api.core.config import settings
from interview_api.infrastructure.db.engine import engine
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.infrastructure.embedding.provider import (
    OpenAICompatibleEmbeddingProvider,
)
from interview_api.infrastructure.milvus.provider import MilvusVectorStoreProvider
from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
from interview_api.modules.kb.ingestion_service import KbIngestionService
from interview_api.modules.kb.repository import KbDocumentRepository


async def process_one(document_id: int, force: bool = False):
    storage = MinioObjectStorageProvider()
    embedding = OpenAICompatibleEmbeddingProvider()
    vector_store = MilvusVectorStoreProvider(embedding_dim=settings.embedding_dim)

    async with async_session_factory() as db:
        repo = KbDocumentRepository(db)
        doc = await repo.get_by_id(document_id)

        if doc is None:
            print(f"ERROR: Document {document_id} not found in kb_documents")
            return 1

        print(f"Document {document_id}: title={doc.title!r}, status={doc.status}")

        if doc.status == "INDEXED" and not force:
            print("Already INDEXED. Use --force to reprocess.")
            return 0

        if doc.status == "PROCESSING":
            print("Status is PROCESSING — may be stuck. Forcing reprocess.")
            force = True

        # Mark processing started (manual lifecycle management, since no Worker)
        await repo.mark_processing_started(document_id)
        await db.commit()
        print("[1/6] Status -> PROCESSING")

        try:
            service = KbIngestionService(db, storage, embedding, vector_store)
            chunk_count = await service.process_document(document_id)

            await repo.mark_processing_finished(
                document_id, "INDEXED", chunk_count=chunk_count
            )
            await db.commit()
            print(f"[6/6] Status -> INDEXED ({chunk_count} chunks)")

        except Exception as e:
            await db.rollback()

            # Mark failed in fresh session
            async with async_session_factory() as db2:
                repo2 = KbDocumentRepository(db2)
                await repo2.mark_processing_finished(
                    document_id, "FAILED", error_message=str(e)[:2000]
                )
                await db2.commit()

            print(f"FAILED: {e}")
            return 1

    print(f"SUCCESS: document_id={document_id}")
    return 0


async def main():
    parser = argparse.ArgumentParser(description="Process a single KB document")
    parser.add_argument("doc_id", type=int, help="Document ID to process")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess even if already INDEXED (cleans old data first)",
    )
    args = parser.parse_args()

    code = await process_one(args.doc_id, force=args.force)
    await engine.dispose()
    sys.exit(code)


if __name__ == "__main__":
    asyncio.run(main())
