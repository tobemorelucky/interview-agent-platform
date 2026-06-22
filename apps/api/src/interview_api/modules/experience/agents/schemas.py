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


class ExperienceSourceExtractRequest(BaseModel):
    force: bool = False
