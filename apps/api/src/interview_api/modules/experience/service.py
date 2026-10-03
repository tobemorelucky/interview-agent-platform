"""Phase 4: Experience keyword presets service."""

import asyncio
from datetime import datetime, timezone
import hashlib
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.config import settings
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.experience.models import (
    ExperienceKeywordPreset,
    ExperienceCollectionTask,
    ExperienceSourceItem,
)
from interview_api.modules.experience.fetchers import FetchResult
from interview_api.modules.experience.repository import (
    ExperienceKeywordPresetRepository,
    ExperienceCollectionTaskRepository,
    ExperienceSourceItemRepository,
)
from interview_api.modules.experience.search import (
    SearchResult,
    build_search_queries,
)
from interview_api.modules.experience.search.filters import evaluate_candidate
from interview_api.modules.experience.search.url_utils import hash_url, normalize_url
from interview_api.modules.public_career.compat import ExperienceRefreshTaskAdapter
from interview_api.modules.public_career.models import PublicDataRefreshTask
from interview_api.modules.public_career.repository import PublicDataRefreshTaskRepository
from interview_api.modules.public_career.refresh_service import (
    PublicDataRefreshService,
    RefreshTaskStatus,
)
from interview_api.modules.public_career.tools.contracts import ToolContext
from interview_api.modules.public_career.tools.errors import ToolExecutionError
from interview_api.modules.public_career.tools.factory import (
    FETCH_TOOL_NAME,
    SEARCH_TOOL_NAME,
    build_live_tool_gateway,
)
from interview_api.modules.public_career.tools.gateway import ToolGateway

VALID_PRESET_TYPES = {"COMPANY", "JOB", "PLATFORM"}
logger = logging.getLogger(__name__)


