"""Generalized existing run/step audit; no hidden model reasoning is stored."""

from datetime import datetime, timezone
import time

from sqlalchemy.ext.asyncio import async_sessionmaker

from interview_api.core.config import settings
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.infrastructure.models.structured import generate_structured
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.public_career.research.prompts import PromptRegistry


class ResearchModelCalls:
    def __init__(self, factory: async_sessionmaker, router: ModelRouter, run_id: int, *, prompts=None, timeout_seconds=None):
        self.factory = factory
        self.router = router
        self.run_id = run_id
        self.prompts = prompts if prompts is not None else PromptRegistry()
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.public_research_model_timeout_seconds

    async def call(self, prompt_name: str, route: str, payload: dict, schema):
        prompt = self.prompts.get(prompt_name)
        provider = self.router.primary(route)
        async with self.factory() as db:
            step = ExperienceAgentStepRun(
                agent_run_id=self.run_id, step_name=prompt_name, status="RUNNING",
                provider_name=provider.provider_name, model_name=provider.model_name,
                prompt_name=prompt.name, prompt_version=prompt.version,
                structured_retry=0,
                input_json={"route": route, "prompt_hash": prompt.content_hash,
                            "thinking_type": getattr(provider, "thinking_type", None)},
            )
            db.add(step)
            await db.commit()
            step_id = step.id
        started = time.perf_counter()
        from interview_api.infrastructure.models.governance import model_scope, run_owner
        scope_token = None
        try:
            if getattr(provider, "governed", False):
                owner, uid = await run_owner(self.factory, self.run_id)
                scope_token = model_scope.set((self.factory, owner, uid, route))
            result = await generate_structured(
                self.router, route, system=prompt.system, payload=payload,
                response_model=schema,
                timeout_seconds=self.timeout_seconds,
            )
            async with self.factory() as db:
                step = await db.get(ExperienceAgentStepRun, step_id)
                actual = self.router.resolve(route)
                step.provider_name, step.model_name = actual.provider_name, actual.model_name
                step.status = "SUCCEEDED"
                step.latency_ms = result.latency_ms
                step.token_usage_json = result.usage
                step.structured_retry = result.structured_retry
                # Bounded public summaries keep the existing review UI useful;
                # never store provider reasoning or raw model responses.
                parsed = result.parsed.model_dump()
                summary = {key: parsed[key] for key in (
                    "is_interview_experience", "extraction_confidence", "routing_confidence",
                    "is_reliable", "reliability_score", "spam_risk_score", "risk_flags", "suitable",
                ) if key in parsed}
                if "questions" in parsed:
                    summary["question_count"] = len(parsed["questions"])
                    if schema.__name__ == 'FragmentInterviewExtraction':
                        # Bounded public artifact only, enough to diagnose a
                        # bad selection without storing raw provider responses.
                        summary['question_selections'] = [dict(
                            fragment_id=q['fragment_id'], kind=q['kind'],
                            normalized_question=q.get('normalized_question'))
                            for q in parsed['questions'][:20]]
                        summary['identity_selection'] = {key: parsed[key] for key in (
                            'company', 'position', 'round_name', 'identity_evidence_ids')}
                step.output_json = {"schema": schema.__name__, "model_invocations": result.invocations, **summary}
                await db.commit()
            return result.parsed
        except Exception as exc:
            async with self.factory() as db:
                step = await db.get(ExperienceAgentStepRun, step_id)
                actual = self.router.resolve(route)
                step.provider_name, step.model_name = actual.provider_name, actual.model_name
                step.status = "FAILED"
                step.latency_ms = round((time.perf_counter() - started) * 1000)
                step.token_usage_json = getattr(exc, "usage", {})
                step.structured_retry = int(getattr(exc, "retried", False))
                step.error_message = getattr(exc, "code", type(exc).__name__)
                step.output_json = {"schema": schema.__name__, "model_invocations": getattr(exc, "invocations", [])}
                await db.commit()
            raise
        finally:
            if scope_token is not None:
                model_scope.reset(scope_token)


async def create_research_run(factory: async_sessionmaker, refresh_task_id: int) -> int:
    async with factory() as db:
        run = ExperienceAgentRun(
            refresh_task_id=refresh_task_id, agent_role="PublicDataResearchAgent",
            graph_name="public_data_research", graph_version="1.0.0",
            status="RUNNING", started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        await db.commit()
        return run.id


async def finish_research_run(factory, run_id, *, status, output=None, error=None):
    async with factory() as db:
        run = await db.get(ExperienceAgentRun, run_id)
        run.status = status
        run.output_json = output
        run.error_message = error
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
