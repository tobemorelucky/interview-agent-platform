"""Versioned contracts shared by live and replay public-data tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field


class ProviderMode(StrEnum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"


class ToolResultStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"


class ToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_task_id: int
    correlation_id: str | None = Field(default=None, max_length=255)


class SourceCandidatePayload(BaseModel):
    """Search observation that creates/reuses Source, never SourceSnapshot."""

    model_config = ConfigDict(extra="forbid")

    canonical_url: str
    title: str | None = None
    source_type: str = "PUBLIC_WEB"
    metadata: dict[str, Any] = Field(default_factory=dict)


class FetchedContentPayload(BaseModel):
    """Successfully fetched body content eligible for SourceSnapshot persistence."""

    model_config = ConfigDict(extra="forbid")

    canonical_url: str
    title: str | None = None
    source_type: str = "PUBLIC_WEB"
    fetched_at: datetime
    published_at: datetime | None = None
    content: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ToolResultStatus = ToolResultStatus.SUCCEEDED
    data: dict[str, Any] = Field(default_factory=dict)
    source_candidates: list[SourceCandidatePayload] = Field(default_factory=list)
    fetched_contents: list[FetchedContentPayload] = Field(default_factory=list)


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    timeout_seconds: float
    redacted_fields: frozenset[str] = field(default_factory=frozenset)

