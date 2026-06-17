"""Phase 4: Experience keyword presets service."""

import asyncio
from datetime import datetime, timezone
import hashlib
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.config import settings
from interview_api.modules.experience.models import (
    ExperienceKeywordPreset,
    ExperienceCollectionTask,
    ExperienceSourceItem,
)
from interview_api.modules.experience.fetchers import FetchResult, HttpxContentFetcher
from interview_api.modules.experience.repository import (
    ExperienceKeywordPresetRepository,
    ExperienceCollectionTaskRepository,
    ExperienceSourceItemRepository,
)
from interview_api.modules.experience.search import (
    SearxngSearchProvider,
    build_search_queries,
)
from interview_api.modules.experience.search.filters import evaluate_candidate
from interview_api.modules.experience.search.url_utils import hash_url, normalize_url

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
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ExperienceCollectionTaskRepository(db)
        self.source_repo = ExperienceSourceItemRepository(db)

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
        await self.db.commit()

        if settings.experience_search_provider.lower() != "searxng":
            error_message = "当前阶段仅支持 EXPERIENCE_SEARCH_PROVIDER=searxng"
            task = await self._finish_task_failed(task_id, error_message, failed_count=1)
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
            task = await self._finish_task_failed(task_id, "没有生成可执行的搜索 query", failed_count=1)
            return {
                "task": self._task_to_dict(task),
                "found_url_count": task.found_url_count,
                "query_count": 0,
                "query_failed_count": 1,
            }

        provider = SearxngSearchProvider()
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
                results = await provider.search(
                    query.query,
                    time_window_hours=task.time_window_hours,
                    max_results=remaining,
                )
                query_success_count += 1
            except RuntimeError as exc:
                query_failed_count += 1
                error_messages.append(f"{query.query}: {exc}")
                logger.info(
                    "[experience-search] task=%s query=%r platform=%s raw=0 "
                    "accepted=0 filtered=0 duplicate=0 error=%r",
                    task.id,
                    query.query,
                    query.platform,
                    str(exc),
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

    async def fetch_task_sources(
        self,
        task_id: int,
        *,
        retry_failed: bool = False,
        limit: int = 20,
    ) -> dict:
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
            return {
                "task_id": task_id,
                "total": 0,
                "fetched_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "task": self._task_to_dict(task),
            }

        fetcher = HttpxContentFetcher()
        fetch_results = await self._fetch_sources_concurrently(fetcher, items)

        fetched_count = 0
        failed_count = 0
        for item, result in fetch_results:
            fetched_at = datetime.now(timezone.utc)
            if result.ok and result.raw_text:
                raw_text = result.raw_text
                await self.source_repo.update_source_item_fetch_result(
                    item.id,
                    raw_text=raw_text,
                    content_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                    fetched_at=fetched_at,
                    fetch_status="FETCHED",
                    error_message=None,
                    title=result.title or item.title,
                )
                fetched_count += 1
                logger.info(
                    "[experience-fetch] source=%s status=FETCHED chars=%s url=%s",
                    item.id,
                    len(raw_text),
                    item.source_url,
                )
            else:
                error_message = (result.error_message or "request_failed")[:2000]
                await self.source_repo.update_source_item_fetch_result(
                    item.id,
                    fetched_at=fetched_at,
                    fetch_status="FETCH_FAILED",
                    error_message=error_message,
                )
                failed_count += 1
                logger.info(
                    "[experience-fetch] source=%s status=FETCH_FAILED error=%r url=%s",
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

        return {
            "task_id": task_id,
            "total": len(items),
            "fetched_count": fetched_count,
            "failed_count": failed_count,
            "skipped_count": 0,
            "task": self._task_to_dict(task),
        }

    @staticmethod
    async def _fetch_sources_concurrently(
        fetcher: HttpxContentFetcher,
        items: list[ExperienceSourceItem],
    ) -> list[tuple[ExperienceSourceItem, FetchResult]]:
        semaphore = asyncio.Semaphore(3)

        async def fetch_one(item: ExperienceSourceItem) -> tuple[ExperienceSourceItem, FetchResult]:
            async with semaphore:
                try:
                    result = await fetcher.fetch(item.source_url)
                except Exception as exc:  # defensive: one URL must not break the batch
                    result = FetchResult(
                        url=item.source_url,
                        final_url=None,
                        status_code=None,
                        content_type=None,
                        title=None,
                        raw_text=None,
                        error_message=f"request_failed: {exc}",
                    )
                return item, result

        return await asyncio.gather(*(fetch_one(item) for item in items))

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
            "raw_text_char_count": len(raw_text),
            "raw_text_preview": raw_text[:2000] if raw_text else None,
            "fetched_at": item.fetched_at.isoformat() if item.fetched_at else None,
            "fetch_status": item.fetch_status,
            "extract_status": item.extract_status,
            "error_message": item.error_message,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
