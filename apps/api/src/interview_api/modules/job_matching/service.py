"""Frozen runs, short claims/commits and atomic publication; never changes M2–M5."""
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import build_job_matching_router
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.job_matching.models import JobMatchRun, JobMatchCandidate, JobMatchRecommendation, JobMatchAttempt
from interview_api.modules.job_matching.retrieval import profile_snapshot, retrieve
from interview_api.modules.job_matching.preferences import approved_preferences
from interview_api.modules.job_matching.policy import POLICY_VERSION, final_rank_key
from interview_api.modules.job_matching.prompts import MATCH_PROMPT
from interview_api.modules.job_matching.agent import JobMatchingAgent


class JobMatchingService:
    def __init__(self, factory, router=None):
        self.factory, self.router = factory, router

    async def owned(self, db, user_id, run_id, lock=False):
        query = select(JobMatchRun).where(JobMatchRun.id == run_id, JobMatchRun.user_id == user_id)
        run = await db.scalar(query.with_for_update() if lock else query)
        if not run: raise AppError("MATCH_NOT_FOUND", "匹配记录不存在", 404)
        return run

    async def create(self, user_id, request):
        try:
            async with self.factory() as db:
                # One consistent MVCC input read. No model calls or long row locks here.
                await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                old = await db.scalar(select(JobMatchRun).where(JobMatchRun.user_id == user_id, JobMatchRun.request_key == request.request_key))
                if old:
                    if old.input_snapshot_json["request"] != request.model_dump():
                        raise AppError("MATCH_REQUEST_CONFLICT", "请求标识已用于不同筛选条件", 409)
                    return await self.serialize(db, old)
                profile = await profile_snapshot(db, user_id, request.candidate_profile_version_id)
                preferences = await approved_preferences(db, user_id)
                selected, excluded, coverage = await retrieve(db, profile, request.filters.model_dump())
                snapshot = dict(request=request.model_dump(), profile=profile, approved_preferences=preferences,
                    candidates=selected, excluded_candidates=excluded, policy_version=POLICY_VERSION,
                    prompt_version=MATCH_PROMPT.version, prompt_hash=MATCH_PROMPT.content_hash,
                    ranking_policy="eligibility tier -> evidence tier -> agent rank -> pre rank -> job id",
                    data_mode="DEMO_REPLAY" if profile["producer"] == "REPLAY_FIXTURE" or any(c["demo"] for c in selected + excluded) else "PERSISTED_PUBLIC_DATA")
                run = JobMatchRun(user_id=user_id, candidate_profile_version_id=profile["id"], request_key=request.request_key,
                    policy_version=POLICY_VERSION, prompt_version=MATCH_PROMPT.version, input_snapshot_json=snapshot,
                    input_fingerprint=digest(snapshot), coverage_json=coverage, status="READY", producer="NOT_GENERATED")
                db.add(run)
                await db.flush()
                for item in selected + excluded:
                    db.add(JobMatchCandidate(run_id=run.id, company_id=item["company_id"], job_id=item["job_id"],
                        job_description_version_id=item["jd_id"], input_json=item, eligibility_status=item["eligibility_status"],
                        eligibility_reasons=item["eligibility_reasons"], unknown_constraints=item["unknown_constraints"],
                        evidence_matrix_json=item["evidence_matrix"], ranking_components_json=item["ranking_components"], pre_rank=item.get("pre_rank")))
                await db.commit()
                return await self.serialize(db, run)
        except IntegrityError:
            # Concurrent identical create: unique request key is the arbiter.
            async with self.factory() as db:
                old = await db.scalar(select(JobMatchRun).where(JobMatchRun.user_id == user_id, JobMatchRun.request_key == request.request_key))
                if not old or old.input_snapshot_json["request"] != request.model_dump(): raise
                return await self.serialize(db, old)

    async def generate(self, user_id, run_id):
        async with self.factory() as db:
            run = await self.owned(db, user_id, run_id, True)
            if run.status == "COMPLETED": return await self.serialize(db, run)
            if run.status == "RUNNING":
                previous = await db.scalar(select(JobMatchAttempt).where(JobMatchAttempt.run_id == run.id,
                    JobMatchAttempt.attempt_number == run.attempt_version))
                if previous.created_at + timedelta(seconds=settings.job_matching_timeout_seconds * 2 + 60) > datetime.now(timezone.utc):
                    raise AppError("MATCH_RUNNING", "本次匹配正在生成，请稍后读取", 409)
                # Expired worker lease: fence its eventual publication using attempt_version.
                previous.status, previous.error, previous.completed_at = "FAILED", "worker_lease_expired", datetime.now(timezone.utc)
            run.attempt_version += 1
            token = run.attempt_version
            attempt = JobMatchAttempt(run_id=run.id, attempt_number=token, status="RUNNING")
            db.add(attempt)
            run.status, run.error = "RUNNING", None
            payload = dict(run.input_snapshot_json, input_fingerprint=run.input_fingerprint)
            payload.pop("excluded_candidates", None)  # excluded jobs are audit/UI only, never model comparison input
            await db.commit()
            attempt_id = attempt.id
        try:
            if not payload["candidates"]:
                raise AppError("MATCH_COVERAGE_INSUFFICIENT", "当前岗位库覆盖不足；未调用模型或 Research", 409)
            if payload["prompt_version"] != MATCH_PROMPT.version or payload["prompt_hash"] != MATCH_PROMPT.content_hash:
                raise AppError("MATCH_PROMPT_VERSION_UNAVAILABLE", "历史 Prompt 版本不可用，不以新 Prompt 替换固定输入", 409)
            router = self.router or build_job_matching_router()
            result = await JobMatchingAgent(self.factory, router).call(attempt_id, payload)
            async with self.factory() as db:
                run = await self.owned(db, user_id, run_id, True)
                if run.status != "RUNNING" or run.attempt_version != token:
                    raise AppError("MATCH_ATTEMPT_CONFLICT", "匹配尝试已变化，不覆盖新结果", 409)
                candidates = list((await db.scalars(select(JobMatchCandidate).where(JobMatchCandidate.run_id == run_id,
                    JobMatchCandidate.eligibility_status != "NOT_SATISFIED"))).all())
                agent_order = {int(ref.split(":")[1]): rank for rank, ref in enumerate(result["artifact"].ordered_candidate_job_ids, 1)}
                ranking = [{**c.input_json, "agent_rank": agent_order[c.job_id], "pre_rank": c.pre_rank} for c in candidates]
                final = {c["job_id"]: rank for rank, c in enumerate(sorted(ranking, key=final_rank_key), 1)}
                for candidate in candidates:
                    candidate.agent_rank, candidate.final_rank = agent_order[candidate.job_id], final[candidate.job_id]
                    analysis = result["resolved"][candidate.job_id]
                    db.add(JobMatchRecommendation(candidate_id=candidate.id, fit_band=analysis["fit_band"], analysis_json=analysis))
                await db.flush()  # complete candidate/recommendation transaction before sealing Run
                attempt = await db.get(JobMatchAttempt, attempt_id)
                attempt.status, attempt.completed_at = "SUCCEEDED", datetime.now(timezone.utc)
                run.status, run.completed_at = "COMPLETED", attempt.completed_at
                run.provider, run.model = result["provider"], result["model"]
                run.producer = "REPLAY_FIXTURE" if run.provider == "replay" else "MODEL"
                await db.commit()
                return await self.serialize(db, run)
        except Exception as exc:
            async with self.factory() as db:
                run = await self.owned(db, user_id, run_id, True)
                attempt = await db.get(JobMatchAttempt, attempt_id)
                if run.status == "RUNNING" and run.attempt_version == token:
                    run.status, run.error = "FAILED", getattr(exc, "code", type(exc).__name__)
                    attempt.status, attempt.error, attempt.completed_at = "FAILED", run.error, datetime.now(timezone.utc)
                    await db.commit()
                    return await self.serialize(db, run)
                raise AppError("MATCH_ATTEMPT_CONFLICT", "旧尝试未覆盖当前结果", 409) from exc

    async def serialize(self, db, run):
        rows = list((await db.scalars(select(JobMatchCandidate).where(JobMatchCandidate.run_id == run.id)
            .order_by(JobMatchCandidate.final_rank.asc().nulls_last(), JobMatchCandidate.pre_rank.asc().nulls_last(), JobMatchCandidate.id))).all())
        recommendations = {r.candidate_id: r for r in (await db.scalars(select(JobMatchRecommendation).where(JobMatchRecommendation.candidate_id.in_([r.id for r in rows])))).all()}
        def view(c):
            r = recommendations.get(c.id)
            return {**c.input_json, "id": c.id, "pre_rank": c.pre_rank, "agent_rank": c.agent_rank, "final_rank": c.final_rank,
                "recommendation": dict(id=r.id, fit_band=r.fit_band, analysis=r.analysis_json) if r else None}
        attempts = list((await db.scalars(select(JobMatchAttempt).where(JobMatchAttempt.run_id == run.id).order_by(JobMatchAttempt.id))).all())
        return dict(id=run.id, status=run.status, error=run.error, producer=run.producer, provider=run.provider, model=run.model,
            profile_version_id=run.candidate_profile_version_id, policy_version=run.policy_version, prompt_version=run.prompt_version,
            input_fingerprint=run.input_fingerprint, snapshot=run.input_snapshot_json, coverage=run.coverage_json,
            created_at=run.created_at.isoformat(), completed_at=run.completed_at.isoformat() if run.completed_at else None,
            candidates=[view(c) for c in rows if c.eligibility_status != "NOT_SATISFIED"],
            excluded_candidates=[view(c) for c in rows if c.eligibility_status == "NOT_SATISFIED"],
            attempts=[dict(id=a.id, status=a.status, error=a.error, agent_run_id=a.agent_run_id, attempt_number=a.attempt_number) for a in attempts])

    async def detail(self, user_id, run_id):
        async with self.factory() as db: return await self.serialize(db, await self.owned(db, user_id, run_id))

    async def history(self, user_id, limit=20, offset=0):
        async with self.factory() as db:
            rows = (await db.scalars(select(JobMatchRun).where(JobMatchRun.user_id == user_id).order_by(JobMatchRun.id.desc()).offset(offset).limit(limit))).all()
            return [dict(id=r.id, status=r.status, producer=r.producer, profile_version_id=r.candidate_profile_version_id,
                created_at=r.created_at.isoformat(), coverage=r.coverage_json) for r in rows]
