"""The only M6 role; shared model infrastructure, no state/domain side effects."""
from datetime import datetime, timezone
import json
from sqlalchemy import select
from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import JOB_MATCHING_ROUTE
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.public_career.research.audit import ResearchModelCalls, finish_research_run
from interview_api.modules.job_matching.models import JobMatchAttempt
from interview_api.modules.job_matching.prompts import MATCH_PROMPT, MatchPromptRegistry
from interview_api.modules.job_matching.schemas import MatchArtifact
from interview_api.modules.job_matching.resolver import resolve_artifact


class JobMatchingAgent:
    def __init__(self, factory, router):
        self.factory, self.router = factory, router

    async def call(self, attempt_id, payload):
        async with self.factory() as db:
            run = ExperienceAgentRun(agent_role="JobMatchingAgent", graph_name="job_matching", graph_version="1.0.0",
                input_hash=payload["input_fingerprint"], status="RUNNING", started_at=datetime.now(timezone.utc))
            db.add(run)
            await db.flush()
            attempt = await db.get(JobMatchAttempt, attempt_id)
            attempt.agent_run_id = run.id
            run_id = run.id
            await db.commit()
        try:
            envelope = dict(input=payload, schema=MatchArtifact.model_json_schema(), system=MATCH_PROMPT.system)
            if len(json.dumps(envelope, ensure_ascii=False).encode()) > settings.job_matching_max_input_bytes:
                raise AppError("MATCH_INPUT_BUDGET", "匹配材料超过输入预算，未静默删除证据", 422)
            artifact = await ResearchModelCalls(self.factory, self.router, run_id, prompts=MatchPromptRegistry(),
                timeout_seconds=settings.job_matching_timeout_seconds).call(MATCH_PROMPT.name, JOB_MATCHING_ROUTE, payload, MatchArtifact)
            resolved = resolve_artifact(artifact, payload["candidates"], payload["profile"])
            await finish_research_run(self.factory, run_id, status="SUCCEEDED", output={"schema": "MatchArtifact"})
            async with self.factory() as db:
                step = await db.scalar(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == run_id))
                return dict(artifact=artifact, resolved=resolved, run_id=run_id,
                    provider=step.provider_name, model=step.model_name)
        except Exception as exc:
            await finish_research_run(self.factory, run_id, status="FAILED", error=getattr(exc, "code", type(exc).__name__))
            raise
