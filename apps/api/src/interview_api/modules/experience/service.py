"""Phase 4: Experience keyword presets service."""

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.experience.models import (
    ExperienceKeywordPreset,
    ExperienceCollectionTask,
)
from interview_api.modules.experience.repository import (
    ExperienceKeywordPresetRepository,
    ExperienceCollectionTaskRepository,
)

VALID_PRESET_TYPES = {"COMPANY", "JOB", "PLATFORM"}


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
        if not isinstance(platforms, list) or not platforms:
            raise ValueError("至少需要选择一个平台")
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
