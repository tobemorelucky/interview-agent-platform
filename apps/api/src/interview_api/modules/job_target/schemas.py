"""Request and extraction schemas for private JD targets."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Importance = Literal["REQUIRED", "PREFERRED", "UNKNOWN"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateJobTargetRequest(StrictModel):
    # The pasted text is untrusted data with a hard ceiling; it is never treated
    # as instructions and never merged into a system prompt.
    raw_text: str = Field(min_length=80, max_length=20000)
    company_name: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    source_reference: str | None = Field(default=None, max_length=500)


class RequirementInput(StrictModel):
    statement: str = Field(min_length=1, max_length=300)
    importance: Importance = "UNKNOWN"
    evidence: str | None = Field(default=None, max_length=300)
    topic: str | None = Field(default=None, max_length=60)


class UpdateJobTargetRequest(StrictModel):
    requirements: list[RequirementInput] = Field(min_length=1, max_length=30)


class ExtractedRequirement(StrictModel):
    statement: str = Field(min_length=1, max_length=300)
    importance: Importance
    # A verbatim span of the user's own text; anything else is discarded.
    evidence: str = Field(min_length=1, max_length=300)
    topic: str | None = Field(default=None, max_length=60)


class ExtractedJobDescription(StrictModel):
    company_name: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    responsibilities: list[str] = Field(default_factory=list, max_length=20)
    requirements: list[ExtractedRequirement] = Field(default_factory=list, max_length=30)
