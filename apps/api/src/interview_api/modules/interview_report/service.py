"""Report generation is independent from completed InterviewSession state."""
from copy import deepcopy
from datetime import datetime, timezone
from functools import cached_property
from sqlalchemy import func, select

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import INTERVIEW_COACH_ROUTE, build_interview_coach_router
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.interview.models import InterviewContextSnapshot, InterviewSession
from interview_api.modules.interview_report.agent import SummaryCoachAgent
from interview_api.modules.interview_report.models import FinalInterviewReport, InterviewReportAttempt, InterviewPerformanceObservation, PerformanceObservationEvaluation
from interview_api.modules.interview_report.prompts import COACH_PROMPT
from interview_api.modules.interview_report.statistics import interview_records, deterministic_statistics, evaluation_fingerprint
from interview_api.modules.memory.proposal_service import create_report_proposals, MemoryProposalService
from interview_api.modules.public_career.research.audit import finish_research_run
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun


def report_conflict(message="报告生成状态已变化，请刷新后重试"):
    return AppError("REPORT_GENERATION_CONFLICT", message, 409)


def _iso(value):
    return value.isoformat() if value else None


class InterviewReportService:
    def __init__(self, factory, router=None):
        self.factory, self._router = factory, router

    @cached_property
    def router(self):
        return self._router or build_interview_coach_router()

    @cached_property
    def agent(self):
        return SummaryCoachAgent(self.factory, self.router)

    async def _session(self, db, session_id, user_id, *, lock=False):
        query = select(InterviewSession).where(InterviewSession.id == session_id, InterviewSession.user_id == user_id)
        session = await db.scalar(query.with_for_update() if lock else query)
        if not session:
            raise AppError("INTERVIEW_NOT_FOUND", "面试会话不存在", 404)
        if session.workflow_mode != "m4" or not session.context_snapshot_id:
            raise AppError("LEGACY_REPORT_UNAVAILABLE", "历史面试没有可靠上下文快照，不猜测生成报告", 409)
        if session.status != "COMPLETED":
            raise AppError("INTERVIEW_NOT_COMPLETED", "面试完成后才能生成报告", 409)
        return session

    async def _current(self, db, session_id):
        return await db.scalar(select(FinalInterviewReport).where(
            FinalInterviewReport.session_id == session_id, FinalInterviewReport.is_current.is_(True)))

    async def generate(self, session_id, user_id, regenerate=False, request_key=None):
        if regenerate and not request_key:
            raise AppError("REPORT_REGENERATION_KEY_REQUIRED", "主动重新生成需要稳定 request_key", 422)
        # This lock only serializes short preparation/number allocation, never the model.
        async with self.factory() as db:
            session = await self._session(db, session_id, user_id, lock=True)
            snapshot = await db.get(InterviewContextSnapshot, session.context_snapshot_id)
            questions, evaluations = await interview_records(db, session_id)
            statistics = deterministic_statistics(questions, evaluations)
            fingerprint = evaluation_fingerprint(evaluations)
            key = digest(["interview_coach", session_id, snapshot.content_fingerprint, fingerprint,
                          COACH_PROMPT.version, COACH_PROMPT.content_hash, request_key if regenerate else None])
            report = await db.scalar(select(FinalInterviewReport).where(FinalInterviewReport.generation_key == key).with_for_update())
            current = await self._current(db, session_id)
            if report and report.generation_status == "COMPLETED":
                result = await self._serialize(db, report)
                result["reused"] = True
                return result
            if not report:
                number = (await db.scalar(select(func.max(FinalInterviewReport.generation_number)).where(
                    FinalInterviewReport.session_id == session_id)) or 0) + 1
                report = FinalInterviewReport(user_id=user_id, session_id=session_id, context_snapshot_id=snapshot.id,
                    generation_number=number, generation_key=key, evaluation_fingerprint=fingerprint,
                    base_current_report_id=current.id if current else None, generation_status="RUNNING", is_current=False,
                    statistics_json=statistics, producer="PENDING", prompt_version=COACH_PROMPT.version, attempt_version=0)
                db.add(report)
                await db.flush()
            elif report.base_current_report_id != (current.id if current else None):
                raise report_conflict("当前报告已被新 generation 替换，请明确重新生成")
            report.generation_status, report.error = "RUNNING", None
            report.attempt_version += 1
            attempt = InterviewReportAttempt(report_id=report.id, attempt_number=report.attempt_version, status="RUNNING")
            db.add(attempt)
            await db.flush()
            report_id, attempt_id, expected = report.id, attempt.id, report.attempt_version
            payload = dict(snapshot=deepcopy(snapshot.context_json), statistics=statistics,
                questions=[dict(id=q.id, question=q.question, standard_answer=q.standard_answer, source=q.source,
                    question_kind=q.question_kind, status=q.status, candidate_refs=q.candidate_refs_json,
                    job_requirement_id=q.job_requirement_id) for q in questions],
                evaluations=evaluations, evaluation_fingerprint=fingerprint)
            if snapshot.context_json.get("adaptive_policy"):
                from interview_api.modules.interview.adaptive import review_projection
                payload["snapshot"].pop("adaptive_policy", None)
                payload["adaptive_review"] = review_projection(snapshot.context_json, session.interview_plan_json)
            await db.commit()
        result, published = None, False
        try:
            if evaluations:
                result = await self.agent.call(report_id, attempt_id, payload)
                analysis = result.resolved
            else:
                analysis = dict(overall_summary="本场没有有效回答评价，当前材料未提供可分析的表现证据。",
                    strengths=[], weaknesses=[], recurring_issues=[], job_alignment=[], recommended_actions=[],
                    practice_plan=[], memory_proposals=[], analysis_status="NO_PERFORMANCE_ANALYSIS",
                    epistemic_status="NO_PERFORMANCE_ANALYSIS")
            async with self.factory() as db:
                session = await self._session(db, session_id, user_id, lock=True)
                report = await db.scalar(select(FinalInterviewReport).where(FinalInterviewReport.id == report_id).with_for_update())
                if report.attempt_version != expected or report.generation_status != "RUNNING":
                    raise report_conflict()
                current = await self._current(db, session_id)
                if (current.id if current else None) != report.base_current_report_id:
                    raise report_conflict("较新的报告已经发布，陈旧结果不会替换它")
                _, latest_evaluations = await interview_records(db, session_id)
                if evaluation_fingerprint(latest_evaluations) != fingerprint:
                    raise report_conflict("评价集合已变化，未发布陈旧报告")
                report.coach_analysis_json = analysis
                report.provider, report.model = (result.provider, result.model) if result else (None, None)
                report.producer = ("REPLAY_FIXTURE" if result.provider == "replay" else "SUMMARY_COACH_AGENT") if result else "deterministic"
                report.generation_status, report.completed_at = "COMPLETED", datetime.now(timezone.utc)
                await db.flush()
                observations = []
                if result:
                    for category in ("strengths", "weaknesses", "recurring_issues"):
                        for finding in analysis[category]:
                            observation = InterviewPerformanceObservation(user_id=user_id, session_id=session_id, report_id=report.id,
                                observation_type={"strengths": "STRENGTH", "weaknesses": "WEAKNESS", "recurring_issues": "RECURRING_ISSUE"}[category], text="本场面试表现观察：" + finding["statement"],
                                supporting_evaluation_ids=finding["supporting_evaluation_ids"], occurrence_count=finding["occurrence_count"])
                            db.add(observation)
                            await db.flush()
                            db.add_all([PerformanceObservationEvaluation(observation_id=observation.id, evaluation_id=id_)
                                        for id_ in finding["supporting_evaluation_ids"]])
                            observations.append(observation)
                    await create_report_proposals(db, user_id, report, analysis["memory_proposals"], observations)
                attempt = await db.get(InterviewReportAttempt, attempt_id)
                attempt.status, attempt.completed_at = "SUCCEEDED", datetime.now(timezone.utc)
                if current:
                    current.is_current = False
                    await db.flush()
                # Last publication step: no current pointer ever references partial content.
                report.is_current = True
                await db.commit()
                published = True
            return await self.report_record(report_id, user_id)
        except Exception as exc:
            if published:
                # A response/read failure cannot rewrite a committed successful generation.
                raise
            code = getattr(exc, "code", type(exc).__name__)
            async with self.factory() as db:
                report = await db.scalar(select(FinalInterviewReport).where(FinalInterviewReport.id == report_id).with_for_update())
                attempt = await db.get(InterviewReportAttempt, attempt_id)
                attempt.status, attempt.error, attempt.completed_at = "FAILED", code[:500], datetime.now(timezone.utc)
                if report.attempt_version == expected and report.generation_status != "COMPLETED":
                    report.generation_status, report.error = "FAILED", code[:500]
                    # Failed Replay is still Replay, not a misleading live/not-run label.
                    try:
                        provider = self.router.resolve(INTERVIEW_COACH_ROUTE)
                        report.provider, report.model = provider.provider_name, provider.model_name
                        report.producer = "REPLAY_FIXTURE" if provider.provider_name == "replay" else "SUMMARY_COACH_AGENT"
                    except Exception:
                        # Provider construction may itself fail due to absent configuration.
                        report.producer = "MODEL_CONFIGURATION_FAILED"
                await db.commit()
            if result:
                await finish_research_run(self.factory, result.run_id, status="FAILED", error=code[:500])
            if isinstance(exc, AppError) and exc.status_code == 409:
                raise
            return await self.report_record(report_id, user_id)

    async def report_record(self, report_id, user_id):
        async with self.factory() as db:
            report = await db.scalar(select(FinalInterviewReport).where(FinalInterviewReport.id == report_id, FinalInterviewReport.user_id == user_id))
            if not report:
                raise AppError("REPORT_NOT_FOUND", "报告不存在", 404)
            return await self._serialize(db, report)

    async def _serialize(self, db, report):
        snapshot = await db.get(InterviewContextSnapshot, report.context_snapshot_id)
        attempts = (await db.scalars(select(InterviewReportAttempt).where(InterviewReportAttempt.report_id == report.id)
            .order_by(InterviewReportAttempt.attempt_number))).all()
        audit = []
        for attempt in attempts:
            run = await db.get(ExperienceAgentRun, attempt.agent_run_id) if attempt.agent_run_id else None
            steps = (await db.scalars(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == run.id))).all() if run else []
            audit.append(dict(id=attempt.id, attempt_number=attempt.attempt_number, status=attempt.status,
                agent_run_id=attempt.agent_run_id, error=attempt.error, started_at=_iso(attempt.started_at),
                completed_at=_iso(attempt.completed_at), steps=[dict(status=s.status, provider=s.provider_name,
                    model=s.model_name, prompt_version=s.prompt_version, structured_retry=s.structured_retry,
                    latency_ms=s.latency_ms, token_usage=s.token_usage_json, error=s.error_message) for s in steps]))
        context = snapshot.context_json
        return dict(id=report.id, session_id=report.session_id, context_snapshot_id=report.context_snapshot_id,
            generation_number=report.generation_number, generation_status=report.generation_status, status=report.generation_status,
            is_current=report.is_current, statistics=deepcopy(report.statistics_json), coach_analysis=deepcopy(report.coach_analysis_json),
            producer=report.producer, provider=report.provider, model=report.model, prompt_version=report.prompt_version,
            error=report.error, created_at=_iso(report.created_at), completed_at=_iso(report.completed_at), attempts=audit,
            company_name=context["company_name"], job_title=context["job_title"],
            mode="replay" if report.provider == "replay" else "deterministic" if report.producer == "deterministic" else "live" if report.provider else "not_run")

    async def _session_detail(self, db, session, *, selected=None):
        snapshot = await db.get(InterviewContextSnapshot, session.context_snapshot_id)
        questions, evaluations = await interview_records(db, session.id)
        reports = (await db.scalars(select(FinalInterviewReport).where(FinalInterviewReport.session_id == session.id,
            FinalInterviewReport.user_id == session.user_id).order_by(FinalInterviewReport.generation_number.desc()))).all()
        history = [await self._serialize(db, row) for row in reports]
        current = next((row for row in history if row["is_current"]), None)
        report = next((row for row in history if selected and row["id"] == selected.id), None) if selected else current
        proposal_result = await MemoryProposalService(db).list(session.user_id, report_id=report["id"]) if report else dict(items=[])
        observations = (await db.scalars(select(InterviewPerformanceObservation).where(
            InterviewPerformanceObservation.report_id == report["id"]).order_by(InterviewPerformanceObservation.id))).all() if report else []
        context = snapshot.context_json
        adaptive_review = None
        if context.get("adaptive_policy"):
            from interview_api.modules.interview.adaptive import review_projection
            adaptive_review = review_projection(context, session.interview_plan_json)
        return dict(session=dict(id=session.id, title=session.title, status=session.status,
            company_name=context["company_name"], job_title=context["job_title"], context_snapshot_id=snapshot.id),
            statistics=report["statistics"] if report else deterministic_statistics(questions, evaluations),
            current_report=current, report=report, history=history, evaluations=evaluations,
            design_trace=deepcopy(session.planner_trace_json or {}) if context.get("system_design") else None,
            adaptive_review=adaptive_review,
            memory_proposals=proposal_result["items"], observations=[dict(id=o.id, report_id=o.report_id, session_id=o.session_id,
                observation_type=o.observation_type, text=o.text, supporting_evaluation_ids=o.supporting_evaluation_ids,
                occurrence_count=o.occurrence_count, created_at=_iso(o.created_at)) for o in observations])

    async def session_detail(self, session_id, user_id):
        async with self.factory() as db:
            session = await self._session(db, session_id, user_id)
            return await self._session_detail(db, session)

    async def detail(self, report_id, user_id):
        async with self.factory() as db:
            report = await db.scalar(select(FinalInterviewReport).where(FinalInterviewReport.id == report_id, FinalInterviewReport.user_id == user_id))
            if not report:
                raise AppError("REPORT_NOT_FOUND", "报告不存在", 404)
            session = await self._session(db, report.session_id, user_id)
            return await self._session_detail(db, session, selected=report)

    async def list_reports(self, user_id, *, limit=100, offset=0):
        async with self.factory() as db:
            total = await db.scalar(select(func.count()).select_from(FinalInterviewReport).where(FinalInterviewReport.user_id == user_id))
            reports = (await db.scalars(select(FinalInterviewReport).where(FinalInterviewReport.user_id == user_id)
                .order_by(FinalInterviewReport.created_at.desc(), FinalInterviewReport.id.desc()).offset(offset).limit(limit))).all()
            return dict(items=[await self._serialize(db, row) for row in reports], total=total)
