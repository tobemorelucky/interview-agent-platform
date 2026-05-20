from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.kb.models import KbChunk, KbDocument


class KbDocumentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, doc_id: int) -> KbDocument | None:
        result = await self.db.execute(
            select(KbDocument).where(KbDocument.id == doc_id)
        )
        return result.scalar_one_or_none()

    async def get_by_content_hash(self, content_hash: str) -> KbDocument | None:
        result = await self.db.execute(
            select(KbDocument).where(KbDocument.content_hash == content_hash)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        title: str,
        source_type: str,
        storage_key: str | None = None,
        content_hash: str | None = None,
        uploaded_by: int | None = None,
    ) -> KbDocument:
        doc = KbDocument(
            title=title,
            source_type=source_type,
            storage_key=storage_key,
            content_hash=content_hash,
            uploaded_by=uploaded_by,
        )
        self.db.add(doc)
        await self.db.flush()
        return doc

    async def update_status(
        self,
        doc_id: int,
        status: str,
        error_message: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        values = {"status": status}
        if error_message is not None:
            values["error_message"] = error_message
        if chunk_count is not None:
            values["chunk_count"] = chunk_count
        if status == "INDEXED":
            from datetime import datetime, timezone
            values["indexed_at"] = datetime.now(timezone.utc)

        await self.db.execute(
            update(KbDocument).where(KbDocument.id == doc_id).values(**values)
        )
        await self.db.flush()

    async def list_all(self, offset: int = 0, limit: int = 50) -> list[KbDocument]:
        result = await self.db.execute(
            select(KbDocument)
            .order_by(KbDocument.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_all(self) -> int:
        from sqlalchemy import func as sql_func
        result = await self.db.execute(
            select(sql_func.count()).select_from(KbDocument)
        )
        return result.scalar() or 0


class KbChunkRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_batch(self, chunks: list[dict]) -> list[KbChunk]:
        entities = [KbChunk(**c) for c in chunks]
        self.db.add_all(entities)
        await self.db.flush()
        return entities

    async def get_by_document_id(self, document_id: int) -> list[KbChunk]:
        result = await self.db.execute(
            select(KbChunk)
            .where(KbChunk.document_id == document_id)
            .order_by(KbChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def mark_indexed(self, chunk_ids: list[int]) -> None:
        await self.db.execute(
            update(KbChunk)
            .where(KbChunk.id.in_(chunk_ids))
            .values(embedding_status="INDEXED")
        )
        await self.db.flush()
