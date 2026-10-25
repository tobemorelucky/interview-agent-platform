"""Model-owned facts and Evidence IDs; source identity and counts are forbidden."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from interview_api.modules.experience.agents.schemas import (
    ExtractionExperience, ExtractionQuestion, RoutingResult, RoutingQuestionResult, ReliabilityResult,
)


EvidenceID = Annotated[str, Field(pattern=r"^ev_[a-f0-9]{24}$")]


class FragmentInterviewQuestion(BaseModel):
    """Selection/classification is model-owned; raw quotes/offsets are not."""
    model_config = ConfigDict(extra="forbid")
    fragment_id: EvidenceID
    kind: Literal["QUESTION", "REPORTED_TOPIC"]
    normalized_question: str | None = Field(default=None, min_length=5, max_length=200)
    question_type: Literal["TECHNICAL", "PROJECT", "ALGORITHM", "SYSTEM_DESIGN", "HR", "OTHER"] | None = None
    confidence: float = Field(ge=0, le=1)


class FragmentInterviewExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_interview_experience: bool
    company: str | None = None
    position: str | None = None
    round_name: str | None = None
    experience_summary: str = Field(default="", max_length=2000)
    questions: list[FragmentInterviewQuestion] = Field(default_factory=list, max_length=20)
    evidence_ids: list[EvidenceID] = Field(min_length=1)
    identity_evidence_ids: list[EvidenceID] = Field(default_factory=list)
    source_quality_note: str | None = None
    extraction_confidence: float = Field(ge=0, le=1)


class SupportedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1)
    evidence_ids: list[EvidenceID] = Field(min_length=1)


class RequirementExtraction(SupportedStatement):
    requirement_type: str | None = Field(default=None, max_length=64)
    importance: str | None = Field(default=None, max_length=32)


class JobJDExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_name: str = Field(min_length=1, max_length=255)
    job_title: str = Field(min_length=1, max_length=255)
    identity_evidence_ids: list[EvidenceID] = Field(min_length=1)
    jd_evidence_ids: list[EvidenceID] = Field(min_length=1)
    requirements: list[RequirementExtraction] = Field(min_length=1)
    # Optional literal public metadata; old extractions remain readable.
    attributes: list["JobAttribute"] = Field(default_factory=list)


class JobAttribute(SupportedStatement):
    field: Literal["RESPONSIBILITY", "LOCATION", "PUBLISHED_AT", "JOB_NUMBER"]


class PublicInterviewQuestion(ExtractionQuestion):
    model_config = ConfigDict(extra="forbid")
    evidence: Literal[None] = None
    evidence_ids: list[EvidenceID] = Field(min_length=1)


class PublicInterviewExtraction(ExtractionExperience):
    model_config = ConfigDict(extra="forbid")
    questions: list[PublicInterviewQuestion] = Field(default_factory=list)
    evidence_ids: list[EvidenceID] = Field(min_length=1)
    identity_evidence_ids: list[EvidenceID] = Field(default_factory=list)


class ResearchQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suitable: bool
    risk_flags: list[str] = Field(default_factory=list)


class PublicRoutingQuestion(RoutingQuestionResult):
    model_config = ConfigDict(extra="forbid")


class PublicRoutingResult(RoutingResult):
    model_config = ConfigDict(extra="forbid")
    question_results: list[PublicRoutingQuestion] = Field(default_factory=list)


class PublicReliabilityResult(ReliabilityResult):
    model_config = ConfigDict(extra="forbid")
