"""Model-owned facts and Evidence IDs; source identity and counts are forbidden."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from interview_api.modules.experience.agents.schemas import (
    ExtractionExperience, ExtractionQuestion, RoutingResult, RoutingQuestionResult, ReliabilityResult,
)


EvidenceID = Annotated[str, Field(pattern=r"^ev_[a-f0-9]{24}$")]


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
