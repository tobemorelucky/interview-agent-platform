"""Phase 4: Experience collection schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


# ── Keyword Preset ──

class ExperienceKeywordPresetCreate(BaseModel):
    preset_type: str = Field(..., description="COMPANY / JOB / PLATFORM")
    name: str = Field(..., max_length=128)
    aliases_json: list[str] = Field(default_factory=list)
    enabled: bool = True


class ExperienceKeywordPresetUpdate(BaseModel):
    name: str | None = Field(None, max_length=128)
    aliases_json: list[str] | None = None
    enabled: bool | None = None


class ExperienceKeywordPresetRead(BaseModel):
    id: int
    preset_type: str
    name: str
    aliases_json: list[str] = []
    enabled: bool
    created_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Collection Task ──

class ExperienceCollectionTaskCreate(BaseModel):
    time_window_hours: int = Field(default=24, gt=0, le=720)
    job_keywords_json: list[str] = Field(default_factory=list)
    company_keywords_json: list[str] = Field(default_factory=list)
    platforms_json: list[str] = Field(default_factory=list)
    max_results: int = Field(default=20, ge=1, le=100)
    review_mode: str = Field(default="MANUAL", pattern="^(MANUAL|AUTO_PUBLISH)$")
    write_to_question_db: bool = False
    write_to_vector_index: bool = False
    update_public_summary: bool = True


class ExperienceCollectionTaskRead(BaseModel):
    id: int
    created_by: int | None = None
    time_window_hours: int
    job_keywords_json: list = []
    company_keywords_json: list = []
    platforms_json: list = []
    max_results: int
    review_mode: str
    write_to_question_db: bool
    write_to_vector_index: bool
    update_public_summary: bool
    status: str
    progress: int
    found_url_count: int
    fetched_count: int
    extracted_count: int
    question_count: int
    approved_count: int
    failed_count: int
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class ExperienceCollectionTaskListResponse(BaseModel):
    items: list[ExperienceCollectionTaskRead]
    total: int


# ── Source Item ──

class ExperienceSourceItemRead(BaseModel):
    id: int
    task_id: int
    source_url: str
    normalized_url_hash: str
    platform: str | None = None
    title: str | None = None
    fetched_at: datetime | None = None
    fetch_status: str
    extract_status: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Interview Experience ──

class InterviewExperienceRead(BaseModel):
    id: int
    source_item_id: int | None = None
    company: str | None = None
    position: str | None = None
    job_direction: str | None = None
    interview_round: str | None = None
    experience_date: str | None = None
    summary: str | None = None
    tags_json: list = []
    reliability_score: float | None = None
    reliability_level: str | None = None
    review_status: str
    created_at: datetime | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Interview Question ──

class InterviewQuestionRead(BaseModel):
    id: int
    experience_id: int | None = None
    question: str
    standard_answer: str | None = None
    answer_source: str
    category: str | None = None
    difficulty: str | None = None
    company: str | None = None
    position: str | None = None
    reliability_score: float | None = None
    tags_json: list = []
    review_status: str
    index_status: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Recent Summary ──

class ExperienceRecentSummaryRead(BaseModel):
    id: int
    time_window_hours: int
    job_keywords_json: list = []
    company_keywords_json: list = []
    summary_json: dict = {}
    generated_by_task_id: int | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
