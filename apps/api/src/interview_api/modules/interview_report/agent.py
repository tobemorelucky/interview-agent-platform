"""One Coach role; reuses shared provider/structured repair/concurrency/run audit."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from sqlalchemy import select

from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import INTERVIEW_COACH_ROUTE
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.public_career.research.audit import ResearchModelCalls, finish_research_run
from interview_api.modules.interview_report.models import InterviewReportAttempt
from interview_api.modules.interview_report.prompts import CoachPromptRegistry, COACH_PROMPT
from interview_api.modules.interview_report.schemas import CoachArtifact
from interview_api.modules.interview_report.resolver import resolve_coach_artifact


@dataclass(frozen=True)
class CoachResult:
    artifact: CoachArtifact
    resolved: dict
    run_id: int
    provider: str
    model: str
    retry: int
    usage: dict


class SummaryCoachAgent:
    def __init__(self, factory, router):
        self.factory, self.router = factory, router

    async def call(self, report_id, attempt_id, payload):
        async with self.factory() as db:
            run = ExperienceAgentRun(agent_role="SummaryCoachAgent", graph_name="interview_coach",
                graph_version="1.0.0", input_hash=payload["evaluation_fingerprint"], status="RUNNING",
                started_at=datetime.now(timezone.utc))
            db.add(run)
            await db.flush()
            run_id = run.id
            attempt = await db.get(InterviewReportAttempt, attempt_id)
            if not attempt or attempt.report_id != report_id:
                raise AppError("REPORT_ATTEMPT_CONFLICT", "报告生成尝试已变化", 409)
            attempt.agent_run_id = run_id
            await db.commit()
        try:
            envelope = dict(input=payload, schema=CoachArtifact.model_json_schema(), system=COACH_PROMPT.system)
            if len(json.dumps(envelope, ensure_ascii=False).encode("utf-8")) > settings.interview_coach_max_input_bytes:
                raise AppError("COACH_INPUT_BUDGET", "面试材料超过 Coach 输入预算，未静默删除证据", 422)
            artifact = await ResearchModelCalls(self.factory, self.router, run_id,
                prompts=CoachPromptRegistry(), timeout_seconds=settings.interview_coach_timeout_seconds
            ).call(COACH_PROMPT.name, INTERVIEW_COACH_ROUTE, payload, CoachArtifact)
            resolved = resolve_coach_artifact(artifact, payload["evaluations"])
            await finish_research_run(self.factory, run_id, status="SUCCEEDED", output={"schema": "CoachArtifact"})
            async with self.factory() as db:
                step = await db.scalar(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == run_id))
                return CoachResult(artifact, resolved, run_id, step.provider_name, step.model_name,
                                   step.structured_retry, step.token_usage_json or {})
        except Exception as exc:
            await finish_research_run(self.factory, run_id, status="FAILED", error=getattr(exc, "code", type(exc).__name__))
            raise
