"""Schemas for audit log APIs."""

from datetime import datetime

from pydantic import BaseModel


class AuditLogRead(BaseModel):
    id: int
    request_id: str | None = None
    actor_user_id: int | None = None
    actor_role: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    before_json: dict | None = None
    after_json: dict | None = None
    metadata_json: dict | None = None
    status: str
    error_message: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
