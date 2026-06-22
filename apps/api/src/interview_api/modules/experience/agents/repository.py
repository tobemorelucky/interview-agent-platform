"""Persistence helpers for experience agent runs and extraction drafts."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.experience.models import (
    ExperienceAgentRun,
    ExperienceAgentStepRun,
    ExperienceCollectionTask,
    ExperienceSourceItem,
    InterviewExperience,
    InterviewQuestion,
)


class ExperienceAgentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_source_item(self, source_id: int) -> ExperienceSourceItem | None:
        result = await self.db.execute(
            select(ExperienceSourceItem).where(ExperienceSourceItem.id == source_id)
        )
        return result.scalar_one_or_none()

    async def create_run(
        self,
        *,
        source_item_id: int,
        task_id: int | None,
        input_hash: str,
    ) -> ExperienceAgentRun:
        now = datetime.now(timezone.utc)
        run = ExperienceAgentRun(
            source_item_id=source_item_id,
            task_id=task_id,
            status="RUNNING",
            input_hash=input_hash,
            started_at=now,
        )
        self.db.add(run)
        await self.db.flush()
        return run

    async def update_run(self, run_id: int, **values) -> ExperienceAgentRun | None:
        await self.db.execute(
            update(ExperienceAgentRun)
            .where(ExperienceAgentRun.id == run_id)
            .values(updated_at=datetime.now(timezone.utc), **values)
        )
        await self.db.flush()
        result = await self.db.execute(
            select(ExperienceAgentRun).where(ExperienceAgentRun.id == run_id)
        )
        return result.scalar_one_or_none()

    async def create_step_run(
        self,
        *,
        agent_run_id: int,
        step_name: str,
        status: str,
        input_json: dict | None = None,
        output_json: dict | None = None,
        error_message: str | None = None,
        latency_ms: int | None = None,
        model_name: str | None = None,
        token_usage_json: dict | None = None,
    ) -> ExperienceAgentStepRun:
        step = ExperienceAgentStepRun(
            agent_run_id=agent_run_id,
            step_name=step_name,
            status=status,
            input_json=input_json,
            output_json=output_json,
            error_message=error_message,
            latency_ms=latency_ms,
            model_name=model_name,
            token_usage_json=token_usage_json,
        )
        self.db.add(step)
        await self.db.flush()
        return step

    async def latest_experience_for_source(
        self, source_item_id: int
    ) -> InterviewExperience | None:
        result = await self.db.execute(
            select(InterviewExperience)
            .where(InterviewExperience.source_item_id == source_item_id)
            .order_by(InterviewExperience.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_questions_for_experience(self, experience_id: int) -> int:
        result = await self.db.execute(
            select(func.count(InterviewQuestion.id)).where(
                InterviewQuestion.experience_id == experience_id
            )
        )
        return int(result.scalar() or 0)

    async def count_indexable_questions_for_experience(self, experience_id: int) -> int:
        result = await self.db.execute(
            select(func.count(InterviewQuestion.id)).where(
                InterviewQuestion.experience_id == experience_id,
                InterviewQuestion.should_index.is_(True),
            )
        )
        return int(result.scalar() or 0)

    async def delete_extraction_drafts_for_source(self, source_item_id: int) -> None:
        await self.db.execute(
            delete(InterviewExperience).where(
                InterviewExperience.source_item_id == source_item_id
            )
        )
        await self.db.flush()

    async def create_experience_draft(
        self,
        *,
        source: ExperienceSourceItem,
        company: str | None,
        position: str | None,
        job_direction: str | None = None,
        round_name: str | None,
        summary: str,
        extraction_confidence: float,
        extraction_output_json: dict,
        routing_json: dict | None = None,
        reliability_json: dict | None = None,
        quality_gate_json: dict | None = None,
        reliability_score: float | None = None,
        quality_flags_json: list | None = None,
        review_status: str = "WAITING_REVIEW",
    ) -> InterviewExperience:
        experience = InterviewExperience(
            source_item_id=source.id,
            task_id=source.task_id,
            source_url=source.source_url,
            platform=source.platform,
            company=company,
            position=position,
            job_direction=job_direction,
            interview_round=round_name,
            summary=summary,
            content_text=source.raw_text,
            tags_json=(routing_json or {}).get("suggested_tags") or [],
            reliability_score=reliability_score,
            extraction_confidence=extraction_confidence,
            extraction_output_json=extraction_output_json,
            routing_json=routing_json,
            reliability_json=reliability_json,
            quality_gate_json=quality_gate_json,
            quality_flags_json=quality_flags_json or [],
            review_status=review_status,
        )
        self.db.add(experience)
        await self.db.flush()
        return experience

    async def create_question_drafts(
        self,
        *,
        experience: InterviewExperience,
        questions: list[dict],
        routing_result: dict | None = None,
        review_status: str = "WAITING_REVIEW",
        reliability_score: float | None = None,
    ) -> list[InterviewQuestion]:
        created: list[InterviewQuestion] = []
        route_by_index = {
            item.get("question_index"): item
            for item in (routing_result or {}).get("question_results", [])
            if isinstance(item, dict)
        }
        for item in questions:
            route = route_by_index.get(len(created), {})
            question = InterviewQuestion(
                experience_id=experience.id,
                question=route.get("normalized_question") or item["question"],
                original_answer=item.get("original_answer"),
                standard_answer=item.get("standard_answer"),
                answer_source=item.get("answer_source") or "NONE",
                evidence=item.get("evidence"),
                question_type=route.get("question_type") or item.get("question_type"),
                category=(
                    (route.get("technical_categories") or [None])[0]
                    or item.get("question_type")
                ),
                difficulty=route.get("difficulty"),
                company=experience.company,
                position=experience.position,
                interview_round=experience.interview_round,
                source_url=experience.source_url,
                reliability_score=reliability_score,
                confidence=item.get("confidence"),
                technical_categories_json=route.get("technical_categories") or [],
                should_index=bool(route.get("should_index", True)),
                tags_json=route.get("target_banks") or [],
                routing_json=route or {},
                review_status=review_status,
                index_status="NOT_INDEXED",
            )
            self.db.add(question)
            created.append(question)
        await self.db.flush()
        return created

    async def update_source_extract_status(
        self,
        source_id: int,
        *,
        extract_status: str,
        error_message: str | None = None,
    ) -> None:
        await self.db.execute(
            update(ExperienceSourceItem)
            .where(ExperienceSourceItem.id == source_id)
            .values(extract_status=extract_status, error_message=error_message)
        )
        await self.db.flush()

    async def refresh_task_extraction_counts(self, task_id: int) -> None:
        extracted_count = (
            await self.db.execute(
                select(func.count(ExperienceSourceItem.id)).where(
                    ExperienceSourceItem.task_id == task_id,
                    ExperienceSourceItem.extract_status == "EXTRACTED",
                )
            )
        ).scalar() or 0
        question_count = (
            await self.db.execute(
                select(func.count(InterviewQuestion.id))
                .join(InterviewExperience, InterviewQuestion.experience_id == InterviewExperience.id)
                .where(InterviewExperience.task_id == task_id)
            )
        ).scalar() or 0
        await self.db.execute(
            update(ExperienceCollectionTask)
            .where(ExperienceCollectionTask.id == task_id)
            .values(
                extracted_count=int(extracted_count),
                question_count=int(question_count),
            )
        )
        await self.db.flush()
