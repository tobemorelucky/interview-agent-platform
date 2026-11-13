"""Report generation is independent from completed InterviewSession state."""
import logging
from copy import deepcopy
from datetime import datetime, timezone
from functools import cached_property
from sqlalchemy import func, select

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import INTERVIEW_COACH_ROUTE, build_interview_coach_router
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.interview.models import InterviewContextSnapshot, InterviewSession
from interview_api.modules.interview_report.agent import SummaryCoachAgent, harness_enabled
from interview_api.modules.interview_report.models import FinalInterviewReport, InterviewReportAttempt, InterviewPerformanceObservation, PerformanceObservationEvaluation
from interview_api.modules.interview_report.prompts import coach_prompt_signature
from interview_api.modules.interview_report.statistics import interview_records, deterministic_statistics, evaluation_fingerprint
from interview_api.modules.memory.proposal_service import create_report_proposals, MemoryProposalService
from interview_api.modules.public_career.research.audit import finish_research_run
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun

logger = logging.getLogger(__name__)


def report_conflict(message="报告生成状态已变化，请刷新后重试"):
    return AppError("REPORT_GENERATION_CONFLICT", message, 409)


def coach_generation_key(*, session_id, content_fingerprint, evaluation_fp, prompt_version,
                         prompt_hash, request_key=None, harness=False, policy=None,
                         capability_fingerprint=None):
    """The one place a Coach report's identity is computed.

    OFF reproduces the historical formula exactly: the same session, snapshot,
    evaluations and prompt must resolve to the report that already exists, or a
    page load would mint a new generation and displace it.

    ON adds the two inputs the old formula could not see — the harness tool
    policy and the capability capture taken at report time — so a report written
    against one memory state is never reused as if it described another.
    """
    parts = ["interview_coach", session_id, content_fingerprint, evaluation_fp,
             prompt_version, prompt_hash, request_key]
    if harness:
        parts += [policy, capability_fingerprint]
    return digest(parts)


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

    async def _capability_history(self, db, user_id, snapshot):
        """One user's stored capability rows for this session's skill, captured once.

        Scoped three ways: the user comes from the session owner, the topics come
        from the frozen blueprint, and only reference-level observation data is
        kept (no answer text). Returns None when the session has no blueprint,
        which also means the history tool is never offered.
        """
        context = snapshot.context_json or {}
        blueprint = (context.get("adaptive_blueprint") or {}).get("targets") or []
        knowledge_ids = [item["knowledge_id"] for item in blueprint if item.get("knowledge_id")]
        if not knowledge_ids:
            return None
        from interview_api.modules.memory.capability_service import CapabilityMemoryService
        rows = await CapabilityMemoryService(db).list(user_id)
        index = {row["topic"]: row for row in rows}
        topics = {}
        for knowledge_id in knowledge_ids:
            topic = knowledge_id.rsplit(":", 1)[-1]
            row = index.get(topic)
            if not row:
                continue
            topics[topic] = dict(
                label=row.get("label"), state=row.get("state"), revision=row.get("revision"),
                revoked=bool(row.get("revoked")), independent_sessions=row.get("independent_sessions"),
                last_observed_at=row.get("last_observed_at"),
                reason_tags=list(row.get("reason_tags") or [])[:4],
                conflicts=[{key: item.get(key) for key in ("status", "reason", "scope")}
                           for item in (row.get("conflicts") or [])[:2]],
                observations=[dict(session_id=item.get("session_id"), evaluation_id=item.get("evaluation_id"),
                                   state=item.get("state"), observed_at=item.get("observed_at"),
                                   assisted=bool(item.get("assisted")), scope=item.get("scope"))
                              for item in (row.get("observations") or [])[-5:]])
        if not topics:
            return None
        return dict(
            semantics=("CAPTURED_AT_REPORT_GENERATION_NOT_THE_SESSION_SNAPSHOT: the session's "
                       "capability_context was frozen when the interview started; this capture "
                       "is what the report was written against"),
            captured_at=datetime.now(timezone.utc).isoformat(),
            # Content-only, so the same memory state always hashes the same way.
            fingerprint=digest(topics),
            topics=topics)

    def _training_scope(self, snapshot, capability_history):
        """Legal next-training targets, from the frozen blueprint and the same rule.

        `modes()` is the function Training uses; reusing it is what stops a model
        from recommending RECHECK for a topic that is not CONFLICTED/RECHECK_DUE.
        """
        from interview_api.modules.interview.training import modes
        context = snapshot.context_json or {}
        blueprint = (context.get("adaptive_blueprint") or {}).get("targets") or []
        captured = (capability_history or {}).get("topics") or {}
        targets = {}
        for item in blueprint:
            knowledge_id = item.get("knowledge_id")
            if not knowledge_id:
                continue
            row = captured.get(knowledge_id.rsplit(":", 1)[-1]) or {}
            state = row.get("state") or "UNKNOWN"
            revoked = bool(row.get("revoked"))
            targets[knowledge_id] = dict(label=item.get("label"), state=state, revoked=revoked,
                available_modes=modes(state, revoked), memory_revision=row.get("revision"))
        skill = ((context.get("interviewer_config") or {}).get("skill") or {})
        return dict(skill_id=skill.get("skill_id") or skill.get("id"),
            scope_label="USER_PROVIDED_JD" if context.get("job_target") else "PUBLIC_JOB",
            source="REPORT_TIME_CAPABILITY_WITH_SESSION_BLUEPRINT", targets=targets)

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
            # OFF must keep producing exactly the key it produced before, or an
            # existing completed report would stop matching itself and a reload
            # would mint a new generation.
            harness = harness_enabled()
            prompt_version, prompt_hash = coach_prompt_signature(harness)
            policy = capability_fingerprint = None
            if harness:
                # The harness has two inputs the old key could not see: the tool
                # policy that shapes what the model may do, and the capacity
                # capture taken at report time. Without them, memory changing
                # after a report was written would silently reuse a report whose
                # suggestion was validated against different capability states.
                # Captured before the key so the key and the payload describe the
                # same frozen input.
                from interview_api.modules.interview_report import tools
                capability_history = await self._capability_history(db, user_id, snapshot)
                policy = tools.policy_signature()
                capability_fingerprint = (capability_history or {}).get("fingerprint")
            key = coach_generation_key(session_id=session_id, content_fingerprint=snapshot.content_fingerprint,
                evaluation_fp=fingerprint, prompt_version=prompt_version, prompt_hash=prompt_hash,
                request_key=request_key if regenerate else None, harness=harness, policy=policy,
                capability_fingerprint=capability_fingerprint)
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
                    statistics_json=statistics, producer="PENDING", prompt_version=prompt_version, attempt_version=0)
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
            completion = session.completion_json or {}
            completion_fact = None
            if completion.get("completion_reason"):
                # Termination facts only. Counts stay derived from persisted
                # evaluations, never from this summary.
                evaluated = completion.get("evaluated_count") or 0
                budget = completion.get("question_budget")
                completion_fact = dict(
                    completion_reason=completion.get("completion_reason"),
                    completion_detail=completion.get("completion_detail"),
                    question_budget=budget,
                    answered_count=completion.get("answered_count"),
                    evaluated_count=completion.get("evaluated_count"),
                    early_terminated=bool(budget) and evaluated < budget,
                    semantics="CONTROLLER_TERMINATION_FACT_NOT_PERFORMANCE_EVIDENCE")
            review = None
            if snapshot.context_json.get("adaptive_policy"):
                from interview_api.modules.interview.adaptive import review_projection
                review = review_projection(snapshot.context_json, session.interview_plan_json)
            from interview_api.modules.interview_report.evidence import coach_evidence
            payload, projection = coach_evidence(snapshot=snapshot.context_json, statistics=statistics,
                questions=[dict(id=q.id, question=q.question, standard_answer=q.standard_answer, source=q.source,
                    question_kind=q.question_kind, status=q.status, candidate_refs=q.candidate_refs_json,
                    job_requirement_id=q.job_requirement_id) for q in questions],
                evaluations=evaluations, completion=completion_fact, review=review, fingerprint=fingerprint)
            # Captured once, at the start of this attempt, and then held still:
            # a tool call two seconds later must see the same numbers, and the
            # capture's content hash is recorded so a reviewer can prove it.
            # The harness captures it before the key (it is part of the frozen
            # input); the OFF path keeps its original query order.
            if not harness:
                capability_history = await self._capability_history(db, user_id, snapshot)
            training_scope = self._training_scope(snapshot, capability_history)
            logger.info("coach evidence projection %s bytes=%s sections=%s", projection["version"],
                        projection["bytes"], projection["section_bytes"])
            await db.commit()
        result, published = None, False
        try:
            if evaluations:
                result = await self.agent.call(report_id, attempt_id, payload, session_id=session_id,
                    capability_history=capability_history, training_scope=training_scope)
                analysis = result.resolved
                if result.audit:
                    # Auditable, additive, and absent on the OFF path.
                    analysis = {**analysis, "audit": result.audit}
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
        # A private JD has no company/job row; the report header must be able to
        # say so instead of presenting the pasted company name as an employer.
        target = context.get("job_target") or {}
        # `job_id`/`company_id` are needed by the report page's "go to training"
        # link: without them the link can pre-select the skill and the mode but
        # loses the job, so the user lands on a page asking which job they meant.
        return dict(session=dict(id=session.id, title=session.title, status=session.status,
            company_name=context["company_name"], job_title=context["job_title"], context_snapshot_id=snapshot.id,
            job_target_id=target.get("id"), job_target_version=target.get("version"),
            job_id=context.get("job_id"), company_id=context.get("company_id")),
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
