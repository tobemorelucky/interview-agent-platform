from datetime import datetime

from pydantic import BaseModel, ConfigDict


# ── Request Schemas ──


class CreateSessionRequest(BaseModel):
    title: str | None = None


class BindResumeRequest(BaseModel):
    resume_id: int


class SendMessageRequest(BaseModel):
    content: str


# ── Response Schemas ──


class InterviewSessionResponse(BaseModel):
    id: int
    user_id: int
    resume_id: int | None = None
    title: str | None = None
    status: str
    turn_count: int
    last_compressed_turn: int
    resume_filename: str | None = None
    resume_status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class InterviewMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    metadata_json: dict | None = None
    turn_index: int
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class InterviewSessionDetailResponse(InterviewSessionResponse):
    memory_summary: str | None = None
    messages: list[InterviewMessageResponse] = []
