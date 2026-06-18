"""Controlled memory writes from completed interview sessions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.interview.models import (
    InterviewMessage,
    InterviewSession,
    InterviewSessionQuestion,
)
from interview_api.modules.memory.models import (
    UserMemoryEvent,
    UserMemoryItem,
    UserSkillProfile,
)


SENSITIVE_PATTERNS = [
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"(?i)(password|passwd|pwd|token|api[_-]?key|secret)\s*[:=]"),
    re.compile(r"(身份证|手机号|住址|家庭住址|密码|令牌|密钥)"),
]

STYLE_PATTERNS = [
    "以后请严格一点",
    "请严格一点",
    "希望你多追问",
    "多追问",
    "不要直接给答案",
    "先给提示",
    "回答完请给我评分",
    "给我评分",
    "按大厂面试风格",
    "大厂面试风格",
]

TARGET_PATTERNS = [
    re.compile(r"(?:我)?目标岗位是\s*([^，。,.；;\n]{2,40})"),
    re.compile(r"(?:我)?准备投\s*([^，。,.；;\n]{2,40})"),
    re.compile(r"(?:我)?主要面试\s*([^，。,.；;\n]{2,40})"),
    re.compile(r"(?:我)?最近准备\s*([^，。,.；;\n]{2,40})"),
]

FOCUS_PATTERNS = [
    re.compile(r"(?:我)?最近主要复习\s*([^，。,.；;\n]{2,40})"),
    re.compile(r"(?:我)?想重点练\s*([^，。,.；;\n]{2,40})"),
    re.compile(r"([^，。,.；;\n]{1,30})比较弱，?想多练([^，。,.；;\n]{1,30})"),
]

NEGATIVE_TERMS = ["不会", "不清楚", "没答上", "遗漏", "答错", "薄弱", "不完整", "不够稳定"]
POSITIVE_TERMS = ["回答较完整", "较好", "准确", "清楚", "到位", "完整"]

SKILL_KEYWORDS: dict[str, list[str]] = {
    "Redis": ["redis", "缓存", "aof", "rdb", "持久化", "缓存穿透", "缓存雪崩", "缓存击穿"],
    "MySQL": ["mysql", "索引", "事务", "mvcc", "锁", "explain", "慢 sql", "慢sql"],
    "Java": ["java", "jvm", "gc", "线程池", "集合", "spring", "spring boot"],
    "Algorithm": ["算法", "leetcode", "动态规划", "二分", "栈", "队列"],
    "Project": ["项目", "架构", "rag", "agent", "fastapi", "milvus", "postgresql"],
    "System Design": ["系统设计", "高并发", "限流", "缓存", "消息队列", "分布式"],
}


@dataclass
class WriterStats:
    preferences_created: int = 0
    preferences_updated: int = 0
    episodic_memory_created: bool = False
    episodic_memory_updated: bool = False
    skills_updated: int = 0
    events_created: int = 0


class InterviewMemoryWriter:
    """Writes memory from explicit user intent and completed interview evidence."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.stats = WriterStats()

    async def capture_explicit_preference_from_message(
        self,
        user_id: int,
        message_id: int | None,
        text: str,
        session_id: int | None = None,
    ) -> list[UserMemoryItem]:
        if not text or _contains_sensitive(text):
            return []

        candidates = self._extract_explicit_memory_candidates(text)
        written: list[UserMemoryItem] = []
        for candidate in candidates:
            item, action = await self._upsert_memory_item(
                user_id=user_id,
                memory_type=candidate["memory_type"],
                scope="INTERVIEW",
                key=candidate["key"],
                content=candidate["content"],
                summary=candidate["summary"],
                confidence=candidate["confidence"],
                importance=candidate["importance"],
                source_type="INTERVIEW_MESSAGE",
                source_id=message_id,
                metadata_json={"session_id": session_id} if session_id else None,
                skip_deleted_match=True,
            )
            if item is None:
                continue
            if action == "created" and candidate["memory_type"] == "PREFERENCE":
                self.stats.preferences_created += 1
            elif action == "updated" and candidate["memory_type"] == "PREFERENCE":
                self.stats.preferences_updated += 1
            written.append(item)
        return written

    async def consolidate_interview_session(
        self,
        user_id: int,
        session_id: int,
        force: bool = False,
    ) -> dict:
        self.stats = WriterStats()
        session = await self._get_session_for_user(user_id, session_id)
        if session is None:
            raise LookupError("interview session not found")

        episodic_key = f"interview_session:{session_id}"
        existing = await self._find_memory_by_key(
            user_id=user_id,
            memory_type="EPISODIC",
            key=episodic_key,
            status="ACTIVE",
        )
        if existing and not force:
            return {
                "session_id": session_id,
                "episodic_memory_created": False,
                "episodic_memory_updated": False,
                "preferences_created": 0,
                "preferences_updated": 0,
                "skills_updated": 0,
                "events_created": 0,
                "existing_memory_id": existing.id,
            }

        messages = await self._load_messages(session_id)
        questions = await self._load_questions(session_id)

        for msg in messages:
            if msg.role == "USER":
                await self.capture_explicit_preference_from_message(
                    user_id=user_id,
                    message_id=msg.id,
                    text=msg.content,
                    session_id=session_id,
                )

        content, summary, importance = self._build_episodic_memory(
            session=session,
            messages=messages,
            questions=questions,
        )
        episodic_item, episodic_action = await self._upsert_memory_item(
            user_id=user_id,
            memory_type="EPISODIC",
            scope="INTERVIEW",
            key=episodic_key,
            content=content,
            summary=summary,
            confidence=0.85,
            importance=importance,
            source_type="INTERVIEW_SESSION",
            source_id=session_id,
            metadata_json={
                "answered_count": _answered_count(questions),
                "question_count": len(questions),
                "target_position": session.target_position,
            },
            force_update_item=existing,
            skip_deleted_match=True,
        )
        self.stats.episodic_memory_created = episodic_action == "created"
        self.stats.episodic_memory_updated = episodic_action == "updated"

        self.stats.skills_updated = await self._update_skill_profiles(
            user_id=user_id,
            session_id=session_id,
            messages=messages,
            questions=questions,
        )

        await self.db.commit()
        return {
            "session_id": session_id,
            "episodic_memory_created": self.stats.episodic_memory_created,
            "episodic_memory_updated": self.stats.episodic_memory_updated,
            "preferences_created": self.stats.preferences_created,
            "preferences_updated": self.stats.preferences_updated,
            "skills_updated": self.stats.skills_updated,
            "events_created": self.stats.events_created,
            "episodic_memory_id": episodic_item.id if episodic_item else None,
        }

    def _extract_explicit_memory_candidates(self, text: str) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        if any(pattern in text for pattern in STYLE_PATTERNS):
            candidates.append(
                {
                    "memory_type": "PREFERENCE",
                    "key": "interview_style_preference",
                    "content": _compact(text, 240),
                    "summary": _compact(text, 120),
                    "confidence": 0.95,
                    "importance": 0.7,
                }
            )

        for pattern in TARGET_PATTERNS:
            match = pattern.search(text)
            if match:
                target = _clean_phrase(match.group(1))
                if target:
                    candidates.append(
                        {
                            "memory_type": "SEMANTIC",
                            "key": "target_position",
                            "content": f"用户明确表达目标岗位: {target}",
                            "summary": f"目标岗位: {target}",
                            "confidence": 0.95,
                            "importance": 0.8,
                        }
                    )
                break

        for pattern in FOCUS_PATTERNS:
            match = pattern.search(text)
            if match:
                focus = _clean_phrase(" ".join(g for g in match.groups() if g))
                if focus:
                    candidates.append(
                        {
                            "memory_type": "SEMANTIC",
                            "key": "preparation_focus",
                            "content": f"用户明确表达近期准备方向: {focus}",
                            "summary": f"准备重点: {focus}",
                            "confidence": 0.9,
                            "importance": 0.7,
                        }
                    )
                break
        return candidates

    async def _upsert_memory_item(
        self,
        *,
        user_id: int,
        memory_type: str,
        scope: str,
        key: str,
        content: str,
        summary: str | None,
        confidence: float,
        importance: float,
        source_type: str,
        source_id: int | None,
        metadata_json: dict | None = None,
        force_update_item: UserMemoryItem | None = None,
        skip_deleted_match: bool = False,
    ) -> tuple[UserMemoryItem | None, str]:
        now = datetime.now(timezone.utc)
        if force_update_item:
            before = _memory_to_dict(force_update_item)
            await self.db.execute(
                update(UserMemoryItem)
                .where(UserMemoryItem.id == force_update_item.id)
                .values(
                    content=content,
                    summary=summary,
                    confidence=confidence,
                    importance=importance,
                    source_type=source_type,
                    source_id=source_id,
                    metadata_json=metadata_json,
                    updated_at=now,
                )
            )
            await self.db.flush()
            updated = await self._get_memory_item(force_update_item.id)
            if updated:
                await self._write_event(user_id, updated.id, "UPDATED", before, _memory_to_dict(updated), "interview_memory_update")
            return updated, "updated"

        match = await self._find_similar_memory(user_id, memory_type, key, content)
        if match and match.status in {"DELETED", "ARCHIVED"} and skip_deleted_match:
            return None, "skipped_deleted"
        if match and match.status == "ACTIVE":
            before = _memory_to_dict(match)
            await self.db.execute(
                update(UserMemoryItem)
                .where(UserMemoryItem.id == match.id)
                .values(
                    confidence=max(match.confidence, confidence),
                    importance=max(match.importance, importance),
                    source_type=match.source_type or source_type,
                    source_id=match.source_id or source_id,
                    metadata_json=match.metadata_json or metadata_json,
                    updated_at=now,
                )
            )
            await self.db.flush()
            updated = await self._get_memory_item(match.id)
            if updated:
                await self._write_event(user_id, updated.id, "UPDATED", before, _memory_to_dict(updated), "interview_memory_dedupe_update")
            return updated, "updated"

        item = UserMemoryItem(
            user_id=user_id,
            memory_type=memory_type,
            scope=scope,
            key=key,
            content=content,
            summary=summary,
            confidence=confidence,
            importance=importance,
            source_type=source_type,
            source_id=source_id,
            metadata_json=metadata_json,
            status="ACTIVE",
            visibility="PRIVATE",
        )
        self.db.add(item)
        await self.db.flush()
        await self._write_event(user_id, item.id, "CREATED", None, _memory_to_dict(item), "interview_memory_create")
        return item, "created"

    async def _update_skill_profiles(
        self,
        *,
        user_id: int,
        session_id: int,
        messages: list[InterviewMessage],
        questions: list[InterviewSessionQuestion],
    ) -> int:
        combined = "\n".join(
            [q.question or "" for q in questions]
            + [q.dimension or "" for q in questions]
            + [m.content or "" for m in messages]
        )
        lowered = combined.lower()
        negative = any(term in combined for term in NEGATIVE_TERMS)
        positive = any(term in combined for term in POSITIVE_TERMS)
        updated_count = 0
        for skill_name, keywords in SKILL_KEYWORDS.items():
            if not any(keyword.lower() in lowered for keyword in keywords):
                continue
            existing = await self._find_skill_profile(user_id, skill_name)
            old_score = existing.level_score if existing else 0.5
            delta = -0.05 if negative else (0.03 if positive else 0.0)
            new_score = _clamp(old_score + delta)
            old_evidence = existing.evidence_count if existing else 0
            new_evidence = old_evidence + 1
            confidence = min(0.95, max((existing.confidence if existing else 0.45), 0.45 + new_evidence * 0.08))
            weakness_summary = (
                f"Session {session_id}: 本场在 {skill_name} 相关回答中出现不清楚、遗漏或薄弱反馈。"
                if negative
                else (existing.weakness_summary if existing else None)
            )
            strength_summary = (
                f"Session {session_id}: 本场在 {skill_name} 相关回答中出现较完整、准确或清楚反馈。"
                if positive
                else (existing.strength_summary if existing else None)
            )
            now = datetime.now(timezone.utc)
            if existing:
                before = _skill_to_dict(existing)
                await self.db.execute(
                    update(UserSkillProfile)
                    .where(UserSkillProfile.id == existing.id)
                    .values(
                        level_score=new_score,
                        confidence=confidence,
                        evidence_count=new_evidence,
                        weakness_summary=weakness_summary,
                        strength_summary=strength_summary,
                        last_evaluated_at=now,
                        updated_at=now,
                        metadata_json={"last_session_id": session_id},
                    )
                )
                await self.db.flush()
                await self._write_event(user_id, None, "UPDATED", before, {
                    "skill_name": skill_name,
                    "level_score": new_score,
                    "confidence": confidence,
                    "evidence_count": new_evidence,
                }, "interview_skill_profile_update")
            else:
                profile = UserSkillProfile(
                    user_id=user_id,
                    skill_name=skill_name,
                    skill_category=_skill_category(skill_name),
                    level_score=new_score,
                    confidence=confidence,
                    evidence_count=new_evidence,
                    weakness_summary=weakness_summary,
                    strength_summary=strength_summary,
                    last_evaluated_at=now,
                    metadata_json={"last_session_id": session_id},
                )
                self.db.add(profile)
                await self.db.flush()
                await self._write_event(user_id, None, "CREATED", None, _skill_to_dict(profile), "interview_skill_profile_create")
            updated_count += 1
        return updated_count

    def _build_episodic_memory(
        self,
        *,
        session: InterviewSession,
        messages: list[InterviewMessage],
        questions: list[InterviewSessionQuestion],
    ) -> tuple[str, str, float]:
        answered = _answered_count(questions)
        dimensions = sorted({q.dimension for q in questions if q.dimension})
        missing_points: list[str] = []
        scores: list[float] = []
        for q in questions:
            if isinstance(q.evaluation_json, dict):
                score = q.evaluation_json.get("score")
                if isinstance(score, (int, float)):
                    scores.append(float(score))
                risk = q.evaluation_json.get("risk_tip")
                if isinstance(risk, str) and risk:
                    missing_points.append(risk)
            if q.missing_points_json:
                if isinstance(q.missing_points_json, list):
                    missing_points.extend(str(x) for x in q.missing_points_json[:3])
                elif isinstance(q.missing_points_json, dict):
                    missing_points.extend(str(x) for x in list(q.missing_points_json.values())[:3])
        avg_score = sum(scores) / len(scores) if scores else None
        recent_user = [m.content for m in messages if m.role == "USER"][-3:]
        summary_base = session.memory_summary or "；".join(_compact(x, 80) for x in recent_user)
        weakness_text = "；".join(_dedupe(missing_points)[:5]) or "暂无明确薄弱点。"
        strength_text = "回答较完整、较好、准确、清楚" if any(
            term in "\n".join(m.content for m in messages) for term in POSITIVE_TERMS
        ) else "暂无明确强项。"
        content = "\n".join(
            [
                f"本场面试目标岗位: {session.target_position or '未指定'}",
                f"问到的主要方向: {', '.join(dimensions[:8]) if dimensions else '暂无明确维度'}",
                f"有效问答数: {answered}/{len(questions)}",
                f"平均评分: {avg_score:.1f}/5" if avg_score is not None else "平均评分: 暂无",
                f"用户表现摘要: {summary_base or '暂无会话摘要'}",
                f"明显薄弱点: {weakness_text}",
                f"明显强项: {strength_text}",
                "后续建议: 优先复盘薄弱点对应题目，并在后续模拟面试中继续覆盖相关维度。",
            ]
        )
        summary = _compact(
            f"{session.target_position or '本场面试'}，有效问答 {answered} 题；主要方向 {', '.join(dimensions[:4]) or '综合'}；薄弱点：{weakness_text}",
            280,
        )
        importance = min(0.9, 0.55 + min(answered, 8) * 0.04)
        return content, summary, importance

    async def _get_session_for_user(self, user_id: int, session_id: int) -> InterviewSession | None:
        result = await self.db.execute(
            select(InterviewSession).where(
                InterviewSession.id == session_id,
                InterviewSession.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def _load_messages(self, session_id: int) -> list[InterviewMessage]:
        result = await self.db.execute(
            select(InterviewMessage)
            .where(InterviewMessage.session_id == session_id)
            .order_by(InterviewMessage.turn_index, InterviewMessage.created_at)
        )
        return list(result.scalars().all())

    async def _load_questions(self, session_id: int) -> list[InterviewSessionQuestion]:
        result = await self.db.execute(
            select(InterviewSessionQuestion)
            .where(InterviewSessionQuestion.session_id == session_id)
            .order_by(InterviewSessionQuestion.question_index)
        )
        return list(result.scalars().all())

    async def _find_memory_by_key(
        self,
        *,
        user_id: int,
        memory_type: str,
        key: str,
        status: str | None = None,
    ) -> UserMemoryItem | None:
        query = select(UserMemoryItem).where(
            UserMemoryItem.user_id == user_id,
            UserMemoryItem.memory_type == memory_type,
            UserMemoryItem.key == key,
        )
        if status:
            query = query.where(UserMemoryItem.status == status)
        result = await self.db.execute(query.order_by(desc(UserMemoryItem.updated_at)).limit(1))
        return result.scalar_one_or_none()

    async def _find_similar_memory(
        self,
        user_id: int,
        memory_type: str,
        key: str,
        content: str,
    ) -> UserMemoryItem | None:
        result = await self.db.execute(
            select(UserMemoryItem).where(
                UserMemoryItem.user_id == user_id,
                UserMemoryItem.memory_type == memory_type,
                UserMemoryItem.key == key,
            )
        )
        normalized = _normalize_text(content)
        for item in result.scalars().all():
            if _similar(_normalize_text(item.content), normalized):
                return item
        return None

    async def _get_memory_item(self, memory_id: int) -> UserMemoryItem | None:
        result = await self.db.execute(select(UserMemoryItem).where(UserMemoryItem.id == memory_id))
        return result.scalar_one_or_none()

    async def _find_skill_profile(self, user_id: int, skill_name: str) -> UserSkillProfile | None:
        result = await self.db.execute(
            select(UserSkillProfile).where(
                UserSkillProfile.user_id == user_id,
                UserSkillProfile.skill_name == skill_name,
            )
        )
        return result.scalar_one_or_none()

    async def _write_event(
        self,
        user_id: int,
        memory_item_id: int | None,
        event_type: str,
        before: dict | None,
        after: dict | None,
        reason: str,
    ) -> None:
        self.db.add(
            UserMemoryEvent(
                user_id=user_id,
                memory_item_id=memory_item_id,
                event_type=event_type,
                actor_type="SYSTEM",
                actor_id=None,
                before_json=before,
                after_json=after,
                reason=reason,
            )
        )
        await self.db.flush()
        self.stats.events_created += 1


def _contains_sensitive(text: str) -> bool:
    return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _similar(left: str, right: str) -> bool:
    if left == right:
        return True
    if not left or not right:
        return False
    shorter, longer = sorted([left, right], key=len)
    return len(shorter) >= 12 and shorter in longer


def _clean_phrase(value: str) -> str:
    value = re.sub(r"[。,.，；;！!？?].*$", "", value).strip()
    return _compact(value, 60)


def _compact(value: str | None, max_chars: int) -> str:
    if not value:
        return ""
    text = " ".join(value.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = _compact(value, 120)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _answered_count(questions: list[InterviewSessionQuestion]) -> int:
    return len([q for q in questions if q.status in {"ANSWERED", "ASKED", "SKIPPED"}])


def _skill_category(skill_name: str) -> str:
    if skill_name in {"Redis", "MySQL"}:
        return "backend"
    if skill_name == "Algorithm":
        return "algorithm"
    if skill_name == "System Design":
        return "architecture"
    return "engineering"


def _memory_to_dict(item: UserMemoryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "memory_type": item.memory_type,
        "scope": item.scope,
        "key": item.key,
        "content": item.content,
        "summary": item.summary,
        "confidence": item.confidence,
        "importance": item.importance,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "status": item.status,
        "updated_at": _iso(item.updated_at),
    }


def _skill_to_dict(item: UserSkillProfile) -> dict[str, Any]:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "skill_name": item.skill_name,
        "skill_category": item.skill_category,
        "level_score": item.level_score,
        "confidence": item.confidence,
        "evidence_count": item.evidence_count,
        "weakness_summary": item.weakness_summary,
        "strength_summary": item.strength_summary,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
