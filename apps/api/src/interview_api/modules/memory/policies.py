"""Small policy helpers for user memory access."""

from dataclasses import dataclass


MEMORY_TYPES = {
    "SEMANTIC",
    "SKILL",
    "EPISODIC",
    "PROCEDURAL",
    "PREFERENCE",
    "SAFETY",
}

MEMORY_SCOPES = {"INTERVIEW", "RESUME", "QA", "EXPERIENCE", "SYSTEM"}
MEMORY_STATUSES = {"ACTIVE", "ARCHIVED", "DELETED"}
MEMORY_VISIBILITIES = {"PRIVATE", "ADMIN_VISIBLE"}
SOURCE_TYPES = {
    "INTERVIEW_SESSION",
    "INTERVIEW_MESSAGE",
    "RESUME",
    "USER_PROFILE",
    "ADMIN",
    "SYSTEM",
}
EVENT_TYPES = {"CREATED", "UPDATED", "ARCHIVED", "DELETED", "READ", "CONSOLIDATED"}
ACTOR_TYPES = {"USER", "ADMIN", "SYSTEM", "AGENT"}


@dataclass(frozen=True)
class MemoryActor:
    actor_type: str
    actor_id: int | None = None


def validate_choice(value: str, allowed: set[str], field_name: str) -> str:
    normalized = value.upper().strip()
    if normalized not in allowed:
        raise ValueError(f"{field_name} 非法，有效值: {', '.join(sorted(allowed))}")
    return normalized


def validate_score(value: float, field_name: str) -> float:
    if value < 0 or value > 1:
        raise ValueError(f"{field_name} 必须在 0 到 1 之间")
    return value


def ensure_owned(resource_user_id: int, current_user_id: int) -> None:
    if resource_user_id != current_user_id:
        raise PermissionError("无权访问其他用户的记忆")


def ensure_user_actor(actor: MemoryActor) -> None:
    if actor.actor_type not in {"USER", "ADMIN"}:
        raise PermissionError("普通接口不能创建 AGENT/SYSTEM 事件")


def ensure_explicit_safety_update(original_type: str, incoming_type: str | None) -> None:
    if original_type == "SAFETY" and incoming_type != "SAFETY":
        raise ValueError("更新 SAFETY 记忆时必须显式传入 memory_type=SAFETY")
