"""Strict agent artifacts: no business state or unvalidated evidence excerpts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Action = Literal["FOLLOW_UP", "DEEP_DIVE", "NEXT", "ADJUST_DIFFICULTY", "COMPLETE"]
Source = Literal["PUBLIC_QUESTION", "JOB_REQUIREMENT", "CANDIDATE_PROFILE", "FOLLOW_UP", "DYNAMIC_GENERATED"]
Difficulty = Literal["EASY", "MEDIUM", "HARD"]
DIMENSIONS = ("technical_correctness", "coverage", "depth", "relevance", "communication")


class StrictArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class QuestionArtifact(StrictArtifact):
    question: str = Field(min_length=3, max_length=3000)
    expected_points: list[str] = Field(min_length=1, max_length=15)
    dimension: str = Field(min_length=1, max_length=50)
    difficulty: Difficulty
    source_type: Source
    source_question_id: int | None = None
    job_requirement_id: int | None = None
    candidate_fact_ids: list[int] = Field(default_factory=list)
    candidate_evidence_ids: list[str] = Field(default_factory=list)


class MainArtifact(StrictArtifact):
    recommendation: Action
    question: QuestionArtifact | None = None


class EvaluationArtifact(StrictArtifact):
    technical_correctness: int = Field(ge=1, le=5)
    coverage: int = Field(ge=1, le=5)
    depth: int = Field(ge=1, le=5)
    relevance: int = Field(ge=1, le=5)
    communication: int = Field(ge=1, le=5)
    evaluation: str = Field(min_length=1, max_length=3000)
    covered_points: list[str]
    missing_points: list[str]
    factual_or_reasoning_issues: list[str]
    follow_up_focus: list[str]
    next_action_recommendation: Action
