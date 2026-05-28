"""Phase 4: Experience keyword presets service."""

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.experience.models import ExperienceKeywordPreset
from interview_api.modules.experience.repository import ExperienceKeywordPresetRepository

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
