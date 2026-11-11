from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from interview_api.modules.interview.skill_engine import SkillId, RoundId, StyleId
from interview_api.modules.interview.system_design import DesignPlan


# ── Request Schemas ──


class CreateSessionRequest(BaseModel):
    title: str | None = None
    company_id: int = Field(gt=0)
    job_id: int = Field(gt=0)
    candidate_profile_version_id: int | None = Field(default=None, gt=0)
    job_description_version_id: int | None = Field(default=None, gt=0)
    question_count: int = Field(default=20, ge=1, le=40)
    interviewer_skill_id: SkillId | None = None
    interview_round: RoundId = "technical_screen"
    interviewer_style: StyleId = "standard"
    interviewer_selection_source: str = Field(default="USER_SELECTED", pattern=r"^(AUTO_RECOMMENDED|USER_SELECTED)$")
    design_interview: bool = False
    adaptive_interview: bool = False
    # Preserve the historical training request fingerprint when omitted/OFF.
    main_harness: bool = Field(default=False, strict=True, exclude_if=lambda value: not value)
    training_mode: Literal['FULL', 'WEAKNESS', 'RECHECK'] | None = None
    training_knowledge_id: str | None = Field(default=None, max_length=150)
    training_request_key: str | None = Field(default=None, min_length=8, max_length=100)


class SkillRecommendationRequest(BaseModel):
    job_ids: list[int] = Field(min_length=1, max_length=50)


class SetTargetPositionRequest(BaseModel):
    target_position: str
    interview_mode: str = "comprehensive"
    question_count: int = 20


class BindResumeRequest(BaseModel):
    resume_id: int


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    question_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=100)
    design_plan: DesignPlan | None = None


class ConsolidateMemoryRequest(BaseModel):
    force: bool = False


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
    target_position: str | None = None
    target_position_confirmed: bool = False
    interview_mode: str = "comprehensive"
    interview_plan_json: dict | None = None
    planner_trace_json: dict | None = None
    question_count: int = 20
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
    parent_question_id: int | None = None
    is_dynamic: bool = False
    planned_order: int | None = None
    answer_summary: str | None = None
    missing_points_json: dict | None = None
    evaluation_json: dict | None = None
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