class ExperienceKeywordService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ExperienceKeywordPresetRepository(db)

    async def list_keywords(
        self,
        preset_type: str | None = None,
        enabled: bool | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict:
        if preset_type and preset_type not in VALID_PRESET_TYPES:
            raise ValueError(f"非法的 preset_type: {preset_type}")

        items, total = await self.repo.list_by_type(
            preset_type=preset_type, enabled=enabled, offset=offset, limit=limit
        )
        return {
            "items": [self._to_dict(item) for item in items],
            "total": total,
        }

    async def create_keyword(self, data: dict, user_id: int | None = None) -> dict:
        preset_type = data.get("preset_type", "")
        name = data.get("name", "")
        aliases = data.get("aliases_json", [])

        if preset_type not in VALID_PRESET_TYPES:
            raise ValueError(f"非法的 preset_type: {preset_type}，有效值: {', '.join(sorted(VALID_PRESET_TYPES))}")
        if not name or not name.strip():
            raise ValueError("name 不能为空")
        if not isinstance(aliases, list):
            raise ValueError("aliases_json 必须是数组")

        existing = await self.repo.get_by_type_and_name(preset_type, name.strip())
        if existing:
            raise ValueError(f"关键词已存在: {preset_type}/{name}")

        preset = ExperienceKeywordPreset(
            preset_type=preset_type,
            name=name.strip(),
            aliases_json=aliases,
            enabled=data.get("enabled", True),
            created_by=user_id,
        )
        created = await self.repo.create(preset)
        return self._to_dict(created)

    async def update_keyword(self, preset_id: int, data: dict) -> dict:
        preset = await self.repo.get_by_id(preset_id)
        if not preset:
            raise ValueError("关键词不存在")

        values = {}
        if "name" in data:
            if not data["name"] or not data["name"].strip():
                raise ValueError("name 不能为空")
            values["name"] = data["name"].strip()
        if "aliases_json" in data:
            if not isinstance(data["aliases_json"], list):
                raise ValueError("aliases_json 必须是数组")
            values["aliases_json"] = data["aliases_json"]
        if "enabled" in data:
            values["enabled"] = bool(data["enabled"])

        if not values:
            raise ValueError("没有可更新的字段")

        updated = await self.repo.update(preset_id, **values)
        return self._to_dict(updated) if updated else self._to_dict(preset)

    async def delete_keyword(self, preset_id: int) -> bool:
        preset = await self.repo.get_by_id(preset_id)
        if not preset:
            raise ValueError("关键词不存在")
        return await self.repo.delete(preset_id)

    @staticmethod
    def _to_dict(preset: ExperienceKeywordPreset) -> dict:
        return {
            "id": preset.id,
            "preset_type": preset.preset_type,
            "name": preset.name,
            "aliases_json": preset.aliases_json,
            "enabled": preset.enabled,
            "created_by": preset.created_by,
            "created_at": preset.created_at.isoformat() if preset.created_at else None,
            "updated_at": preset.updated_at.isoformat() if preset.updated_at else None,
        }


class ExperienceTaskService:
    def __init__(self, db: AsyncSession, gateway: ToolGateway | None = None):
        self.db = db
        self.repo = ExperienceCollectionTaskRepository(db)
        self.source_repo = ExperienceSourceItemRepository(db)
        self.gateway = gateway or build_live_tool_gateway(async_session_factory)
        self.public_task_repo = PublicDataRefreshTaskRepository(db)
        self.refresh_adapter = ExperienceRefreshTaskAdapter(self.public_task_repo)
        self.refresh_service = PublicDataRefreshService(db, self.gateway)

    async def create_task(self, data: dict, user_id: int | None = None) -> dict:
        search_scope = data.get("search_scope", "JOB")
        if search_scope not in ("JOB", "COMPANY"):
            raise ValueError("search_scope 必须是 JOB 或 COMPANY")
        time_window = data.get("time_window_hours", 24)
        if not isinstance(time_window, int) or time_window <= 0:
            raise ValueError("time_window_hours 必须 > 0")
        max_results = data.get("max_results", 20)
        if not isinstance(max_results, int) or max_results < 1 or max_results > 100:
            raise ValueError("max_results 必须在 1-100 之间")
        review_mode = data.get("review_mode", "MANUAL")
        if review_mode not in ("MANUAL", "AUTO_PUBLISH"):
            raise ValueError("review_mode 必须是 MANUAL 或 AUTO_PUBLISH")
        platforms = data.get("platforms_json", [])
        if not isinstance(platforms, list):
            raise ValueError("platforms_json 必须是数组")
        platforms = [
            item.strip() for item in platforms
            if isinstance(item, str) and item.strip() and item.strip() != "全网"
        ]
        job_kw = data.get("job_keywords_json", [])
        company_kw = data.get("company_keywords_json", [])
        if not isinstance(job_kw, list):
            raise ValueError("job_keywords_json 必须是数组")
        if not isinstance(company_kw, list):
            raise ValueError("company_keywords_json 必须是数组")
        if search_scope == "JOB":
            if not job_kw:
                raise ValueError("按岗位搜索时，必须至少选择一个岗位关键词")
            if company_kw:
                raise ValueError("按岗位搜索时，公司关键词必须为空")
        elif search_scope == "COMPANY":
            if not company_kw:
                raise ValueError("按公司搜索时，必须至少选择一个公司关键词")
            if job_kw:
                raise ValueError("按公司搜索时，岗位关键词必须为空")

        task = ExperienceCollectionTask(
            created_by=user_id,
            search_scope=search_scope,
            time_window_hours=time_window,
            job_keywords_json=job_kw,
            company_keywords_json=company_kw,
            platforms_json=platforms,
            max_results=max_results,
            review_mode=review_mode,
            write_to_question_db=bool(data.get("write_to_question_db", False)),
            write_to_vector_index=bool(data.get("write_to_vector_index", False)),
            update_public_summary=bool(data.get("update_public_summary", True)),
        )
        created = await self.repo.create(task)
        await self.refresh_adapter.get_or_create(created)
        await self.db.refresh(created)
        return self._task_to_dict(created)

    async def list_tasks(
        self, status: str | None = None, offset: int = 0, limit: int = 50
    ) -> dict:
        items, total = await self.repo.list_tasks(status=status, offset=offset, limit=limit)
        return {
            "items": [self._task_to_dict(t) for t in items],
            "total": total,
        }

    async def get_task(self, task_id: int) -> dict | None:
        task = await self.repo.get_by_id(task_id)
        return self._task_to_dict(task) if task else None

    async def delete_task(self, task_id: int) -> bool:
        task = await self.repo.get_by_id(task_id)
        if not task:
            raise LookupError("任务不存在")
        deleted = await self.repo.delete(task_id)
        await self.db.commit()
        return deleted

    async def run_search(self, task_id: int) -> dict:
        task = await self.repo.get_by_id(task_id)
        if not task:
            raise LookupError("任务不存在")

        if task.status not in {"PENDING", "FAILED", "SEARCH_COMPLETED"}:
            raise ValueError(
                f"当前任务状态为 {task.status}，只允许 PENDING / FAILED / SEARCH_COMPLETED 执行搜索"
            )

        started_at = task.started_at or datetime.now(timezone.utc)
        task = await self.repo.update(
            task_id,
            status="SEARCHING",
            progress=5,
            failed_count=0,
            error_message=None,
            started_at=started_at,
            finished_at=None,
        )
        refresh_task = await self.refresh_adapter.get_or_create(task)
        if refresh_task.status in {
            RefreshTaskStatus.PENDING.value,
            RefreshTaskStatus.FAILED.value,
            RefreshTaskStatus.READY_FOR_EXTRACTION.value,
        }:
            await self.refresh_service.transition(
                refresh_task, RefreshTaskStatus.SEARCHING
            )
        await self.db.commit()

        if settings.experience_search_provider.lower() != "searxng":
            error_message = "当前阶段仅支持 EXPERIENCE_SEARCH_PROVIDER=searxng"
            task = await self._finish_task_failed(task_id, error_message, failed_count=1)
            await self._mark_public_refresh_failed(refresh_task, error_message)
            return {
                "task": self._task_to_dict(task),
                "found_url_count": task.found_url_count,
                "query_count": 0,
                "query_failed_count": 1,
            }

        queries = build_search_queries(
            search_scope=task.search_scope,
            job_keywords_json=task.job_keywords_json or [],
            company_keywords_json=task.company_keywords_json or [],
            platforms_json=task.platforms_json or [],
            time_window_hours=task.time_window_hours,
        )
        if not queries:
            error_message = "没有生成可执行的搜索 query"
            task = await self._finish_task_failed(task_id, error_message, failed_count=1)
            await self._mark_public_refresh_failed(refresh_task, error_message)
            return {
                "task": self._task_to_dict(task),
                "found_url_count": task.found_url_count,
                "query_count": 0,
                "query_failed_count": 1,
            }

        max_results = min(task.max_results, settings.experience_search_max_results)
        query_failed_count = 0
        query_success_count = 0
        raw_result_count_total = 0
        accepted_count_total = 0
        filtered_count_total = 0
        duplicate_count_total = 0
        error_messages: list[str] = []

        for index, query in enumerate(queries, start=1):
            current_count = await self.source_repo.count_source_items_by_task(task.id)
            remaining = max_results - current_count
            if remaining <= 0:
                break

            try:
                tool_result = await self.gateway.invoke(
                    SEARCH_TOOL_NAME,
                    {
                        "query": query.query,
                        "time_window_hours": task.time_window_hours,
                        "max_results": remaining,
                    },
                    ToolContext(refresh_task_id=refresh_task.id),
                )
                results = [
                    SearchResult(
                        title=item.get("title") or "",
                        url=item["url"],
                        snippet=item.get("snippet"),
                        engine=item.get("engine"),
                        published_at=item.get("published_at"),
                        metadata=item.get("metadata") or {},
                    )
                    for item in tool_result.data.get("results", [])
                ]
                query_success_count += 1
            except ToolExecutionError as exc:
                query_failed_count += 1
                error_messages.append(f"{query.query}: {exc.code}: {exc.message}")
                logger.info(
                    "[experience-search] task=%s query=%r platform=%s raw=0 "
                    "accepted=0 filtered=0 duplicate=0 error=%r",
                    task.id,
                    query.query,
                    query.platform,
                    exc.message,
                )
                continue

            raw_result_count = len(results)
            accepted_count = 0
            filtered_count = 0
            duplicate_count = 0
            raw_result_count_total += raw_result_count
            for result in results:
                filter_result = evaluate_candidate(
                    result,
                    keyword=query.keyword,
                    platform=query.platform,
                )
                if not filter_result.accepted:
                    filtered_count += 1
                    continue
                normalized_url = normalize_url(result.url)
                if not normalized_url:
                    filtered_count += 1
                    continue
                normalized_url_hash = hash_url(normalized_url)
                exists = await self.source_repo.exists_by_task_and_url_hash(
                    task.id, normalized_url_hash
                )
                if exists:
                    duplicate_count += 1
                    continue
                await self.source_repo.create_source_item(
                    ExperienceSourceItem(
                        task_id=task.id,
                        source_url=normalized_url,
                        normalized_url_hash=normalized_url_hash,
                        platform=query.platform,
                        title=result.title or None,
                        query_text=query.query,
                        snippet=result.snippet,
                        engine=result.engine,
                        matched_reason=filter_result.reason,
                        fetch_status="DISCOVERED",
                    )
                )
                accepted_count += 1

            accepted_count_total += accepted_count
            filtered_count_total += filtered_count
            duplicate_count_total += duplicate_count
            logger.info(
                "[experience-search] task=%s query=%r platform=%s raw=%s "
                "accepted=%s filtered=%s duplicate=%s",
                task.id,
                query.query,
                query.platform,
                raw_result_count,
                accepted_count,
                filtered_count,
                duplicate_count,
            )

            progress = min(95, 5 + int(index / len(queries) * 90))
            await self.repo.update(task_id, progress=progress)
            await self.db.flush()

        found_url_count = await self.source_repo.count_source_items_by_task(task.id)
        if query_success_count == 0:
            error_message = "；".join(error_messages[:5]) or "所有搜索 query 均执行失败"
            task = await self._finish_task_failed(
                task_id, error_message, failed_count=query_failed_count
            )
            await self._mark_public_refresh_failed(refresh_task, error_message)
        else:
            final_error_message = "；".join(error_messages[:3]) if error_messages else None
            if found_url_count == 0:
                if raw_result_count_total > 0:
                    final_error_message = "搜索完成但候选过滤后为 0，请检查平台或标题过滤规则。"
                else:
                    final_error_message = "搜索完成但 SearXNG 未返回结果，请尝试扩大时间范围或调整关键词。"
            task = await self.repo.update(
                task_id,
                status="SEARCH_COMPLETED",
                progress=100,
                found_url_count=found_url_count,
                failed_count=query_failed_count,
                error_message=final_error_message,
                finished_at=datetime.now(timezone.utc),
            )
            await self.db.commit()

        return {
            "task": self._task_to_dict(task),
            "query_count": len(queries),
            "query_success_count": query_success_count,
            "query_failed_count": query_failed_count,
            "raw_result_count": raw_result_count_total,
            "accepted_count": accepted_count_total,
            "filtered_count": filtered_count_total,
            "duplicate_count": duplicate_count_total,
            "found_url_count": found_url_count,
        }

    async def list_source_items(
        self,
        task_id: int,
        *,
        offset: int = 0,
        limit: int = 50,
        fetch_status: str | None = None,
    ) -> dict:
        task = await self.repo.get_by_id(task_id)
        if not task:
            raise LookupError("任务不存在")
        items, total = await self.source_repo.list_source_items_by_task(
            task_id,
            offset=offset,
            limit=limit,
            fetch_status=fetch_status,
        )
        return {
            "items": [self._source_item_to_dict(item) for item in items],
            "total": total,
        }

    async def get_fetch_stats(self, task_id: int) -> dict:
        task = await self.repo.get_by_id(task_id)
        if not task:
            raise LookupError("任务不存在")
        stats = await self.source_repo.get_fetch_stats_by_task(task_id)
        stats["task_id"] = task_id
        stats["pending_count"] = stats["discovered_count"]
        stats["failure_reasons"] = await self.source_repo.list_failure_reasons_by_task(
            task_id
        )
        stats["platform_stats"] = await self.source_repo.list_platform_fetch_stats_by_task(
            task_id
        )
        logger.info(
            "[experience-fetch-stats] task=%s total=%s fetched=%s failed=%s avg_chars=%s",
            task_id,
            stats["total"],
            stats["fetched_count"],
            stats["failed_count"],
            stats["avg_raw_text_chars"],
        )
        return stats

    async def get_source_preview(self, source_id: int) -> dict:
        item = await self.source_repo.get_source_item(source_id)
        if not item:
            raise LookupError("来源不存在")
        raw_text = item.raw_text or ""
        return {
            "source_id": item.id,
            "title": item.title,
            "source_url": item.source_url,
            "fetch_status": item.fetch_status,
            "fetch_status_label": self._fetch_status_label(item.fetch_status),
            "fetch_quality": self._fetch_quality(item.fetch_status, len(raw_text)),
            "raw_text_char_count": len(raw_text),
            "raw_text_preview": raw_text[:2000],
            "message": "" if raw_text else "暂无正文",
        }

    async def fetch_source_item(self, source_id: int, *, force: bool = False) -> dict:
        item = await self.source_repo.get_source_item(source_id)
        if not item:
            raise LookupError("来源不存在")
        if not force and item.fetch_status not in {"DISCOVERED", "FETCH_FAILED"}:
            return {
                "source_id": source_id,
                "task_id": item.task_id,
                "skipped": True,
                "fetch_status": item.fetch_status,
                "error_message": None,
                "raw_text_char_count": len(item.raw_text or ""),
            }

        refresh_task = await self._prepare_public_fetch(item.task_id)
        result = await self._invoke_public_fetch(
            item.source_url, refresh_task_id=refresh_task.id
        )
        fetched_at = datetime.now(timezone.utc)
        if result.ok and result.raw_text:
            raw_text = result.raw_text
            updated = await self.source_repo.update_source_item_fetch_success(
                item.id,
                raw_text=raw_text,
                content_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                fetched_at=fetched_at,
                title=result.title or item.title,
            )
            await self.db.commit()
            logger.info(
                "[experience-fetch-retry] source=%s status=FETCHED chars=%s",
                item.id,
                len(raw_text),
            )
            await self._refresh_task_fetch_counts(item.task_id)
            await self._sync_public_refresh_after_fetch(refresh_task)
            return {
                "source_id": source_id,
                "task_id": item.task_id,
                "skipped": False,
                "fetch_status": "FETCHED",
                "error_message": None,
                "raw_text_char_count": len(raw_text),
                "item": self._source_item_to_dict(updated),
            }

        error_message = (result.error_message or "request_failed")[:2000]
        updated = await self.source_repo.update_source_item_fetch_failed(
            item.id,
            fetched_at=fetched_at,
            error_message=error_message,
        )
        await self.db.commit()
        logger.info(
            "[experience-fetch-retry] source=%s status=FETCH_FAILED error=%r",
            item.id,
            error_message,
        )
        await self._refresh_task_fetch_counts(item.task_id)
        await self._sync_public_refresh_after_fetch(refresh_task)
        return {
            "source_id": source_id,
            "task_id": item.task_id,
            "skipped": False,
            "fetch_status": "FETCH_FAILED",
            "error_message": error_message,
            "raw_text_char_count": 0,
            "item": self._source_item_to_dict(updated),
        }

    async def fetch_task_sources(
        self,
        task_id: int,
        *,
        retry_failed: bool = False,
        limit: int = 20,
    ) -> dict:
        started = time.perf_counter()
        task = await self.repo.get_by_id(task_id)
        if not task:
            raise LookupError("任务不存在")
        if limit < 1 or limit > 100:
            raise ValueError("limit 必须在 1-100 之间")

        items = await self.source_repo.list_fetchable_source_items(
            task_id,
            retry_failed=retry_failed,
            limit=limit,
        )
        task = await self.repo.update(
            task_id,
            status="FETCHING",
            progress=60,
            error_message=None,
            finished_at=None,
        )
        refresh_task = await self._prepare_public_fetch(task_id)
        await self.db.commit()

        if not items:
            fetched_total = await self.source_repo.count_source_items_by_task(
                task_id, fetch_status="FETCHED"
            )
            failed_total = await self.source_repo.count_source_items_by_task(
                task_id, fetch_status="FETCH_FAILED"
            )
            task = await self.repo.update(
                task_id,
                status="FETCH_COMPLETED",
                progress=100,
                fetched_count=fetched_total,
                failed_count=failed_total,
                finished_at=datetime.now(timezone.utc),
            )
            await self.db.commit()
            await self._sync_public_refresh_after_fetch(refresh_task)
            logger.info(
                "[experience-fetch] task=%s total=0 fetched=0 failed=0 duration_ms=%s",
                task_id,
                int((time.perf_counter() - started) * 1000),
            )
            return {
                "task_id": task_id,
                "total": 0,
                "fetched_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "task": self._task_to_dict(task),
            }

        fetch_results = await self._fetch_sources_concurrently(
            items, refresh_task_id=refresh_task.id
        )

        fetched_count = 0
        failed_count = 0
        for item, result in fetch_results:
            fetched_at = datetime.now(timezone.utc)
            if result.ok and result.raw_text:
                raw_text = result.raw_text
                await self.source_repo.update_source_item_fetch_success(
                    item.id,
                    raw_text=raw_text,
                    content_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                    fetched_at=fetched_at,
                    title=result.title or item.title,
                )
                fetched_count += 1
                logger.info(
                    "[experience-fetch] task=%s source=%s status=FETCHED chars=%s url=%s",
                    task_id,
                    item.id,
                    len(raw_text),
                    item.source_url,
                )
            else:
                error_message = (result.error_message or "request_failed")[:2000]
                await self.source_repo.update_source_item_fetch_failed(
                    item.id,
                    fetched_at=fetched_at,
                    error_message=error_message,
                )
                failed_count += 1
                logger.info(
                    "[experience-fetch] task=%s source=%s status=FETCH_FAILED error=%r url=%s",
                    task_id,
                    item.id,
                    error_message,
                    item.source_url,
                )
            await self.db.commit()

        fetched_total = await self.source_repo.count_source_items_by_task(
            task_id, fetch_status="FETCHED"
        )
        failed_total = await self.source_repo.count_source_items_by_task(
            task_id, fetch_status="FETCH_FAILED"
        )
        task = await self.repo.update(
            task_id,
            status="FETCH_COMPLETED",
            progress=100,
            fetched_count=fetched_total,
            failed_count=failed_total,
            error_message=None if fetched_count else "本次抓取未获得可用正文",
            finished_at=datetime.now(timezone.utc),
        )
        await self.db.commit()
        await self._sync_public_refresh_after_fetch(refresh_task)
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "[experience-fetch] task=%s total=%s fetched=%s failed=%s duration_ms=%s",
            task_id,
            len(items),
            fetched_count,
            failed_count,
            duration_ms,
        )

        return {
            "task_id": task_id,
            "total": len(items),
            "fetched_count": fetched_count,
            "failed_count": failed_count,
            "skipped_count": 0,
            "task": self._task_to_dict(task),
        }

    async def _fetch_sources_concurrently(
        self,
        items: list[ExperienceSourceItem],
        *,
        refresh_task_id: int,
    ) -> list[tuple[ExperienceSourceItem, FetchResult]]:
        semaphore = asyncio.Semaphore(3)

        async def fetch_one(item: ExperienceSourceItem) -> tuple[ExperienceSourceItem, FetchResult]:
            async with semaphore:
                try:
                    result = await self._invoke_public_fetch(
                        item.source_url, refresh_task_id=refresh_task_id
                    )
                except Exception as exc:  # defensive: one URL must not break the batch
                    result = FetchResult(
                        url=item.source_url,
                        final_url=None,
                        status_code=None,
                        content_type=None,
                        title=None,
                        raw_text=None,
                        error_code="request_failed",
                        error_message=f"request_failed: {exc}",
                        fetch_method="httpx",
                    )
                return item, result

        return await asyncio.gather(*(fetch_one(item) for item in items))

    async def _prepare_public_fetch(
        self, task_id: int
    ) -> PublicDataRefreshTask:
        legacy_task = await self.repo.get_by_id(task_id)
        if legacy_task is None:
            raise LookupError("任务不存在")
        refresh_task = await self.refresh_adapter.get_or_create(legacy_task)
        if refresh_task.status in {
            RefreshTaskStatus.PENDING.value,
            RefreshTaskStatus.FAILED.value,
        }:
            await self.refresh_service.transition(
                refresh_task, RefreshTaskStatus.SEARCHING
            )
        if refresh_task.status in {
            RefreshTaskStatus.SEARCHING.value,
            RefreshTaskStatus.READY_FOR_EXTRACTION.value,
        }:
            await self.refresh_service.transition(
                refresh_task, RefreshTaskStatus.FETCHING
            )
        await self.db.commit()
        return refresh_task

    async def _invoke_public_fetch(
        self, url: str, *, refresh_task_id: int
    ) -> FetchResult:
        try:
            tool_result = await self.gateway.invoke(
                FETCH_TOOL_NAME,
                {"url": url},
                ToolContext(refresh_task_id=refresh_task_id),
            )
        except ToolExecutionError as exc:
            return FetchResult(
                url=url,
                final_url=None,
                status_code=exc.details.get("status_code"),
                content_type=exc.details.get("content_type"),
                title=None,
                raw_text=None,
                error_code=exc.code,
                error_message=exc.message,
                fetch_method="gateway",
            )
        data = tool_result.data
        return FetchResult(
            url=data.get("url") or url,
            final_url=data.get("final_url"),
            status_code=data.get("status_code"),
            content_type=data.get("content_type"),
            title=data.get("title"),
            raw_text=data.get("content"),
            fetch_method=data.get("fetch_method") or "gateway",
            metadata=data.get("metadata") or {},
        )

    async def _sync_public_refresh_after_fetch(
        self, refresh_task: PublicDataRefreshTask
    ) -> None:
        legacy_task_id = int(refresh_task.target_ref or 0)
        discovered = await self.source_repo.count_source_items_by_task(
            legacy_task_id, fetch_status="DISCOVERED"
        )
        if discovered > 0:
            return
        fetched = await self.source_repo.count_source_items_by_task(
            legacy_task_id, fetch_status="FETCHED"
        )
        await self.db.refresh(refresh_task)
        if refresh_task.status != RefreshTaskStatus.FETCHING.value:
            return
        if fetched > 0:
            await self.refresh_service.transition(
                refresh_task, RefreshTaskStatus.READY_FOR_EXTRACTION
            )
        else:
            await self.refresh_service.transition(
                refresh_task,
                RefreshTaskStatus.FAILED,
                error_message="All compatibility fetches failed",
            )
        await self.db.commit()

    async def _mark_public_refresh_failed(
        self,
        refresh_task: PublicDataRefreshTask,
        error_message: str,
    ) -> None:
        await self.db.refresh(refresh_task)
        if refresh_task.status not in {
            RefreshTaskStatus.COMPLETED.value,
            RefreshTaskStatus.FAILED.value,
        }:
            await self.refresh_service.transition(
                refresh_task,
                RefreshTaskStatus.FAILED,
                error_message=error_message[:2000],
            )
            await self.db.commit()

    async def _refresh_task_fetch_counts(self, task_id: int) -> None:
        fetched_total = await self.source_repo.count_source_items_by_task(
            task_id, fetch_status="FETCHED"
        )
        failed_total = await self.source_repo.count_source_items_by_task(
            task_id, fetch_status="FETCH_FAILED"
        )
        await self.repo.update(
            task_id,
            fetched_count=fetched_total,
            failed_count=failed_total,
        )
        await self.db.commit()

    async def _finish_task_failed(
        self,
        task_id: int,
        error_message: str,
        *,
        failed_count: int,
    ) -> ExperienceCollectionTask:
        found_url_count = await self.source_repo.count_source_items_by_task(task_id)
        task = await self.repo.update(
            task_id,
            status="FAILED",
            progress=100,
            found_url_count=found_url_count,
            failed_count=failed_count,
            error_message=error_message[:2000],
            finished_at=datetime.now(timezone.utc),
        )
        await self.db.commit()
        return task

    @staticmethod
    def _task_to_dict(task: ExperienceCollectionTask) -> dict:
        return {
            "id": task.id,
            "public_refresh_task_id": task.public_refresh_task_id,
            "created_by": task.created_by,
            "time_window_hours": task.time_window_hours,
            "search_scope": task.search_scope,
            "job_keywords_json": task.job_keywords_json,
            "company_keywords_json": task.company_keywords_json,
            "platforms_json": task.platforms_json,
            "max_results": task.max_results,
            "review_mode": task.review_mode,
            "write_to_question_db": task.write_to_question_db,
            "write_to_vector_index": task.write_to_vector_index,
            "update_public_summary": task.update_public_summary,
            "status": task.status,
            "progress": task.progress,
            "found_url_count": task.found_url_count,
            "fetched_count": task.fetched_count,
            "extracted_count": task.extracted_count,
            "question_count": task.question_count,
            "approved_count": task.approved_count,
            "failed_count": task.failed_count,
            "error_message": task.error_message,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        }

    @staticmethod
    def _source_item_to_dict(item: ExperienceSourceItem) -> dict:
        raw_text = item.raw_text or ""
        raw_text_len = len(raw_text)
        return {
            "id": item.id,
            "task_id": item.task_id,
            "source_url": item.source_url,
            "normalized_url_hash": item.normalized_url_hash,
            "platform": item.platform,
            "title": item.title,
            "query_text": item.query_text,
            "snippet": item.snippet,
            "engine": item.engine,
            "matched_reason": item.matched_reason,
            "filtered_reason": item.filtered_reason,
            "raw_text_char_count": raw_text_len,
            "raw_text_preview": raw_text[:300] if raw_text else None,
            "fetched_at": item.fetched_at.isoformat() if item.fetched_at else None,
            "fetch_status": item.fetch_status,
            "fetch_status_label": ExperienceTaskService._fetch_status_label(item.fetch_status),
            "fetch_quality": ExperienceTaskService._fetch_quality(
                item.fetch_status, raw_text_len
            ),
            "extract_status": item.extract_status,
            "error_message": item.error_message,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }

    @staticmethod
    def _fetch_status_label(fetch_status: str) -> str:
        return {
            "DISCOVERED": "待抓取",
            "FETCHED": "已抓取",
            "FETCH_FAILED": "抓取失败",
        }.get(fetch_status, fetch_status)

    @staticmethod
    def _fetch_quality(fetch_status: str, raw_text_len: int) -> str:
        if fetch_status == "FETCH_FAILED":
            return "FAILED"
        if fetch_status == "DISCOVERED":
            return "PENDING"
        if fetch_status == "FETCHED" and raw_text_len >= 1000:
            return "GOOD"
        if fetch_status == "FETCHED":
            return "SHORT"
        return "PENDING"
