from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupportedStatement(StrictModel):
    statement: str = Field(min_length=1, max_length=5000)
    evidence_ids: list[str] = Field(min_length=1, max_length=30)


class NamedFact(SupportedStatement):
    label: str = Field(min_length=1, max_length=300)


class SkillFact(NamedFact):
    evidence_state: Literal["MENTIONED", "DEMONSTRATED", "STRONG_EVIDENCE"] = Field(
        default="MENTIONED", validation_alias=AliasChoices("proposed_evidence_state", "evidence_state"),
        description="Proposed state only. Deterministic resolver can lower, never raise, the persisted state.")


class CandidateProfileDelta(StrictModel):
    candidate_summary: SupportedStatement | None = None
    skills: list[SkillFact] = Field(default_factory=list, max_length=100)
    projects: list[NamedFact] = Field(default_factory=list, max_length=50)
    education: list[NamedFact] = Field(default_factory=list, max_length=30)
    work_experience: list[NamedFact] = Field(default_factory=list, max_length=50)
    internship_experience: list[NamedFact] = Field(default_factory=list, max_length=50)
    strengths: list[SupportedStatement] = Field(default_factory=list, max_length=30)
    # These are requested information topics, not negative capability claims.
    unknowns: list[str] = Field(default_factory=list, max_length=50)


class CorrectionRequest(StrictModel):
    base_version_id: int
    field_path: str = Field(max_length=200)
    new_value: str = Field(min_length=1, max_length=5000)
    reason: str = Field(min_length=1, max_length=1000)


class GenerationRequest(StrictModel):
    resume_id: int
    regenerate: bool = False
