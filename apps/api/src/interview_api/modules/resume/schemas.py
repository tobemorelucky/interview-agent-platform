from datetime import datetime

from pydantic import BaseModel, Field


# ── Resume ──

class ResumeResponse(BaseModel):
    id: int
    user_id: int
    filename: str
    file_type: str
    file_size: int | None = None
    status: str
    error_message: str | None = None
    task_id: str | None = None
    processing_stage: str | None = None
    stage_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ResumeDetailResponse(ResumeResponse):
    raw_text_preview: str | None = None


class ResumeListResponse(BaseModel):
    items: list[ResumeResponse]
    total: int


# ── Report sub-schemas ──

class BasicInfo(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    years_of_experience: float | None = None
    current_role: str = ""
    target_role: str = ""


class Education(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""
    start_year: int | None = None
    end_year: int | None = None


class Skills(BaseModel):
    languages: list[str] = []
    frameworks: list[str] = []
    databases: list[str] = []
    tools: list[str] = []
    ai_ml: list[str] = []
    other: list[str] = []


class Project(BaseModel):
    name: str = ""
    role: str = ""
    duration: str = ""
    description: str = ""
    tech_stack: list[str] = []
    key_contributions: list[str] = []
    quantitative_results: list[str] = []


class Internship(BaseModel):
    company: str = ""
    role: str = ""
    duration: str = ""
    responsibilities: list[str] = []
    tech_stack: list[str] = []


class RiskPoint(BaseModel):
    area: str = ""
    description: str = ""
    severity: str = "MEDIUM"  # HIGH | MEDIUM | LOW


class ResumeSummary(BaseModel):
    basic_info: BasicInfo = Field(default_factory=BasicInfo)
    education: list[Education] = []
    skills: Skills = Field(default_factory=Skills)
    projects: list[Project] = []
    internships: list[Internship] = []
    publications: list[dict] = []
    highlights: list[str] = []
    risk_points: list[RiskPoint] = []


# ── Retrieval sub-schemas ──

class RetrievalQueryItem(BaseModel):
    query: str
    target: str


class RetrievalHit(BaseModel):
    chunk_id: int
    doc_id: int
    title: str
    preview: str
    score: float
    source_type: str


class RetrievedQueryResult(BaseModel):
    query: str
    target: str
    hit_count: int
    top_hits: list[RetrievalHit] = []


class RetrievedContext(BaseModel):
    total_hits: int = 0
    queries: list[RetrievedQueryResult] = []


# ── Question sub-schemas ──

class Evidence(BaseModel):
    title: str = ""
    preview: str = ""
    score: float = 0.0
    source_type: str = ""
    chunk_id: int | None = None
    doc_id: int | None = None


class InterviewQuestion(BaseModel):
    question: str
    category: str = "general"
    difficulty: str = "MEDIUM"  # EASY | MEDIUM | HARD
    reason: str = ""
    source: str = "LLM_GENERATED"  # KB_RETRIEVED | LLM_GENERATED | HYBRID
    suggested_answer: str = ""
    follow_up_questions: list[str] = []
    evidence: Evidence | None = None


class InterviewQuestions(BaseModel):
    questions: list[InterviewQuestion] = []


class InterviewSuggestions(BaseModel):
    strengths: list[str] = []
    weaknesses_to_prepare: list[str] = []
    interview_tips: list[str] = []


# ── Full report response ──

class ResumeReportResponse(BaseModel):
    id: int
    resume_id: int
    summary_json: dict | None = None
    retrieval_queries_json: dict | None = None
    retrieved_context_json: dict | None = None
    questions_json: dict | None = None
    suggestions_json: dict | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Request schemas ──

class ResumeListRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
