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
    current_question_index: int = 0
    question_generation_status: str = "PENDING"
    question_generation_error: str | None = None
    total_questions: int = 0
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
    questions: list["InterviewSessionQuestionResponse"] = []


# ── Question Schemas ──


class InterviewSessionQuestionResponse(BaseModel):
    id: int
    session_id: int
    question_index: int
    question: str
    standard_answer: str | None = None  # None when masked
    dimension: str | None = None
    difficulty: str | None = None
    source: str = "LLM_GENERATED"
    evidence_json: dict | None = None
    follow_up_count: int = 0
    status: str = "PENDING"
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class StartInterviewResponse(BaseModel):
    type: str = "QUESTION"
    question_id: int
    question_index: int
    total_questions: int
    question: str
    dimension: str | None = None
    difficulty: str | None = None
    source: str = "LLM_GENERATED"
    evidence: dict | None = None


class RevealAnswerResponse(BaseModel):
    question_id: int
    question: str
    standard_answer: str | None = None
    source: str
    evidence_json: dict | None = None


class QuestionListResponse(BaseModel):
    questions: list[InterviewSessionQuestionResponse]
    total: int
    current_question_index: int
    question_generation_status: str
