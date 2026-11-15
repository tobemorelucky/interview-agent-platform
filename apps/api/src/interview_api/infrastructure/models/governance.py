"""Short-transaction user quotas; repairs/fallback each reserve separately.

Unknown outcomes retain estimates. Expired/late settlements cannot refund them.
This is deliberately not a promise about provider invoices or free credits.
"""
import asyncio
from contextvars import ContextVar
from datetime import timedelta
import json
from uuid import uuid4
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from interview_api.core.config import settings
from interview_api.core.errors import AppError
from .governance_models import ModelAdmission, ModelBudgetOwner

request_user_id: ContextVar[int | None] = ContextVar("model_request_user", default=None)
provider_guard: ContextVar[object | None] = ContextVar("model_provider_guard", default=None)


def check_generation_result(result):
    """Keep durable FAILED audit, but return a proper HTTP admission error."""
    code = result.get("error")
    if code in {"MODEL_USER_QUOTA", "MODEL_USER_CONCURRENCY"}:
        raise AppError(code, "模型额度不足或已有任务正在运行，请稍后重试", 429)
    if code == "MODEL_IDENTITY_REQUIRED":
        raise AppError(code, "模型任务缺少可信归属关系", 403)
    return result


class ModelGovernance:
    def __init__(self, factory, *, calls=None, tokens=None, concurrency=None):
        self.factory = factory
        self.calls = calls if calls is not None else settings.model_user_daily_calls
        self.tokens = tokens if tokens is not None else settings.model_user_daily_tokens
        self.concurrency = concurrency if concurrency is not None else settings.model_user_concurrency

    async def reserve(self, owner_key, *, user_id=None, route, model, tokens, timeout, call_key=None):
        key = call_key or uuid4().hex
        async with self.factory() as db, db.begin():
            await db.execute(insert(ModelBudgetOwner).values(owner_key=owner_key, user_id=user_id)
                             .on_conflict_do_nothing(index_elements=["owner_key"]))
            await db.scalar(select(ModelBudgetOwner).where(ModelBudgetOwner.owner_key == owner_key).with_for_update())
            now = await db.scalar(select(func.clock_timestamp()))
            await db.execute(update(ModelAdmission).where(ModelAdmission.owner_key == owner_key,
                ModelAdmission.status == "RESERVED", ModelAdmission.expires_at <= now)
                .values(status="UNKNOWN", finished_at=now))
            if await db.get(ModelAdmission, key):
                raise AppError("MODEL_CALL_ALREADY_RESERVED", "该模型调用已受理，不能重复发送", 409)
            active = await db.scalar(select(func.count()).select_from(ModelAdmission).where(
                ModelAdmission.owner_key == owner_key, ModelAdmission.status == "RESERVED"))
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            count, charged = (await db.execute(select(func.count(), func.coalesce(func.sum(ModelAdmission.charged_tokens), 0))
                .where(ModelAdmission.owner_key == owner_key, ModelAdmission.created_at >= day))).one()
            if active >= self.concurrency:
                raise AppError("MODEL_USER_CONCURRENCY", "已有模型任务运行中，请稍后重试", 429)
            if count >= self.calls or charged + tokens > self.tokens:
                raise AppError("MODEL_USER_QUOTA", "今日模型使用额度不足，请稍后再试", 429)
            db.add(ModelAdmission(call_key=key, owner_key=owner_key, route=route, model=model,
                status="RESERVED", reserved_tokens=tokens, charged_tokens=tokens,
                expires_at=now + timedelta(seconds=timeout + 30)))
        return key

    async def settle(self, key, usage=None):
        async with self.factory() as db, db.begin():
            owner = await db.scalar(select(ModelAdmission.owner_key).where(ModelAdmission.call_key == key))
            if owner is None:
                return
            await db.scalar(select(ModelBudgetOwner).where(ModelBudgetOwner.owner_key == owner).with_for_update())
            row = await db.get(ModelAdmission, key)
            now = await db.scalar(select(func.clock_timestamp()))
            if row.status != "RESERVED":
                return
            total = (usage or {}).get("total_tokens")
            if row.expires_at <= now or type(total) is not int or total < 0:
                row.status = "UNKNOWN"
            else:
                row.status, row.actual_tokens, row.charged_tokens = "SETTLED", total, total
            row.finished_at = now


