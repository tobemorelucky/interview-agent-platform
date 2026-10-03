"""LLM-free orchestration for public data search and fetch."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.public_career.models import PublicDataRefreshTask
from interview_api.modules.public_career.repository import (
    PublicDataRefreshTaskRepository,
)
from interview_api.modules.public_career.tools.contracts import ToolContext
from interview_api.modules.public_career.tools.errors import ToolExecutionError
from interview_api.modules.public_career.tools.factory import (
    FETCH_TOOL_NAME,
    SEARCH_TOOL_NAME,
)
from interview_api.modules.public_career.tools.gateway import ToolGateway


class RefreshTaskStatus(StrEnum):
    PENDING = "PENDING"
    SEARCHING = "SEARCHING"
    FETCHING = "FETCHING"
    READY_FOR_EXTRACTION = "READY_FOR_EXTRACTION"
    EXTRACTING = "EXTRACTING"
    VALIDATING = "VALIDATING"
    PUBLISHING = "PUBLISHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


_ALLOWED_TRANSITIONS: dict[RefreshTaskStatus, set[RefreshTaskStatus]] = {
    RefreshTaskStatus.PENDING: {
        RefreshTaskStatus.SEARCHING,
        RefreshTaskStatus.FAILED,
    },
    RefreshTaskStatus.SEARCHING: {
        RefreshTaskStatus.FETCHING,
        RefreshTaskStatus.FAILED,
    },
    RefreshTaskStatus.FETCHING: {
        RefreshTaskStatus.READY_FOR_EXTRACTION,
        RefreshTaskStatus.FAILED,
    },
    RefreshTaskStatus.READY_FOR_EXTRACTION: {
        RefreshTaskStatus.EXTRACTING,
        RefreshTaskStatus.COMPLETED,
        RefreshTaskStatus.FAILED,
        RefreshTaskStatus.FETCHING,
        RefreshTaskStatus.SEARCHING,
    },
    RefreshTaskStatus.COMPLETED: {RefreshTaskStatus.EXTRACTING},  # explicit reviewed rerun only
    RefreshTaskStatus.EXTRACTING: {RefreshTaskStatus.VALIDATING, RefreshTaskStatus.FAILED},
    RefreshTaskStatus.VALIDATING: {RefreshTaskStatus.PUBLISHING, RefreshTaskStatus.FAILED},
    RefreshTaskStatus.PUBLISHING: {RefreshTaskStatus.COMPLETED, RefreshTaskStatus.FAILED},
    RefreshTaskStatus.FAILED: {RefreshTaskStatus.SEARCHING, RefreshTaskStatus.EXTRACTING},
}


class InvalidRefreshTaskTransition(ValueError):
    pass


class PublicDataRefreshService:
    def __init__(self, db: AsyncSession, gateway: ToolGateway | None) -> None:
        self.db = db
        self.gateway = gateway
        self.repository = PublicDataRefreshTaskRepository(db)

    async def transition(
        self,
        task: PublicDataRefreshTask,
        target: RefreshTaskStatus,
        *,
        error_message: str | None = None,
    ) -> PublicDataRefreshTask:
        current = RefreshTaskStatus(task.status)
        if current == target:
            return task
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise InvalidRefreshTaskTransition(
                f"Cannot transition public refresh task from {current} to {target}"
            )
        now = datetime.now(timezone.utc)
        task.status = target.value
        if task.started_at is None and target != RefreshTaskStatus.PENDING:
            task.started_at = now
        task.error_message = error_message
        if target in {RefreshTaskStatus.COMPLETED, RefreshTaskStatus.FAILED}:
            task.completed_at = now
        else:
            task.completed_at = None
        await self.db.flush()
        return task

    async def execute_to_ready(
        self,
        task: PublicDataRefreshTask,
        *,
        query: str,
        time_window_hours: int,
        max_results: int,
    ) -> PublicDataRefreshTask:
        if self.gateway is None:
            raise ValueError("Search/fetch execution requires a ToolGateway")
        task_id = task.id
        context = ToolContext(refresh_task_id=task_id)
        try:
            await self.transition(task, RefreshTaskStatus.SEARCHING)
            await self.db.commit()
            search_result = await self.gateway.invoke(
                SEARCH_TOOL_NAME,
                {
                    "query": query,
                    "time_window_hours": time_window_hours,
                    "max_results": max_results,
                },
                context,
            )
            urls = [item["url"] for item in search_result.data.get("results", [])]
            if not urls:
                raise ToolExecutionError("no_search_results", "Search returned no URLs")

            await self.db.refresh(task)
            await self.transition(task, RefreshTaskStatus.FETCHING)
            await self.db.commit()
            successful_fetches = 0
            errors: list[str] = []
            for url in urls:
                try:
                    await self.gateway.invoke(
                        FETCH_TOOL_NAME, {"url": url}, context
                    )
                    successful_fetches += 1
                except ToolExecutionError as exc:
                    errors.append(f"{exc.code}: {exc.message}")
            await self.db.refresh(task)
            if successful_fetches == 0:
                raise ToolExecutionError(
                    "all_fetches_failed",
                    "; ".join(errors) or "All public source fetches failed",
                )
            await self.transition(task, RefreshTaskStatus.READY_FOR_EXTRACTION)
            await self.db.commit()
            return task
        except ToolExecutionError as exc:
            await self.db.rollback()
            task = await self.repository.get(task_id) or task
            if task.status not in {
                RefreshTaskStatus.COMPLETED.value,
                RefreshTaskStatus.FAILED.value,
            }:
                await self.transition(
                    task,
                    RefreshTaskStatus.FAILED,
                    error_message=f"{exc.code}: {exc.message}",
                )
                await self.db.commit()
            return task

    async def mark_completed(
        self, task: PublicDataRefreshTask
    ) -> PublicDataRefreshTask:
        await self.transition(task, RefreshTaskStatus.COMPLETED)
        await self.db.commit()
        return task
