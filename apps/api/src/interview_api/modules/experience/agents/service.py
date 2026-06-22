"""Service entrypoint for experience agent graph runs."""

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
from interview_api.modules.experience.agents.quality_gate import evaluate_quality_gate
from interview_api.modules.experience.agents.reliability_agent import ReliabilityAgent
from interview_api.modules.experience.agents.repository import ExperienceAgentRepository
from interview_api.modules.experience.agents.routing_agent import RoutingAgent
from interview_api.modules.experience.agents.schemas import (
    ExtractionExperience,
    QualityGateResult,
    ReliabilityResult,
    RoutingResult,
)
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
            raise LookupError("source_not_found")
        if source.fetch_status != "FETCHED":
            raise ValueError("only_FETCHED_source_can_run_extraction")
        raw_text = source.raw_text or ""
        if len(raw_text.strip()) < MIN_RAW_TEXT_CHARS:
            raise ValueError("raw_text_empty_or_too_short")

        if source.extract_status == "EXTRACTED" and not force:
            existing = await self.repo.latest_experience_for_source(source.id)
            if existing:
                return await self._existing_result_payload(source.id, existing)

        llm = self.llm or self._build_default_llm()
        extraction_agent = ExtractionAgent(llm, model_name=settings.llm_model)
        routing_agent = RoutingAgent(llm, model_name=settings.llm_model)
        reliability_agent = ReliabilityAgent(llm, model_name=settings.llm_model)
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
            "routing_result": None,
            "reliability_result": None,
            "quality_gate_result": None,
            "final_review_status": None,
            "should_create_review_item": False,
            "should_reject": False,
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
                result = await extraction_agent.run(state)
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

        async def extraction_validation_node(state: ExperienceAgentState) -> dict:
            started = time.perf_counter()
            if not state.get("extraction_result"):
                await self._record_step(
                    run.id,
                    "extraction_validation",
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
                "extraction_validation",
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

        async def routing_node(state: ExperienceAgentState) -> dict:
            started = time.perf_counter()
            if not state.get("is_valid") or not state.get("extraction_result"):
                await self._record_step(
                    run.id,
                    "routing",
                    "SKIPPED",
                    started,
                    input_json={"is_valid": state.get("is_valid")},
                    output_json={"reason": "invalid_extraction"},
                )
                return {}
            extraction = ExtractionExperience.model_validate(state["extraction_result"])
            if not extraction.is_interview_experience or not extraction.questions:
                await self._record_step(
                    run.id,
                    "routing",
                    "SKIPPED",
                    started,
                    input_json={
                        "is_interview_experience": extraction.is_interview_experience,
                        "question_count": len(extraction.questions),
                    },
                    output_json={"reason": "no_interview_questions"},
                )
                return {}
            try:
                result = await routing_agent.run(state)
            except (ValueError, ValidationError) as e:
                error = str(e)[:2000]
                await self._record_step(
                    run.id,
                    "routing",
                    "VALIDATION_FAILED",
                    started,
                    input_json={"question_count": len(extraction.questions)},
                    error_message=error,
                    model_name=settings.llm_model,
                )
                return {
                    "validation_errors": state.get("validation_errors", [])
                    + [f"routing_parse_failed: {error}"],
                    "is_valid": False,
                    "status": "VALIDATION_FAILED",
                }
            payload = result.model_dump()
            await self._record_step(
                run.id,
                "routing",
                "SUCCEEDED",
                started,
                input_json={"question_count": len(extraction.questions)},
                output_json={
                    "routing_confidence": result.routing_confidence,
                    "indexable_question_count": sum(
                        1 for item in result.question_results if item.should_index
                    ),
                },
                model_name=settings.llm_model,
            )
            return {"routing_result": payload}

        async def reliability_node(state: ExperienceAgentState) -> dict:
            started = time.perf_counter()
            if not state.get("is_valid") or not state.get("extraction_result"):
                await self._record_step(
                    run.id,
                    "reliability",
                    "SKIPPED",
                    started,
                    input_json={"is_valid": state.get("is_valid")},
                    output_json={"reason": "invalid_extraction"},
                )
                return {}
            try:
                result = await reliability_agent.run(state)
            except (ValueError, ValidationError) as e:
                error = str(e)[:2000]
                await self._record_step(
                    run.id,
                    "reliability",
                    "VALIDATION_FAILED",
                    started,
                    input_json={"has_extraction_result": True},
                    error_message=error,
                    model_name=settings.llm_model,
                )
                return {
                    "validation_errors": state.get("validation_errors", [])
                    + [f"reliability_parse_failed: {error}"],
                    "is_valid": False,
                    "status": "VALIDATION_FAILED",
                }
            payload = result.model_dump()
            await self._record_step(
                run.id,
                "reliability",
                "SUCCEEDED",
                started,
                input_json={"has_extraction_result": True},
                output_json={
                    "is_reliable": result.is_reliable,
                    "reliability_score": result.reliability_score,
                    "spam_risk_score": result.spam_risk_score,
                    "risk_flags": result.risk_flags,
                },
                model_name=settings.llm_model,
            )
            return {"reliability_result": payload}

        async def quality_gate_node(state: ExperienceAgentState) -> dict:
            started = time.perf_counter()
            extraction = (
                ExtractionExperience.model_validate(state["extraction_result"])
                if state.get("extraction_result")
                else None
            )
            routing = (
                RoutingResult.model_validate(state["routing_result"])
                if state.get("routing_result")
                else None
            )
            reliability = (
                ReliabilityResult.model_validate(state["reliability_result"])
                if state.get("reliability_result")
                else None
            )
            result = evaluate_quality_gate(
                extraction=extraction,
                routing=routing,
                reliability=reliability,
                raw_text=state["raw_text"],
            )
            payload = result.model_dump()
            await self._record_step(
                run.id,
                "quality_gate",
                "SUCCEEDED",
                started,
                input_json={
                    "is_valid": state.get("is_valid"),
                    "has_routing": routing is not None,
                    "has_reliability": reliability is not None,
                },
                output_json=payload,
            )
            return {
                "quality_gate_result": payload,
                "final_review_status": result.review_status,
                "should_create_review_item": bool(
                    extraction and extraction.is_interview_experience
                ),
                "should_reject": result.review_status == "REJECTED",
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
                    "final_review_status": state.get("final_review_status"),
                },
                output_json=saved,
            )
            return {"saved_result": saved, "status": saved.get("status", state.get("status", ""))}

        graph = build_experience_extraction_graph(
            extraction_node=extraction_node,
            extraction_validation_node=extraction_validation_node,
            routing_node=routing_node,
            reliability_node=reliability_node,
            quality_gate_node=quality_gate_node,
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
            logger.exception("[experience-agent] graph failed source=%s run=%s", source.id, run.id)
            raise

        saved_result = final_state.get("saved_result") or {}
        validation_errors = final_state.get("validation_errors") or []
        run_status = "SUCCEEDED"
        if final_state.get("status") == "VALIDATION_FAILED" or validation_errors:
            run_status = "VALIDATION_FAILED"
        await self.repo.update_run(
            run.id,
            status=run_status,
            output_json=self._graph_output(final_state),
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
            "indexable_question_count": saved_result.get("indexable_question_count", 0),
            "reliability_score": saved_result.get("reliability_score"),
            "review_status": saved_result.get("review_status"),
            "risk_flags": saved_result.get("risk_flags") or [],
            "quality_gate_reasons": saved_result.get("quality_gate_reasons") or [],
            "status": saved_result.get("review_status") or saved_result.get("status"),
            "extract_status": saved_result.get("extract_status"),
            "skipped": False,
            "error_message": "; ".join(validation_errors)[:2000]
            if validation_errors
            else saved_result.get("error_message"),
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
                **self._quality_payload(state),
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
                "status": "REJECTED",
                "extract_status": "NOT_EXPERIENCE",
                "is_interview_experience": False,
                "experience_id": None,
                "question_count": 0,
                **self._quality_payload(state),
            }

        if force:
            await self.repo.delete_extraction_drafts_for_source(source.id)

        routing_json = state.get("routing_result")
        reliability_json = state.get("reliability_result")
        quality_gate_json = state.get("quality_gate_result")
        review_status = state.get("final_review_status") or "NEEDS_MANUAL_CHECK"
        reliability_score = (
            reliability_json.get("reliability_score") if reliability_json else None
        )
        quality_flags = reliability_json.get("quality_flags") if reliability_json else []
        company = (routing_json or {}).get("company") or result.company
        position = (routing_json or {}).get("position") or result.position
        job_direction = (routing_json or {}).get("overall_job_direction")

        experience = await self.repo.create_experience_draft(
            source=source,
            company=company,
            position=position,
            job_direction=job_direction,
            round_name=result.round_name,
            summary=result.experience_summary,
            extraction_confidence=result.extraction_confidence,
            extraction_output_json=output_json,
            routing_json=routing_json,
            reliability_json=reliability_json,
            quality_gate_json=quality_gate_json,
            reliability_score=reliability_score,
            quality_flags_json=quality_flags,
            review_status=review_status,
        )
        questions = await self.repo.create_question_drafts(
            experience=experience,
            questions=[question.model_dump() for question in result.questions],
            routing_result=routing_json,
            review_status=review_status,
            reliability_score=reliability_score,
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
            "status": review_status,
            "extract_status": "EXTRACTED",
            "is_interview_experience": True,
            "experience_id": experience.id,
            "question_count": len(questions),
            "review_status": experience.review_status,
            **self._quality_payload(state),
        }

    async def _existing_result_payload(self, source_id: int, existing) -> dict:
        question_count = await self.repo.count_questions_for_experience(existing.id)
        indexable_question_count = await self.repo.count_indexable_questions_for_experience(
            existing.id
        )
        reliability_json = existing.reliability_json or {}
        quality_gate_json = existing.quality_gate_json or {}
        return {
            "source_id": source_id,
            "agent_run_id": None,
            "is_interview_experience": True,
            "experience_id": existing.id,
            "question_count": question_count,
            "indexable_question_count": indexable_question_count,
            "reliability_score": existing.reliability_score,
            "review_status": existing.review_status,
            "risk_flags": reliability_json.get("risk_flags") or [],
            "quality_gate_reasons": quality_gate_json.get("reasons") or [],
            "status": existing.review_status,
            "extract_status": "EXTRACTED",
            "skipped": True,
            "error_message": None,
        }

    def _quality_payload(self, state: ExperienceAgentState) -> dict[str, Any]:
        quality_gate = state.get("quality_gate_result") or {}
        reliability = state.get("reliability_result") or {}
        return {
            "indexable_question_count": quality_gate.get("indexable_question_count", 0),
            "reliability_score": quality_gate.get(
                "reliability_score", reliability.get("reliability_score")
            ),
            "review_status": quality_gate.get("review_status"),
            "risk_flags": reliability.get("risk_flags") or [],
            "quality_gate_reasons": quality_gate.get("reasons") or [],
        }

    def _graph_output(self, state: ExperienceAgentState) -> dict[str, Any]:
        return {
            "extraction_result": state.get("extraction_result"),
            "routing_result": state.get("routing_result"),
            "reliability_result": state.get("reliability_result"),
            "quality_gate_result": state.get("quality_gate_result"),
            "saved_result": state.get("saved_result"),
        }
