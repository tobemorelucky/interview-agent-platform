"""Coach output never contains computable scores/counts or invented Question IDs."""
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, RootModel

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


class CoachTrainingSuggestion(StrictArtifact):
    """An advisory next-training idea, never an instruction to create a session.

    The service re-derives legality from capability memory and the existing
    training rules; a suggestion that fails is dropped and recorded, never
    silently upgraded into a plan.
    """
    mode: Literal["FULL", "WEAKNESS", "RECHECK"]
    knowledge_id: str = Field(min_length=3, max_length=150)
    reason: str = Field(min_length=3, max_length=600)
    supporting_evaluation_ids: list[EvaluationRef] = Field(min_length=1, max_length=20)
    scope: Literal["CURRENT_JOB_TARGET", "GENERAL_SKILL"] = "CURRENT_JOB_TARGET"


class HarnessCoachArtifact(CoachArtifact):
    """The OFF path keeps `CoachArtifact` byte-for-byte; only the harness adds this."""
    suggested_training: CoachTrainingSuggestion | None = None


# ── One Coach turn: call a tool, or finish ──


class ReadEvaluationDetailAction(StrictArtifact):
    action: Literal["read_evaluation_detail"]
    evaluation_id: int = Field(ge=1, le=2**31 - 1)


class ReadCapabilityHistoryAction(StrictArtifact):
    action: Literal["read_capability_history"]
    knowledge_id: str = Field(min_length=3, max_length=150)


class FinishAction(StrictArtifact):
    action: Literal["final"]
    artifact: HarnessCoachArtifact


CoachAction = Annotated[
    Union[ReadEvaluationDetailAction, ReadCapabilityHistoryAction, FinishAction],
    Field(discriminator="action"),
]


class CoachTurn(RootModel[CoachAction]):
    """A single discriminated turn; `action` is the only branch selector."""


class ReportGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    regenerate: bool = False
    request_key: str | None = Field(default=None, min_length=1, max_length=100)
