"""User decisions, not automatic Agent consent."""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProposalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=2000)


class ProposalCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("content", "reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("修正内容和原因不能为空")
        return value
