from datetime import datetime

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    title: str | None = None


class SessionResponse(BaseModel):
    id: int
    user_id: int
    title: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ChatMessageResponse(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    citations_json: dict | list | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class SessionDetailResponse(SessionResponse):
    messages: list[ChatMessageResponse] = []


class ChatStreamRequest(BaseModel):
    session_id: int
    message: str


class CitationItem(BaseModel):
    chunk_id: int
    doc_id: int
    title: str
    content: str