async def governed_call(provider, messages, operation):
    """Admit, send and settle exactly one request under the caller's own scope.

    There is deliberately no fallback: inferring the owner from an ambient request
    or the process-wide session factory is what booked calls as ``legacy_llm`` and
    what let a sandbox harness bill the production ledger. A missing or mismatched
    scope raises before any bytes are sent, so it costs nothing.
    """
    from .scope import assert_ledger_target, require_scope
    scope = require_scope()
    assert_ledger_target(scope)
    factory, owner, uid, route = scope.factory, scope.owner, scope.user_id, scope.route
    reserve = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + provider.max_output_tokens
    governance = ModelGovernance(factory)
    try:
        key = await governance.reserve(owner, user_id=uid, route=route, model=provider.model_name,
                                       tokens=reserve, timeout=provider.timeout)
    except Exception as exc:
        exc.request_sent = False
        raise
    sent = False
    async def execute():
        nonlocal sent
        guard = provider_guard.get()
        if guard is None:
            sent = True
            return await operation()
        async with guard:
            sent = True
            return await operation()
    try:
        result = await asyncio.wait_for(execute(), provider.timeout)
    except BaseException as exc:
        # A provider that positively knows nothing was written outranks the
        # coarse "we entered the guarded section" flag. Only a classified
        # pre-send failure may release the hold; everything else stays UNKNOWN.
        claimed = getattr(exc, "request_sent", None)
        sent = sent if claimed is None else claimed
        exc.request_sent = sent
        usage = getattr(exc, "usage", None)
        if sent is False:
            usage = {"total_tokens": 0}
        elif not (isinstance(usage, dict) and type(usage.get("total_tokens")) is int and usage["total_tokens"] >= 0):
            usage = None
        await asyncio.shield(governance.settle(key, usage))
        raise
    await asyncio.shield(governance.settle(key, result.usage))
    return result


async def run_owner(factory, run_id):
    """Resolve workers' owners from domain FK links, never client-supplied IDs."""
    from interview_api.modules.candidate_profile.models import ProfileGeneration
    from interview_api.modules.resume.models import Resume
    from interview_api.modules.interview.models import InterviewAgentCall, InterviewSession
    from interview_api.modules.interview_report.models import InterviewReportAttempt, FinalInterviewReport
    from interview_api.modules.job_matching.models import JobMatchAttempt, JobMatchRun
    from interview_api.modules.experience.models import ExperienceAgentRun
    queries = [
        select(Resume.user_id).join(ProfileGeneration, ProfileGeneration.resume_id == Resume.id).where(ProfileGeneration.agent_run_id == run_id),
        select(InterviewSession.user_id).join(InterviewAgentCall, InterviewAgentCall.session_id == InterviewSession.id).where(InterviewAgentCall.agent_run_id == run_id),
        select(FinalInterviewReport.user_id).join(InterviewReportAttempt, InterviewReportAttempt.report_id == FinalInterviewReport.id).where(InterviewReportAttempt.agent_run_id == run_id),
        select(JobMatchRun.user_id).join(JobMatchAttempt, JobMatchAttempt.run_id == JobMatchRun.id).where(JobMatchAttempt.agent_run_id == run_id),
    ]
    async with factory() as db:
        for query in queries:
            uid = await db.scalar(query)
            if uid is not None:
                return f"user:{uid}", uid
        run = await db.get(ExperienceAgentRun, run_id)
        if run and (run.refresh_task_id is not None or run.task_id is not None):
            return "system:public-career", None
    raise AppError("MODEL_IDENTITY_REQUIRED", "模型任务缺少归属关系", 403)
