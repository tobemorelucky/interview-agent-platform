"""Initialize Milvus collections for knowledge base.

Creates kb_chunks_v1 collection with alias kb_chunks_current.
Idempotent: safe to run multiple times.

Usage:
    cd apps/api
    uv run python scripts/init_milvus.py
"""

import asyncio
import os

from interview_api.core.config import settings
from interview_api.infrastructure.milvus.provider import MilvusVectorStoreProvider


async def main():
    dim = int(os.getenv("EMBEDDING_DIM", "768"))

    provider = MilvusVectorStoreProvider(embedding_dim=dim)

    collection_name = "kb_chunks_v1"
    alias = "kb_chunks_current"

    print(f"Ensuring collection '{collection_name}' ...")
    col = provider.ensure_collection(
        collection_name,
        description="Interview knowledge base chunks",
    )
    print(f"Collection '{collection_name}' ready (entities: {col.num_entities}).")

    print(f"Ensuring alias '{alias}' -> '{collection_name}' ...")
    provider.ensure_alias(collection_name, alias)
    print(f"Alias '{alias}' ready.")


if __name__ == "__main__":
    asyncio.run(main())
