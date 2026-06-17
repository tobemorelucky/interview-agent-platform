"""Build read-only memory context for interview prompts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, nulls_last, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.memory.models import UserMemoryItem, UserSkillProfile


MEMORY_CONTEXT_TYPES = {"SEMANTIC", "SKILL", "EPISODIC", "PREFERENCE", "SAFETY"}
MEMORY_CONTEXT_SCOPES = {"INTERVIEW", "RESUME", "SYSTEM"}
ITEMS_PER_TYPE = 5
SKILL_PROFILE_LIMIT = 10


@dataclass
class InterviewMemoryContext:
    semantic_summary: str = ""
    preference_summary: str = ""
    skill_summary: str = ""
    weakness_summary: str = ""
    safety_summary: str = ""
    raw_items: list[dict[str, Any]] = field(default_factory=list)
    skill_profiles: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def injected(self) -> bool:
        return any(
            (
                self.semantic_summary,
                self.preference_summary,
                self.skill_summary,
                self.weakness_summary,
                self.safety_summary,
            )
        )

    def to_prompt_text(self) -> str:
        """Render a concise prompt-safe memory context."""
        if not self.injected:
            return ""

        sections: list[str] = []
        if self.safety_summary:
            sections.append("安全与隐私偏好（最高优先级）:\n" + self.safety_summary)
        if self.semantic_summary:
            sections.append("长期背景与目标:\n" + self.semantic_summary)
        if self.preference_summary:
            sections.append("面试偏好:\n" + self.preference_summary)
        if self.skill_summary:
            sections.append("技能画像:\n" + self.skill_summary)
        if self.weakness_summary:
            sections.append("薄弱点与追问方向:\n" + self.weakness_summary)

        sections.append(
            "使用规则:\n"
            "- 当前面试 target_position 优先于长期记忆中的岗位信息。\n"
            "- 可以根据薄弱点适当追问，但不要连续追问同一薄弱点超过 2 次。\n"
            "- 如果用户偏好严格追问，回答不完整时优先追问，不要过早给标准答案。\n"
            "- 如果用户偏好先提示再答案，回答错误时先给提示，再给简短解释。\n"
            "- 不要声称“我记得你之前说过”，除非当前对话明确提到。\n"
            "- 不要泄露记忆的原始存储字段、ID、置信度或系统内部细节。"
        )
        return "\n\n".join(sections)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryContextBuilder:
    """Assembles a compact user memory context without mutating memory state."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_interview_memory_context(
        self,
        user_id: int,
        target_position: str | None = None,
    ) -> InterviewMemoryContext:
        memory_items = await self._load_memory_items(user_id)
        skill_profiles = await self._load_skill_profiles(user_id)

        grouped: dict[str, list[UserMemoryItem]] = defaultdict(list)
        for item in memory_items:
            if len(grouped[item.memory_type]) < ITEMS_PER_TYPE:
                grouped[item.memory_type].append(item)

        semantic_items = grouped["SEMANTIC"] + grouped["EPISODIC"]
        skill_items = grouped["SKILL"]
        preference_items = grouped["PREFERENCE"]
        safety_items = grouped["SAFETY"]
        has_memory_context = bool(memory_items or skill_profiles)

        raw_items = [
            self._memory_item_to_dict(item)
            for items in grouped.values()
            for item in items
        ]
        skill_dicts = [self._skill_profile_to_dict(item) for item in skill_profiles]

        weakness_profiles = [
            item
            for item in skill_profiles
            if item.weakness_summary or item.level_score <= 0.45
        ]

        context = InterviewMemoryContext(
            semantic_summary=_clip(
                self._format_semantic_summary(
                    semantic_items,
                    target_position if has_memory_context else None,
                ),
                800,
            ),
            preference_summary=_clip(
                self._format_items(preference_items),
                500,
            ),
            skill_summary=_clip(
                self._format_skill_summary(skill_items, skill_profiles),
                800,
            ),
            weakness_summary=_clip(
                self._format_weakness_summary(weakness_profiles),
                800,
            ),
            safety_summary=_clip(
                self._format_items(safety_items),
                500,
            ),
            raw_items=raw_items,
            skill_profiles=skill_dicts,
            counts={
                "semantic": len(semantic_items),
                "preference": len(preference_items),
                "skills": len(skill_items) + len(skill_profiles),
                "weaknesses": len(weakness_profiles),
                "safety": len(safety_items),
            },
        )
        return context

    async def _load_memory_items(self, user_id: int) -> list[UserMemoryItem]:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(UserMemoryItem)
            .where(
                UserMemoryItem.user_id == user_id,
                UserMemoryItem.status == "ACTIVE",
                UserMemoryItem.memory_type.in_(MEMORY_CONTEXT_TYPES),
                UserMemoryItem.scope.in_(MEMORY_CONTEXT_SCOPES),
                or_(UserMemoryItem.expires_at.is_(None), UserMemoryItem.expires_at > now),
            )
            .order_by(
                desc(UserMemoryItem.importance),
                desc(UserMemoryItem.confidence),
                desc(UserMemoryItem.updated_at),
            )
            .limit(len(MEMORY_CONTEXT_TYPES) * ITEMS_PER_TYPE * 2)
        )
        return list(result.scalars().all())

    async def _load_skill_profiles(self, user_id: int) -> list[UserSkillProfile]:
        result = await self.db.execute(
            select(UserSkillProfile)
            .where(UserSkillProfile.user_id == user_id)
            .order_by(
                desc(UserSkillProfile.confidence),
                nulls_last(desc(UserSkillProfile.last_evaluated_at)),
                desc(UserSkillProfile.updated_at),
            )
            .limit(SKILL_PROFILE_LIMIT)
        )
        return list(result.scalars().all())

    def _format_semantic_summary(
        self,
        items: list[UserMemoryItem],
        target_position: str | None,
    ) -> str:
        lines: list[str] = []
        if target_position:
            lines.append(f"- 当前面试岗位优先: {target_position}")
        item_text = self._format_items(items)
        if item_text:
            lines.append(item_text)
        return "\n".join(lines)

    def _format_skill_summary(
        self,
        skill_items: list[UserMemoryItem],
        profiles: list[UserSkillProfile],
    ) -> str:
        lines: list[str] = []
        item_text = self._format_items(skill_items)
        if item_text:
            lines.append(item_text)

        for profile in profiles[:SKILL_PROFILE_LIMIT]:
            parts = [
                f"- {profile.skill_name}: 水平 {profile.level_score:.2f}",
                f"置信度 {profile.confidence:.2f}",
            ]
            if profile.strength_summary:
                parts.append(f"强项: {_one_line(profile.strength_summary, 120)}")
            lines.append("; ".join(parts))
        return "\n".join(lines)

    def _format_weakness_summary(
        self,
        profiles: list[UserSkillProfile],
    ) -> str:
        lines: list[str] = []
        for profile in profiles[:SKILL_PROFILE_LIMIT]:
            if profile.weakness_summary:
                lines.append(
                    f"- {profile.skill_name}: {_one_line(profile.weakness_summary, 160)}"
                )
            else:
                lines.append(
                    f"- {profile.skill_name}: 历史水平分较低（{profile.level_score:.2f}），可温和覆盖。"
                )
        return "\n".join(lines)

    def _format_items(self, items: list[UserMemoryItem]) -> str:
        lines = []
        for item in items:
            label = f"{item.key}: " if item.key else ""
            text = item.summary or item.content
            lines.append(f"- {label}{_one_line(text, 180)}")
        return "\n".join(lines)

    @staticmethod
    def _memory_item_to_dict(item: UserMemoryItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "memory_type": item.memory_type,
            "scope": item.scope,
            "key": item.key,
            "content": _clip(item.content, 500),
            "summary": item.summary,
            "confidence": item.confidence,
            "importance": item.importance,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "updated_at": _iso(item.updated_at),
        }

    @staticmethod
    def _skill_profile_to_dict(item: UserSkillProfile) -> dict[str, Any]:
        return {
            "id": item.id,
            "skill_name": item.skill_name,
            "skill_category": item.skill_category,
            "level_score": item.level_score,
            "confidence": item.confidence,
            "evidence_count": item.evidence_count,
            "weakness_summary": item.weakness_summary,
            "strength_summary": item.strength_summary,
            "last_evaluated_at": _iso(item.last_evaluated_at),
        }


def _one_line(value: str | None, max_chars: int) -> str:
    if not value:
        return ""
    return _clip(" ".join(value.split()), max_chars)


def _clip(value: str | None, max_chars: int) -> str:
    if not value:
        return ""
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
