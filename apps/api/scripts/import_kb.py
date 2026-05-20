"""Batch import knowledge documents from a local directory.

Reads .md / .txt files from a directory, uploads them to MinIO,
chunks, embeds, and indexes into Milvus.

Usage:
    cd apps/api
    uv run python scripts/import_kb.py --dir /path/to/docs
    uv run python scripts/import_kb.py --dir /path/to/docs --retry-failed

Idempotency:
    - Computes SHA-256 content hash for each file.
    - Skips files already imported with status UPLOADED / PROCESSING / INDEXED.
    - FAILED files are skipped by default; use --retry-failed to reprocess them.
"""

import argparse
import asyncio
import sys
from pathlib import Path

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


async def import_file(
    file_path: Path,
    storage: MinioObjectStorageProvider,
    embedding: OpenAICompatibleEmbeddingProvider,
    vector_store: MilvusVectorStoreProvider,
    retry_failed: bool = False,
) -> str:
    """Import a single file in its own transaction. Returns status."""
    file_bytes = file_path.read_bytes()
    filename = file_path.name
    ext = file_path.suffix.lower()
    source_type = "markdown" if ext in (".md", ".markdown") else "txt"
    content_hash = KbIngestionService.compute_hash(file_bytes)

    async with async_session_factory() as db:
        service = KbIngestionService(db, storage, embedding, vector_store)
        try:
            existing = await service.kb_repo.get_by_content_hash(content_hash)

            if existing is not None:
                if existing.status == "FAILED" and retry_failed:
                    print(f"RETRY: {filename} (was FAILED)")
                    await service.retry_failed(existing.id)
                    await db.commit()
                    return "retried"
                else:
                    print(f"SKIP: {filename} (status={existing.status})")
                    return "skipped"

            doc = await service.upload_and_create_document(
                filename=filename,
                file_bytes=file_bytes,
                source_type=source_type,
                uploaded_by=None,
            )
            await service.process_document(doc.id)
            await db.commit()
            print(f"IMPORTED: {filename} -> doc_id={doc.id}")
            return "imported"

        except Exception as e:
            await db.rollback()
            print(f"FAILED: {filename} - {e}")
            return "failed"


async def main():
    parser = argparse.ArgumentParser(description="Batch import KB documents")
    parser.add_argument("--dir", required=True, help="Directory containing .md/.txt files")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Reprocess documents that previously failed",
    )
    args = parser.parse_args()

    docs_dir = Path(args.dir)
    if not docs_dir.is_dir():
        print(f"ERROR: '{args.dir}' is not a directory")
        sys.exit(1)

    ext_list = {".md", ".markdown", ".txt"}
    files = sorted(
        [f for f in docs_dir.iterdir() if f.is_file() and f.suffix.lower() in ext_list]
    )
    if not files:
        print(f"No .md/.txt files found in '{args.dir}'")
        sys.exit(0)

    print(f"Found {len(files)} file(s) in '{args.dir}'")
    if args.retry_failed:
        print("--retry-failed enabled: will reprocess FAILED documents")

    # Providers are stateless — create once, reuse across files
    storage = MinioObjectStorageProvider()
    embedding = OpenAICompatibleEmbeddingProvider()
    vector_store = MilvusVectorStoreProvider(embedding_dim=settings.embedding_dim)

    stats = {"imported": 0, "skipped": 0, "retried": 0, "failed": 0}
    for file_path in files:
        result = await import_file(
            file_path, storage, embedding, vector_store,
            retry_failed=args.retry_failed,
        )
        stats[result] += 1

    print()
    print(f"Done. Imported: {stats['imported']}, Skipped: {stats['skipped']}, "
          f"Retried: {stats['retried']}, Failed: {stats['failed']}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
