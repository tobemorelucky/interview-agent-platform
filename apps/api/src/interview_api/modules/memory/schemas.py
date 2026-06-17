"""Pydantic schemas for user memory APIs."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from interview_api.modules.memory.policies import (
    MEMORY_SCOPES,
    MEMORY_STATUSES,
    MEMORY_TYPES,
    MEMORY_VISIBILITIES,
    SOURCE_TYPES,
)


class UserMemoryItemCreate(BaseModel):
    memory_type: str
    scope: str = "INTERVIEW"
    key: str | None = Field(None, max_length=200)
    content: str = Field(..., min_length=1)
    summary: str | None = None
    metadata_json: dict | None = None
    confidence: float = Field(default=0.8, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    source_type: str | None = None
    source_id: int | None = None
    visibility: str = "PRIVATE"
    expires_at: datetime | None = None

    @field_validator("memory_type")
    @classmethod
    def validate_memory_type(cls, value: str) -> str:
        return _validate_choice(value, MEMORY_TYPES, "memory_type")

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: str) -> str:
        return _validate_choice(value, MEMORY_SCOPES, "scope")

    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, value: str) -> str:
        return _validate_choice(value, MEMORY_VISIBILITIES, "visibility")

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_choice(value, SOURCE_TYPES, "source_type")


class UserMemoryItemUpdate(BaseModel):
    memory_type: str | None = None
    scope: str | None = None
    key: str | None = Field(None, max_length=200)
    content: str | None = Field(None, min_length=1)
    summary: str | None = None
    metadata_json: dict | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    importance: float | None = Field(None, ge=0, le=1)
    source_type: str | None = None
    source_id: int | None = None
    status: str | None = None
    visibility: str | None = None
    expires_at: datetime | None = None

    @field_validator("memory_type")
    @classmethod
    def validate_memory_type(cls, value: str | None) -> str | None:
        return _validate_optional_choice(value, MEMORY_TYPES, "memory_type")

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: str | None) -> str | None:
        return _validate_optional_choice(value, MEMORY_SCOPES, "scope")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        return _validate_optional_choice(value, MEMORY_STATUSES, "status")

    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, value: str | None) -> str | None:
        return _validate_optional_choice(value, MEMORY_VISIBILITIES, "visibility")

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str | None) -> str | None:
        return _validate_optional_choice(value, SOURCE_TYPES, "source_type")


class UserMemoryItemResponse(BaseModel):
    id: int
    user_id: int
    memory_type: str
    scope: str
    key: str | None = None
    content: str
    summary: str | None = None
    metadata_json: dict | None = None
    confidence: float
    importance: float
    source_type: str | None = None
    source_id: int | None = None
    status: str
    visibility: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_accessed_at: datetime | None = None
    expires_at: datetime | None = None

    model_config = {"from_attributes": True}


class UserMemoryItemListResponse(BaseModel):
    items: list[UserMemoryItemResponse]
    total: int


class UserSkillProfileResponse(BaseModel):
    id: int
    user_id: int
    skill_name: str
    skill_category: str | None = None
    level_score: float
    confidence: float
    evidence_count: int
    weakness_summary: str | None = None
    strength_summary: str | None = None
    last_evaluated_at: datetime | None = None
    metadata_json: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class UserMemoryEventResponse(BaseModel):
    id: int
    user_id: int
    memory_item_id: int | None = None
    event_type: str
    actor_type: str
    actor_id: int | None = None
    before_json: dict | None = None
    after_json: dict | None = None
    reason: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class MemorySearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    memory_types: list[str] | None = None
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("memory_types")
    @classmethod
    def validate_memory_types(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [_validate_choice(item, MEMORY_TYPES, "memory_types") for item in value]


def _validate_choice(value: str, allowed: set[str], field_name: str) -> str:
    normalized = value.upper().strip()
    if normalized not in allowed:
        raise ValueError(f"{field_name} 非法，有效值: {', '.join(sorted(allowed))}")
    return normalized


def _validate_optional_choice(
    value: str | None,
    allowed: set[str],
    field_name: str,
) -> str | None:
    if value is None:
        return None
    return _validate_choice(value, allowed, field_name)
