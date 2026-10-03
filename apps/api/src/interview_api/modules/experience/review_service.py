"""Admin review service for agent-produced interview experience candidates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from interview_api.modules.experience.models import InterviewExperience, InterviewQuestion
from interview_api.modules.experience.review_repository import ExperienceReviewRepository


REVIEW_STATUSES = {
    "WAITING_REVIEW",
    "NEEDS_MANUAL_CHECK",
    "APPROVED",
    "REJECTED",
    "NEEDS_RERUN",
}
QUESTION_REVIEW_STATUSES = {"WAITING_REVIEW", "APPROVED", "REJECTED"}
ANSWER_SOURCES = {"ORIGINAL", "LLM_GENERATED", "HYBRID", "NONE"}


class ExperienceReviewService:
    def __init__(self, db):
        self.db = db
        self.repo = ExperienceReviewRepository(db)

    async def list_items(self, **filters) -> dict:
        items, total = await self.repo.list_items(**filters)
        return {"items": items, "total": total}

    async def get_detail(self, experience_id: int) -> dict:
        experience = await self.repo.get_experience(experience_id)
        if not experience:
            raise LookupError("review_item_not_found")
        source = await self.repo.get_source(experience.source_item_id)
        questions = await self.repo.list_questions(experience.id)
        run = await self.repo.latest_agent_run(experience.source_item_id)
        steps = await self.repo.list_agent_steps(run.id if run else None)
        raw_text = source.raw_text if source else None
        return {
            "experience": self._experience_detail(experience, source, raw_text),
            "agent": {
                "agent_run_id": run.id if run else None,
                "graph_name": run.graph_name if run else None,
                "graph_version": run.graph_version if run else None,
                "status": run.status if run else None,
                "error_message": run.error_message if run else None,
                "started_at": run.started_at if run else None,
                "finished_at": run.finished_at if run else None,
                "step_runs": [self._step_dict(step) for step in steps],
            },
            "questions": [self._question_dict(question) for question in questions],
        }

    async def update_experience(self, experience_id: int, values: dict[str, Any]) -> dict:
        experience = await self.repo.get_experience(experience_id)
        if not experience:
            raise LookupError("review_item_not_found")
        allowed = {
            "company": values.get("company"),
            "position": values.get("position"),
            "interview_round": values.get("round_name"),
            "summary": values.get("summary"),
            "review_note": values.get("review_note"),
        }
        update_values = {key: value for key, value in allowed.items() if value is not None}
        before = self._experience_audit_summary(experience)
        updated = await self.repo.update_experience(experience_id, **update_values)
        return {
            "item": await self.get_detail(experience_id),
            "before": before,
            "after": self._experience_audit_summary(updated),
        }

    async def update_question(self, question_id: int, values: dict[str, Any]) -> dict:
        question = await self.repo.get_question(question_id)
        if not question:
            raise LookupError("review_question_not_found")
        update_values: dict[str, Any] = {}
        for key in [
            "question",
            "original_answer",
            "standard_answer",
            "answer_source",
            "question_type",
            "difficulty",
            "should_index",
            "evidence",
            "review_status",
        ]:
            if key in values and values[key] is not None:
                update_values[key] = values[key]
        if "technical_categories" in values and values["technical_categories"] is not None:
            update_values["technical_categories_json"] = values["technical_categories"]
        self._validate_question_update(update_values)
        before = self._question_audit_summary(question)
        updated = await self.repo.update_question(question_id, **update_values)
        return {
            "question": self._question_dict(updated),
            "before": before,
            "after": self._question_audit_summary(updated),
        }

    async def approve(self, experience_id: int, reviewer_id: int) -> dict:
        experience = await self.repo.get_experience(experience_id)
        if not experience:
            raise LookupError("review_item_not_found")
        before = self._experience_audit_summary(experience)
        now = datetime.now(timezone.utc)
        await self.repo.update_experience(
            experience_id,
            review_status="APPROVED",
            reviewed_by=reviewer_id,
            reviewed_at=now,
            reject_reason=None,
        )
        await self.repo.approve_indexable_questions(experience_id)
        await self.repo.refresh_task_review_counts(experience.task_id)
        updated = await self.repo.get_experience(experience_id)
        return {
            "item": await self.get_detail(experience_id),
            "before": before,
            "after": self._experience_audit_summary(updated),
        }

    async def reject(self, experience_id: int, reviewer_id: int, reason: str) -> dict:
        experience = await self.repo.get_experience(experience_id)
        if not experience:
            raise LookupError("review_item_not_found")
        before = self._experience_audit_summary(experience)
        now = datetime.now(timezone.utc)
        await self.repo.update_experience(
            experience_id,
            review_status="REJECTED",
            reviewed_by=reviewer_id,
            reviewed_at=now,
            reject_reason=reason,
        )
        await self.repo.update_questions_for_experience(
            experience_id,
            review_status="REJECTED",
        )
        await self.repo.refresh_task_review_counts(experience.task_id)
        updated = await self.repo.get_experience(experience_id)
        return {
            "item": await self.get_detail(experience_id),
            "before": before,
            "after": self._experience_audit_summary(updated),
        }

    async def request_rerun(
        self,
        experience_id: int,
        reviewer_id: int,
        *,
        reason: str | None,
        force: bool,
    ) -> dict:
        experience = await self.repo.get_experience(experience_id)
        if not experience:
            raise LookupError("review_item_not_found")
        before = self._experience_audit_summary(experience)
        now = datetime.now(timezone.utc)
        note = reason or "request_rerun"
        await self.repo.update_experience(
            experience_id,
            review_status="NEEDS_RERUN",
            reviewed_by=reviewer_id,
            reviewed_at=now,
            review_note=note,
        )
        if force:
            await self.repo.update_source_extract_status(
                experience.source_item_id,
                extract_status="NEEDS_RERUN",
                error_message=f"review_rerun_requested: {note}"[:2000],
            )
        updated = await self.repo.get_experience(experience_id)
        return {
            "item": await self.get_detail(experience_id),
            "before": before,
            "after": self._experience_audit_summary(updated),
        }

    def _validate_question_update(self, values: dict[str, Any]) -> None:
        if values.get("answer_source") and values["answer_source"] not in ANSWER_SOURCES:
            raise ValueError("invalid_answer_source")
        if values.get("review_status") and values["review_status"] not in QUESTION_REVIEW_STATUSES:
            raise ValueError("invalid_question_review_status")

    def _experience_detail(
        self,
        experience: InterviewExperience,
        source,
        raw_text: str | None,
    ) -> dict[str, Any]:
        reliability_json = experience.reliability_json or {}
        quality_gate_json = experience.quality_gate_json or {}
        return {
            "id": experience.id,
            "source_item_id": experience.source_item_id,
            "task_id": experience.task_id,
            "company": experience.company,
            "position": experience.position,
            "round_name": experience.interview_round,
            "summary": experience.summary,
            "source_url": experience.source_url,
            "platform": experience.platform,
            "title": source.title if source else None,
            "snippet": source.snippet if source else None,
            "raw_text_preview": (raw_text or "")[:2000],
            "extraction_json": experience.extraction_output_json,
            "routing_json": experience.routing_json,
            "reliability_json": experience.reliability_json,
            "quality_gate_json": experience.quality_gate_json,
            "risk_flags": reliability_json.get("risk_flags") or [],
            "quality_flags": experience.quality_flags_json
            or reliability_json.get("quality_flags")
            or [],
            "quality_gate_reasons": quality_gate_json.get("reasons") or [],
            "reliability_score": experience.reliability_score,
            "review_status": experience.review_status,
            "review_note": experience.review_note,
            "reviewer_id": experience.reviewed_by,
            "reviewed_at": experience.reviewed_at,
            "reject_reason": experience.reject_reason,
            "created_at": experience.created_at,
            "updated_at": experience.updated_at,
        }

    def _question_dict(self, question: InterviewQuestion | None) -> dict[str, Any]:
        if not question:
            return {}
        return {
            "question_id": question.id,
            "experience_id": question.experience_id,
            "question": question.question,
            "original_answer": question.original_answer,
            "standard_answer": question.standard_answer,
            "answer_source": question.answer_source,
            "evidence": question.evidence,
            "question_type": question.question_type,
            "technical_categories": question.technical_categories_json or [],
            "difficulty": question.difficulty,
            "should_index": question.should_index,
            "confidence": question.confidence,
            "review_status": question.review_status,
            "index_status": question.index_status,
        }

    def _step_dict(self, step) -> dict[str, Any]:
        output = step.output_json or {}
        return {
            "id": step.id,
            "step_name": step.step_name,
            "status": step.status,
            "error_message": step.error_message,
            "latency_ms": step.latency_ms,
            "model_name": step.model_name,
            "created_at": step.created_at,
            "output_summary": self._summarize_step_output(output),
        }

    def _summarize_step_output(self, output: dict[str, Any]) -> dict[str, Any]:
        keys = [
            "is_interview_experience",
            "question_count",
            "extraction_confidence",
            "routing_confidence",
            "indexable_question_count",
            "is_reliable",
            "reliability_score",
            "spam_risk_score",
            "review_status",
            "recommended_action",
            "reasons",
        ]
        return {key: output[key] for key in keys if key in output}

    def _experience_audit_summary(self, experience: InterviewExperience | None) -> dict[str, Any]:
        if not experience:
            return {}
        return {
            "experience_id": experience.id,
            "company": experience.company,
            "position": experience.position,
            "round_name": experience.interview_round,
            "summary": experience.summary,
            "review_status": experience.review_status,
            "review_note": experience.review_note,
            "reject_reason": experience.reject_reason,
            "reviewed_by": experience.reviewed_by,
            "reviewed_at": experience.reviewed_at.isoformat() if experience.reviewed_at else None,
        }

    def _question_audit_summary(self, question: InterviewQuestion | None) -> dict[str, Any]:
        if not question:
            return {}
        return {
            "question_id": question.id,
            "question": question.question,
            "standard_answer": question.standard_answer,
            "answer_source": question.answer_source,
            "question_type": question.question_type,
            "technical_categories": question.technical_categories_json or [],
            "difficulty": question.difficulty,
            "should_index": question.should_index,
            "review_status": question.review_status,
        }
