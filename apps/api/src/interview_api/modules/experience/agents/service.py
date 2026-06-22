"""Service entrypoint for experience Extraction Agent runs."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.config import settings
from interview_api.core.errors import AppError
from interview_api.infrastructure.llm import LLMProvider
from interview_api.infrastructure.llm.provider import OpenAICompatibleLLMProvider
from interview_api.modules.experience.agents.extraction_agent import ExtractionAgent
from interview_api.modules.experience.agents.graph import build_experience_extraction_graph
from interview_api.modules.experience.agents.repository import ExperienceAgentRepository
from interview_api.modules.experience.agents.schemas import ExtractionExperience
from interview_api.modules.experience.agents.state import ExperienceAgentState
from interview_api.modules.experience.agents.validators import validate_extraction_result

logger = logging.getLogger(__name__)

MIN_RAW_TEXT_CHARS = 300


class ExperienceAgentService:
    def __init__(self, db: AsyncSession, llm: LLMProvider | None = None):
        self.db = db
        self.repo = ExperienceAgentRepository(db)
        self.llm = llm

    async def run_extraction_for_source(
        self,
        source_id: int,
        *,
        force: bool = False,
    ) -> dict:
        source = await self.repo.get_source_item(source_id)
        if not source:
            raise LookupError("来源不存在")
        if source.fetch_status != "FETCHED":
            raise ValueError("只有 FETCHED 状态的来源可以执行面经抽取")
        raw_text = source.raw_text or ""
        if len(raw_text.strip()) < MIN_RAW_TEXT_CHARS:
            raise ValueError("raw_text 为空或正文过短，无法执行面经抽取")

        if source.extract_status == "EXTRACTED" and not force:
            existing = await self.repo.latest_experience_for_source(source.id)
            if existing:
                question_count = await self.repo.count_questions_for_experience(existing.id)
                return {
                    "source_id": source.id,
                    "agent_run_id": None,
                    "is_interview_experience": True,
                    "experience_id": existing.id,
                    "question_count": question_count,
                    "status": existing.review_status,
                    "extract_status": source.extract_status,
                    "skipped": True,
                    "error_message": None,
                }

        llm = self.llm or self._build_default_llm()
        agent = ExtractionAgent(llm, model_name=settings.llm_model)
        input_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        run = await self.repo.create_run(
            source_item_id=source.id,
            task_id=source.task_id,
            input_hash=input_hash,
        )
        await self.db.commit()

        initial_state: ExperienceAgentState = {
            "source_item_id": source.id,
            "task_id": source.task_id,
            "source_url": source.source_url,
            "title": source.title,
            "snippet": source.snippet,
            "platform": source.platform,
            "raw_text": raw_text,
            "extraction_result": None,
            "validation_errors": [],
            "is_valid": False,
            "run_id": run.id,
            "status": "RUNNING",
            "saved_result": None,
        }

        async def extraction_node(state: ExperienceAgentState) -> dict:
            started = time.perf_counter()
            step_input = {
                "source_item_id": state["source_item_id"],
                "raw_text_chars": len(state["raw_text"]),
                "title": state.get("title"),
                "source_url": state["source_url"],
            }
            try:
                result = await agent.run(state)
            except (ValueError, ValidationError) as e:
                error = str(e)[:2000]
                await self._record_step(
                    run.id,
                    "extraction",
                    "VALIDATION_FAILED",
                    started,
                    input_json=step_input,
                    error_message=error,
                    model_name=settings.llm_model,
                )
                return {
                    "validation_errors": [f"extraction_parse_failed: {error}"],
                    "is_valid": False,
                    "status": "VALIDATION_FAILED",
                }
            payload = result.model_dump()
            await self._record_step(
                run.id,
                "extraction",
                "SUCCEEDED",
                started,
                input_json=step_input,
                output_json={
                    "is_interview_experience": result.is_interview_experience,
                    "question_count": len(result.questions),
                    "extraction_confidence": result.extraction_confidence,
                },
                model_name=settings.llm_model,
            )
            return {"extraction_result": payload, "status": "EXTRACTED"}

        async def validation_node(state: ExperienceAgentState) -> dict:
            started = time.perf_counter()
            if not state.get("extraction_result"):
                await self._record_step(
                    run.id,
                    "validation",
                    "VALIDATION_FAILED",
                    started,
                    input_json={"has_extraction_result": False},
                    output_json={"errors": state.get("validation_errors", [])},
                )
                return {}
            result = ExtractionExperience.model_validate(state["extraction_result"])
            validation = validate_extraction_result(result, state["raw_text"])
            await self._record_step(
                run.id,
                "validation",
                "SUCCEEDED" if validation.is_valid else "VALIDATION_FAILED",
                started,
                input_json={
                    "is_interview_experience": result.is_interview_experience,
                    "question_count": len(result.questions),
                },
                output_json={
                    "is_valid": validation.is_valid,
                    "errors": validation.errors,
                    "warnings": validation.warnings,
                },
            )
            return {
                "is_valid": validation.is_valid,
                "validation_errors": validation.errors,
                "status": "VALIDATED" if validation.is_valid else "VALIDATION_FAILED",
            }

        async def save_result_node(state: ExperienceAgentState) -> dict:
            started = time.perf_counter()
            saved = await self._save_extraction_state(state, force=force)
            await self._record_step(
                run.id,
                "save_result",
                "SUCCEEDED" if saved.get("saved") else saved.get("status", "SKIPPED"),
                started,
                input_json={
                    "is_valid": state.get("is_valid"),
                    "status": state.get("status"),
                },
                output_json=saved,
            )
            return {"saved_result": saved, "status": saved.get("status", state.get("status", ""))}

        graph = build_experience_extraction_graph(
            extraction_node=extraction_node,
            validation_node=validation_node,
            save_result_node=save_result_node,
        )

        try:
            final_state = await graph.ainvoke(initial_state)
        except Exception as e:
            error = str(e)[:2000]
            await self.repo.update_source_extract_status(
                source.id, extract_status="EXTRACT_FAILED", error_message=error
            )
            await self.repo.update_run(
                run.id,
                status="FAILED",
                error_message=error,
                finished_at=datetime.now(timezone.utc),
            )
            await self.db.commit()
            logger.exception("[experience-agent] extraction failed source=%s run=%s", source.id, run.id)
            raise

        extraction_result = final_state.get("extraction_result")
        saved_result = final_state.get("saved_result") or {}
        validation_errors = final_state.get("validation_errors") or []
        run_status = "SUCCEEDED"
        if final_state.get("status") == "VALIDATION_FAILED" or validation_errors:
            run_status = "VALIDATION_FAILED"
        await self.repo.update_run(
            run.id,
            status=run_status,
            output_json=extraction_result,
            error_message="; ".join(validation_errors)[:2000] if validation_errors else None,
            finished_at=datetime.now(timezone.utc),
        )
        await self.db.commit()

        return {
            "source_id": source.id,
            "agent_run_id": run.id,
            "is_interview_experience": bool(saved_result.get("is_interview_experience")),
            "experience_id": saved_result.get("experience_id"),
            "question_count": saved_result.get("question_count", 0),
            "status": saved_result.get("review_status") or saved_result.get("status"),
            "extract_status": saved_result.get("extract_status"),
            "skipped": False,
            "error_message": "; ".join(validation_errors)[:2000] if validation_errors else saved_result.get("error_message"),
        }

    def _build_default_llm(self) -> LLMProvider:
        if not settings.llm_api_key or not settings.llm_model:
            raise AppError(
                "LLM_NOT_CONFIGURED",
                "LLM is not configured. Please set LLM_API_KEY and LLM_MODEL.",
                status_code=400,
            )
        return OpenAICompatibleLLMProvider()

    async def _record_step(
        self,
        run_id: int,
        step_name: str,
        status: str,
        started: float,
        *,
        input_json: dict | None = None,
        output_json: dict | None = None,
        error_message: str | None = None,
        model_name: str | None = None,
    ) -> None:
        await self.repo.create_step_run(
            agent_run_id=run_id,
            step_name=step_name,
            status=status,
            input_json=input_json,
            output_json=output_json,
            error_message=error_message,
            latency_ms=int((time.perf_counter() - started) * 1000),
            model_name=model_name,
        )
        await self.db.flush()

    async def _save_extraction_state(
        self,
        state: ExperienceAgentState,
        *,
        force: bool,
    ) -> dict[str, Any]:
        source = await self.repo.get_source_item(state["source_item_id"])
        if not source:
            return {
                "saved": False,
                "status": "FAILED",
                "extract_status": "EXTRACT_FAILED",
                "error_message": "source_not_found",
            }

        if not state.get("is_valid"):
            message = "; ".join(state.get("validation_errors", []))[:2000]
            await self.repo.update_source_extract_status(
                source.id,
                extract_status="EXTRACT_FAILED",
                error_message=message or "validation_failed",
            )
            return {
                "saved": False,
                "status": "VALIDATION_FAILED",
                "extract_status": "EXTRACT_FAILED",
                "error_message": message or "validation_failed",
            }

        result = ExtractionExperience.model_validate(state["extraction_result"])
        output_json = result.model_dump()
        if not result.is_interview_experience:
            await self.repo.update_source_extract_status(
                source.id,
                extract_status="NOT_EXPERIENCE",
                error_message=None,
            )
            if source.task_id:
                await self.repo.refresh_task_extraction_counts(source.task_id)
            return {
                "saved": True,
                "status": "SUCCEEDED",
                "extract_status": "NOT_EXPERIENCE",
                "is_interview_experience": False,
                "experience_id": None,
                "question_count": 0,
                "review_status": None,
            }

        if force:
            await self.repo.delete_extraction_drafts_for_source(source.id)

        experience = await self.repo.create_experience_draft(
            source=source,
            company=result.company,
            position=result.position,
            round_name=result.round_name,
            summary=result.experience_summary,
            extraction_confidence=result.extraction_confidence,
            extraction_output_json=output_json,
        )
        questions = await self.repo.create_question_drafts(
            experience=experience,
            questions=[question.model_dump() for question in result.questions],
        )
        await self.repo.update_source_extract_status(
            source.id,
            extract_status="EXTRACTED",
            error_message=None,
        )
        if source.task_id:
            await self.repo.refresh_task_extraction_counts(source.task_id)

        return {
            "saved": True,
            "status": "WAITING_REVIEW",
            "extract_status": "EXTRACTED",
            "is_interview_experience": True,
            "experience_id": experience.id,
            "question_count": len(questions),
            "review_status": experience.review_status,
        }
