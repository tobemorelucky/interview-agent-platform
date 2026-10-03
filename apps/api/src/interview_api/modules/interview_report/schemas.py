"""Coach output never contains computable scores/counts or invented Question IDs."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

EvaluationRef = Annotated[str, Field(pattern=r"^evaluation:[1-9][0-9]*$")]


class StrictArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CoachFinding(StrictArtifact):
    statement: str = Field(min_length=3, max_length=2000)
    supporting_evaluation_ids: list[EvaluationRef] = Field(min_length=1, max_length=100)


class CoachMemoryProposal(StrictArtifact):
    proposal_type: Literal["GOAL", "PREFERENCE", "EXPERIENCE", "SKILL_OBSERVATION", "PREPARATION_FOCUS"]
    content: str = Field(min_length=3, max_length=2000)
    supporting_evaluation_ids: list[EvaluationRef] = Field(min_length=1, max_length=100)


class CoachArtifact(StrictArtifact):
    overall_summary: str = Field(min_length=3, max_length=3000)
    strengths: list[CoachFinding] = Field(max_length=20)
    weaknesses: list[CoachFinding] = Field(max_length=20)
    recurring_issues: list[CoachFinding] = Field(max_length=20)
    job_alignment: list[CoachFinding] = Field(max_length=20)
    recommended_actions: list[str] = Field(max_length=20)
    practice_plan: list[str] = Field(max_length=20)
    memory_proposals: list[CoachMemoryProposal] = Field(max_length=20)


class ReportGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    regenerate: bool = False
    request_key: str | None = Field(default=None, min_length=1, max_length=100)
