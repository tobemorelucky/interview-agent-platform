"""Repository helpers for admin review of extracted experience candidates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Text, and_, cast, desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.experience.models import (
    ExperienceAgentRun,
    ExperienceAgentStepRun,
    ExperienceCollectionTask,
    ExperienceSourceItem,
    InterviewExperience,
    InterviewQuestion,
)


class ExperienceReviewRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_items(
        self,
        *,
        review_status: str | None = None,
        company: str | None = None,
        position: str | None = None,
        platform: str | None = None,
        reliability_min: float | None = None,
        reliability_max: float | None = None,
        risk_flag: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        question_count = (
            select(
                InterviewQuestion.experience_id.label("experience_id"),
                func.count(InterviewQuestion.id).label("question_count"),
            )
            .group_by(InterviewQuestion.experience_id)
            .subquery()
        )
        query = (
            select(
                InterviewExperience,
                ExperienceSourceItem.title,
                func.coalesce(question_count.c.question_count, 0).label("question_count"),
            )
            .outerjoin(ExperienceSourceItem, InterviewExperience.source_item_id == ExperienceSourceItem.id)
            .outerjoin(question_count, InterviewExperience.id == question_count.c.experience_id)
        )
        count_query = (
            select(func.count(InterviewExperience.id))
            .outerjoin(ExperienceSourceItem, InterviewExperience.source_item_id == ExperienceSourceItem.id)
        )
        filters = self._filters(
            review_status=review_status,
            company=company,
            position=position,
            platform=platform,
            reliability_min=reliability_min,
            reliability_max=reliability_max,
            risk_flag=risk_flag,
            keyword=keyword,
        )
        if filters:
            query = query.where(*filters)
            count_query = count_query.where(*filters)
        total = int((await self.db.execute(count_query)).scalar() or 0)
        rows = (
            await self.db.execute(
                query.order_by(desc(InterviewExperience.updated_at))
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [
            self._item_dict(experience, title, int(question_count_value or 0))
            for experience, title, question_count_value in rows
        ], total

    async def get_experience(self, experience_id: int) -> InterviewExperience | None:
        result = await self.db.execute(
            select(InterviewExperience).where(InterviewExperience.id == experience_id)
        )
        return result.scalar_one_or_none()

    async def get_question(self, question_id: int) -> InterviewQuestion | None:
        result = await self.db.execute(
            select(InterviewQuestion).where(InterviewQuestion.id == question_id)
        )
        return result.scalar_one_or_none()

    async def get_source(self, source_item_id: int | None) -> ExperienceSourceItem | None:
        if not source_item_id:
            return None
        result = await self.db.execute(
            select(ExperienceSourceItem).where(ExperienceSourceItem.id == source_item_id)
        )
        return result.scalar_one_or_none()

    async def list_questions(self, experience_id: int) -> list[InterviewQuestion]:
        result = await self.db.execute(
            select(InterviewQuestion)
            .where(InterviewQuestion.experience_id == experience_id)
            .order_by(InterviewQuestion.id.asc())
        )
        return list(result.scalars().all())

    async def latest_agent_run(self, source_item_id: int | None) -> ExperienceAgentRun | None:
        if not source_item_id:
            return None
        result = await self.db.execute(
            select(ExperienceAgentRun)
            .where(ExperienceAgentRun.source_item_id == source_item_id)
            .order_by(ExperienceAgentRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_agent_steps(self, agent_run_id: int | None) -> list[ExperienceAgentStepRun]:
        if not agent_run_id:
            return []
        result = await self.db.execute(
            select(ExperienceAgentStepRun)
            .where(ExperienceAgentStepRun.agent_run_id == agent_run_id)
            .order_by(ExperienceAgentStepRun.id.asc())
        )
        return list(result.scalars().all())

    async def update_experience(
        self,
        experience_id: int,
        **values,
    ) -> InterviewExperience | None:
        if not values:
            return await self.get_experience(experience_id)
        await self.db.execute(
            update(InterviewExperience)
            .where(InterviewExperience.id == experience_id)
            .values(updated_at=datetime.now(timezone.utc), **values)
        )
        await self.db.flush()
        return await self.get_experience(experience_id)

    async def update_question(
        self,
        question_id: int,
        **values,
    ) -> InterviewQuestion | None:
        if not values:
            return await self.get_question(question_id)
        await self.db.execute(
            update(InterviewQuestion)
            .where(InterviewQuestion.id == question_id)
            .values(updated_at=datetime.now(timezone.utc), **values)
        )
        await self.db.flush()
        return await self.get_question(question_id)

    async def update_questions_for_experience(
        self,
        experience_id: int,
        **values,
    ) -> None:
        await self.db.execute(
            update(InterviewQuestion)
            .where(InterviewQuestion.experience_id == experience_id)
            .values(updated_at=datetime.now(timezone.utc), **values)
        )
        await self.db.flush()

    async def approve_indexable_questions(self, experience_id: int) -> None:
        await self.db.execute(
            update(InterviewQuestion)
            .where(
                InterviewQuestion.experience_id == experience_id,
                InterviewQuestion.should_index.is_(True),
            )
            .values(review_status="APPROVED", updated_at=datetime.now(timezone.utc))
        )
        await self.db.flush()

    async def update_source_extract_status(
        self,
        source_item_id: int | None,
        *,
        extract_status: str,
        error_message: str | None = None,
    ) -> None:
        if not source_item_id:
            return
        await self.db.execute(
            update(ExperienceSourceItem)
            .where(ExperienceSourceItem.id == source_item_id)
            .values(
                extract_status=extract_status,
                error_message=error_message,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()

    async def refresh_task_review_counts(self, task_id: int | None) -> None:
        if not task_id:
            return
        approved_count = (
            await self.db.execute(
                select(func.count(InterviewExperience.id)).where(
                    InterviewExperience.task_id == task_id,
                    InterviewExperience.review_status == "APPROVED",
                )
            )
        ).scalar() or 0
        await self.db.execute(
            update(ExperienceCollectionTask)
            .where(ExperienceCollectionTask.id == task_id)
            .values(approved_count=int(approved_count))
        )
        await self.db.flush()

    def _filters(self, **params) -> list:
        filters = []
        if params.get("review_status"):
            filters.append(InterviewExperience.review_status == params["review_status"])
        if params.get("company"):
            filters.append(InterviewExperience.company.ilike(f"%{params['company']}%"))
        if params.get("position"):
            filters.append(InterviewExperience.position.ilike(f"%{params['position']}%"))
        if params.get("platform"):
            filters.append(InterviewExperience.platform == params["platform"])
        if params.get("reliability_min") is not None:
            filters.append(InterviewExperience.reliability_score >= params["reliability_min"])
        if params.get("reliability_max") is not None:
            filters.append(InterviewExperience.reliability_score <= params["reliability_max"])
        if params.get("risk_flag"):
            filters.append(
                cast(InterviewExperience.reliability_json, Text).ilike(
                    f"%{params['risk_flag']}%"
                )
            )
        if params.get("keyword"):
            keyword = f"%{params['keyword']}%"
            filters.append(
                or_(
                    InterviewExperience.company.ilike(keyword),
                    InterviewExperience.position.ilike(keyword),
                    InterviewExperience.summary.ilike(keyword),
                    ExperienceSourceItem.title.ilike(keyword),
                    InterviewExperience.source_url.ilike(keyword),
                )
            )
        return filters

    def _item_dict(
        self,
        experience: InterviewExperience,
        title: str | None,
        question_count: int,
    ) -> dict[str, Any]:
        reliability_json = experience.reliability_json or {}
        return {
            "experience_id": experience.id,
            "source_item_id": experience.source_item_id,
            "task_id": experience.task_id,
            "title": title,
            "source_url": experience.source_url,
            "platform": experience.platform,
            "company": experience.company,
            "position": experience.position,
            "round_name": experience.interview_round,
            "summary": experience.summary,
            "question_count": question_count,
            "reliability_score": experience.reliability_score,
            "review_status": experience.review_status,
            "risk_flags": reliability_json.get("risk_flags") or [],
            "quality_flags": experience.quality_flags_json or reliability_json.get("quality_flags") or [],
            "created_at": experience.created_at,
            "updated_at": experience.updated_at,
        }
