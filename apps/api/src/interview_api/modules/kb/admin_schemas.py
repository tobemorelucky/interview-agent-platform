from datetime import datetime

from pydantic import BaseModel, Field


class KbDocumentResponse(BaseModel):
    id: int
    title: str
    source_type: str
    status: str
    chunk_count: int
    error_message: str | None = None
    uploaded_by: int | None = None
    created_at: datetime | None = None
    indexed_at: datetime | None = None

    model_config = {"from_attributes": True}


class KbDocumentDetailResponse(KbDocumentResponse):
    chunks: list["KbChunkResponse"] = []


class KbChunkResponse(BaseModel):
    id: int
    document_id: int
    chunk_index: int
    content: str
    embedding_status: str
    token_count: int | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class KbDocumentListResponse(BaseModel):
    items: list[KbDocumentResponse]
    total: int
