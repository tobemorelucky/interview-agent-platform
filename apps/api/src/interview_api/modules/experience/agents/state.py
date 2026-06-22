"""LangGraph state for Phase 4 Step 7A experience extraction."""

from typing import Any, TypedDict


class ExperienceAgentState(TypedDict, total=False):
    source_item_id: int
    task_id: int | None
    source_url: str
    title: str | None
    snippet: str | None
    platform: str | None
    raw_text: str
    extraction_result: dict[str, Any] | None
    validation_errors: list[str]
    is_valid: bool
    run_id: int | None
    status: str
    saved_result: dict[str, Any] | None
