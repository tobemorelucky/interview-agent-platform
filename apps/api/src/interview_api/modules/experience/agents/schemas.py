"""Structured outputs for the experience Extraction Agent."""

from typing import Literal

from pydantic import BaseModel, Field


QuestionType = Literal[
    "TECHNICAL",
    "PROJECT",
    "ALGORITHM",
    "SYSTEM_DESIGN",
    "HR",
    "OTHER",
]

AnswerSource = Literal["ORIGINAL", "LLM_GENERATED", "HYBRID", "NONE"]

JobDirection = Literal[
    "BACKEND",
    "FRONTEND",
    "AI_APPLICATION",
    "ALGORITHM",
    "DATA",
    "TEST",
    "PRODUCT",
    "OTHER",
]

RoutingQuestionType = Literal[
    "BASIC_KNOWLEDGE",
    "PROJECT_DEEP_DIVE",
    "SYSTEM_DESIGN",
    "ALGORITHM",
    "SCENARIO",
    "HR",
    "OTHER",
]

Difficulty = Literal["EASY", "MEDIUM", "HARD", "UNKNOWN"]
PublishRecommendation = Literal["APPROVE", "NEEDS_REVIEW", "REJECT"]
ReviewStatus = Literal["WAITING_REVIEW", "NEEDS_MANUAL_CHECK", "REJECTED"]
RecommendedAction = Literal["REVIEW", "REJECT", "RETRY_EXTRACTION"]


class ExtractionQuestion(BaseModel):
    question: str = Field(..., min_length=1)
    question_type: QuestionType | None = None
    original_answer: str | None = None
    standard_answer: str | None = None
    answer_source: AnswerSource = "NONE"
    evidence: str | None = None
    confidence: float = Field(..., ge=0, le=1)


class ExtractionExperience(BaseModel):
    is_interview_experience: bool
    company: str | None = None
    position: str | None = None
    round_name: str | None = None
    experience_summary: str = ""
    questions: list[ExtractionQuestion] = Field(default_factory=list)
    source_quality_note: str | None = None
    extraction_confidence: float = Field(..., ge=0, le=1)


class RoutingQuestionResult(BaseModel):
    question_index: int = Field(..., ge=0)
    normalized_question: str
    job_direction: JobDirection | None = None
    technical_categories: list[str] = Field(default_factory=list)
    question_type: RoutingQuestionType = "OTHER"
    difficulty: Difficulty = "UNKNOWN"
    target_banks: list[str] = Field(default_factory=list)
    should_index: bool = True
    routing_confidence: float = Field(..., ge=0, le=1)


class RoutingResult(BaseModel):
    overall_job_direction: JobDirection | None = None
    company: str | None = None
    position: str | None = None
    question_results: list[RoutingQuestionResult] = Field(default_factory=list)
    suggested_tags: list[str] = Field(default_factory=list)
    routing_summary: str = ""
    routing_confidence: float = Field(..., ge=0, le=1)


class ReliabilityResult(BaseModel):
    is_reliable: bool
    reliability_score: float = Field(..., ge=0, le=1)
    content_quality_score: float = Field(..., ge=0, le=1)
    source_quality_score: float = Field(..., ge=0, le=1)
    spam_risk_score: float = Field(..., ge=0, le=1)
    ad_or_training_risk: bool = False
    outdated_risk: bool = False
    hallucination_risk_note: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    publish_recommendation: PublishRecommendation = "NEEDS_REVIEW"
    reason: str = ""


class QualityGateResult(BaseModel):
    passed: bool
    review_status: ReviewStatus
    reasons: list[str] = Field(default_factory=list)
    question_count: int = 0
    indexable_question_count: int = 0
    reliability_score: float = Field(..., ge=0, le=1)
    recommended_action: RecommendedAction


class ExperienceSourceExtractRequest(BaseModel):
    force: bool = False
