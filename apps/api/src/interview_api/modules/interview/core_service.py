"""M4 orchestration: read/close -> model -> short Controller transaction -> events."""
import json
import logging
import asyncio
from datetime import datetime, timezone
import anyio
from functools import cached_property
from copy import deepcopy
from uuid import uuid4
from interview_api.core.config import settings
from sqlalchemy import select

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import build_interview_router
from interview_api.modules.interview.agents import InterviewMainAgent, AnswerEvaluationAgent
from interview_api.modules.interview.controller import SessionController, question_data
from interview_api.modules.interview.context import question_policy, model_context
from interview_api.modules.interview.topics import coverage, requirement_opening_question
from interview_api.modules.interview.end_intent import end_intent, end_intent_detail
from interview_api.modules.interview.models import InterviewSession, InterviewMessage, InterviewSessionQuestion, InterviewAnswer, InterviewContextSnapshot, InterviewEvaluation

logger = logging.getLogger(__name__)


def session_policy(context, questions, action="NEXT"):
    if context.get("system_design"):
        from interview_api.modules.interview.system_design import design_policy
        return design_policy(context, questions, action)
    return question_policy(context, questions, action)


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class SystemOpening:
    """A server-authored opening question, never presented as model output.

    Used only when the operator explicitly asks for it after a model question
    failed the frozen topic gate. The wording quotes the frozen JD requirement
    and names the frozen target, and the result still goes through
    ``validate_decision`` — nothing is forced through.
    """
    provider = "system"
    run_id = None
    usage = {}

    def __init__(self, artifact):
        self.artifact = artifact


class SystemMain:
    """Server-authored NEXT after a rejected model question. No model call."""
    provider = "deterministic"
    model = None
    prompt_version = None
    retry = 0
    run_id = None
    usage = {}

    def __init__(self, artifact, policy=None):
        self.artifact = artifact
        self.policy = policy


class RecoveredEvaluation:
    """A validated evaluation artifact replayed from a persisted checkpoint."""

    def __init__(self, artifact, *, provider, model, prompt_version, retry, usage, run_id):
        self.artifact = artifact
        self.provider, self.model, self.prompt_version = provider, model, prompt_version
        self.retry, self.usage, self.run_id = retry, usage, run_id


class MockInterviewService:
    def __init__(self, factory, router=None):
        self.factory = factory
        self._router = router

    @cached_property
    def router(self):
        return self._router or build_interview_router()

    @cached_property
    def main(self):
        return InterviewMainAgent(self.factory, self.router)

    @cached_property
    def evaluator(self):
        return AnswerEvaluationAgent(self.factory, self.router)

    async def create(self, user_id, request):
        # Every training creation goes through TrainingService, whichever fields
        # the client sent. It used to route on `training_mode and key`, so a
        # request without a key silently became a *plain* session that still
        # carried a frozen `training` block: no lock, no reuse, and a retried
        # request produced a second training session that looked legitimate.
        if request.training_mode:
            from interview_api.modules.interview.training import TrainingService
            return await TrainingService(self.factory).create(user_id, request)
        async with self.factory() as db:
            session = await SessionController(db).create(user_id, request)
            await db.commit()
            session_id = session.id
        return await self.detail(session_id, user_id)

    async def read_state(self, session_id, user_id):
        async with self.factory() as db:
            return await SessionController(db).state(session_id, user_id)

    async def call_agent(self, agent, session_id, user_id, expected, payload, *, answer_id=None):
        # One scope per call, on this service's own factory: the owner is the
        # session owner, the route is the agent's route, and the ledger database is
        # the database this session lives in. Setting it here means a provider
        # wrapper that forgets to declare `governed` can never bill the caller
        # under an ambient user or the process default database.
        from interview_api.infrastructure.models.scope import model_scope_for
        with model_scope_for(self.factory, user_id=user_id, route=agent.route):
            return await self._call_agent(agent, session_id, user_id, expected, payload, answer_id=answer_id)

    async def _call_agent(self, agent, session_id, user_id, expected, payload, *, answer_id=None):
        if agent.route == self.main.route and payload.get("snapshot", {}).get("adaptive_policy", {}).get("main_harness_version"):
            from interview_api.modules.interview.main_harness import MainHarness
            return await MainHarness(self, agent).call(session_id, user_id, expected, payload, answer_id)
        if agent.route == self.evaluator.route and payload.get('adaptive'):
            payload = await self.evaluation_context(session_id, user_id, payload, answer_id=answer_id)
        if agent.route == self.evaluator.route and answer_id is not None:
            reused = await agent.reusable(session_id, answer_id, payload)
            if reused is not None:
                return reused
        if payload.get("adaptive") and agent.route == self.main.route and answer_id is not None:
            reused = await agent.reusable(session_id, answer_id, payload)
            if reused is not None:
                return reused
        # An admission estimate is temporary, not permanent token usage. Include
        # UTF-8 input units and both bounded output attempts, then settle to audit.
        provider = self.router.resolve(agent.route)
        admission_payload = payload
        system_bytes = 0
        from interview_api.modules.interview.context_projection import enabled, model_payload
        if enabled(payload):
            from interview_api.modules.interview.skill_engine import agent_payload
            from interview_api.modules.interview.agents import configured_prompt_registry
            sanitized = agent_payload(payload, agent.route)
            admission_payload = model_payload(sanitized, agent.route)
            system_bytes = len(configured_prompt_registry(sanitized).get(agent.route).system.encode('utf-8'))
        output_cap = getattr(provider, "max_output_tokens", settings.interview_model_max_output_tokens)
        reservation = 0 if provider.provider_name == "replay" else (
            len(json.dumps(admission_payload, ensure_ascii=False).encode("utf-8")) + system_bytes +
            len(json.dumps(agent.schema.model_json_schema()).encode("utf-8")) +
            2 * (output_cap + 2048))
        return await self.with_model_reservation(session_id, user_id, expected, provider, reservation,
            lambda timeout: agent.call(session_id, payload, answer_id=answer_id, timeout_seconds=timeout),
            answer_id=answer_id)

    async def with_model_reservation(self, session_id, user_id, expected, provider, reservation, operation, *, answer_id=None):
        call_key = uuid4().hex
        async with self.factory() as db:
            timeout = await SessionController(db).reserve_model_call(session_id, user_id, expected, reservation, call_key)
            await db.commit()
        if timeout is None:
            return None
        usage_reported = provider.provider_name == "replay"
        known_tokens = 0
        request_sent = None
        try:
            result = await operation(timeout)
            known_tokens = result.usage.get('total_tokens') or 0
            usage_reported = usage_reported or (type(result.usage.get('total_tokens')) is int and result.usage_complete)
            return result
        except BaseException as exc:
            total = (getattr(exc, 'usage', {}) or {}).get('total_tokens')
            audited = getattr(exc, 'usage_audited', True)
            request_sent = getattr(exc, 'request_sent', None)
            known_tokens = total if audited and type(total) is int and total >= 0 else 0
            usage_reported = usage_reported or (audited and type(total) is int and total >= 0 and getattr(exc, 'usage_complete', True))
            raise
        finally:
            with anyio.CancelScope(shield=True):
                async with self.factory() as db:
                    await SessionController(db).settle_model_call(session_id, user_id, call_key, usage_reported,
                        known_tokens=known_tokens, request_sent=request_sent, answer_id=answer_id)
                    await db.commit()

    async def evaluation_context(self, sid, uid, payload, *, answer_id=None):
        """Read-only, deterministic single-parent projection, before reuse/hash/admission.

        No history, past scores or inferred memory. An absent hint event is UNKNOWN.
        Completed parent Q/A are immutable; bounded raw prefixes retain provenance.
        """
        from interview_api.modules.interview.context_projection import SCORING_VERSION
        contract = payload['snapshot'].get('adaptive_policy', {}).get('evaluation_context')
        if contract is None:
            return payload  # historical frozen inputs are not upgraded
        if contract != SCORING_VERSION:
            raise AppError('SCORING_CONTEXT_UNSUPPORTED', '评分语境版本不可用', 409)
        async with self.factory() as db:
            session = await SessionController(db).owned(sid, uid)
            snapshot = await db.get(InterviewContextSnapshot, session.context_snapshot_id)
            if (snapshot.user_id != uid or snapshot.interview_session_id != sid or
                    snapshot.context_json.get('adaptive_policy', {}).get('evaluation_context') != contract):
                raise AppError('SCORING_CONTEXT_VERSION', '评分语境与本场冻结版本不一致', 409)
            qid = (payload.get('current_question') or {}).get('question_id')
            child = await db.scalar(select(InterviewSessionQuestion).where(
                InterviewSessionQuestion.id == qid, InterviewSessionQuestion.session_id == sid))
            if child is None:
                raise AppError('SCORING_QUESTION_NOT_FOUND', '本场评分题目不存在', 404)
            if answer_id is not None:
                current = (await db.execute(select(InterviewAnswer, InterviewMessage).join(
                    InterviewMessage, InterviewMessage.id == InterviewAnswer.message_id).where(
                    InterviewAnswer.id == answer_id, InterviewAnswer.session_id == sid,
                    InterviewAnswer.session_question_id == qid, InterviewMessage.session_id == sid,
                    InterviewMessage.role == 'USER'))).first()
                if current is None or current[1].content != payload.get('answer'):
                    raise AppError('SCORING_ANSWER_NOT_FOUND', '评分回答不属于本场当前题目', 404)
            kind = child.question_kind or 'UNKNOWN'
            sc = dict(version=contract, question_kind=kind, parent_question_id=None,
                parent_status='NOT_APPLICABLE' if kind == 'NEXT' and child.parent_question_id is None else 'UNAVAILABLE',
                parent=None, assistance_status='UNKNOWN',
                open_gaps=deepcopy(payload['adaptive']['knowledge_state']['items'].get(
                    payload['adaptive']['active_knowledge_id'], {}).get('open_gaps', [])))
            if child.parent_question_id is not None and kind in ('FOLLOW_UP', 'DEEP_DIVE'):
                row = (await db.execute(select(InterviewSessionQuestion, InterviewAnswer, InterviewMessage)
                    .join(InterviewAnswer, InterviewAnswer.session_question_id == InterviewSessionQuestion.id)
                    .join(InterviewMessage, InterviewMessage.id == InterviewAnswer.message_id)
                    .join(InterviewEvaluation, InterviewEvaluation.answer_id == InterviewAnswer.id)
                    .where(InterviewSessionQuestion.id == child.parent_question_id,
                        InterviewSessionQuestion.session_id == sid, InterviewSessionQuestion.status == 'COMPLETED',
                        InterviewSessionQuestion.question_index < child.question_index,
                        InterviewAnswer.session_id == sid, InterviewAnswer.status == 'COMPLETED',
                        InterviewMessage.session_id == sid, InterviewMessage.role == 'USER',
                        InterviewEvaluation.session_id == sid,
                        InterviewEvaluation.session_question_id == InterviewSessionQuestion.id))).first()
                if row:
                    parent, answer, message = row
                    def excerpt(text, identifier, limit):
                        return dict(id=identifier, content=text[:limit], start=0, end=min(len(text), limit),
                            total_chars=len(text), truncated=len(text) > limit)
                    sc.update(parent_question_id=parent.id, parent_status='AVAILABLE', parent=dict(
                        question=excerpt(parent.question, parent.id, 512),
                        answer={**excerpt(message.content, answer.id, 1024), 'message_id': message.id}))
        result = deepcopy(payload)
        result['scoring_context'] = sc
        return result

    @staticmethod
    def payload(context, questions, active, action, policy, *, answer=None, evaluation=None):
        desired = None
        if action == "ADJUST_DIFFICULTY" and active and evaluation:
            desired = SessionController.adjusted_difficulty(active, evaluation)
        if context.get("interviewer_config") and desired is None:
            desired = active.difficulty if active else "MEDIUM"
        selected_policy = policy or (dict(source_type="PUBLIC_QUESTION", source_id=active.source_question_id) if active else {})
        return dict(snapshot=model_context(context, selected_policy), requested_action=action, preferred_source=policy, desired_difficulty=desired,
            topic_coverage=coverage(questions, context["budgets"]["question_count"]),
            history=[dict(question_id=q.id, question=q.question, status=q.status, source=q.source, difficulty=q.difficulty) for q in questions],
            current_question=question_data(active, context["budgets"]["question_count"]) if active else None,
            rubric=(active.standard_answer or "").splitlines() if active else [],
            answer=answer, evaluation=evaluation)

    @staticmethod
    def requirement_opening(payload):
        """Build and validate a zero-model opening question from frozen evidence."""
        from interview_api.modules.interview.adaptive import (
            AdaptiveSelection, ScopedAdaptiveSelection, QuestionDraft, resolve_selection,
            validate_decision, uses_gap_scope)
        adaptive = payload["adaptive"]
        requirements = {r["id"]: r for r in payload["snapshot"].get("requirements") or []}
        chosen = next((c for c in adaptive.get("candidates") or []
                       if c["source"]["source_type"] == "JOB_REQUIREMENT"
                       and "NEXT" in c["strategies"]
                       and c["source"]["source_id"] in requirements), None)
        if chosen is None:
            raise AppError("REQUIREMENT_OPENING_UNAVAILABLE",
                           "本场没有可用于开场的岗位要求，请改用模型出题", 409)
        statement = requirements[chosen["source"]["source_id"]]["statement"].strip()
        label = chosen.get("label") or chosen["topic"]
        question = requirement_opening_question(statement, label)
        draft = QuestionDraft(question=question,
                              expected_points=[f"{label}的实际做法", "一次具体问题与解决"],
                              dimension=label[:50],
                              difficulty=payload.get("desired_difficulty") or "MEDIUM")
        selection_cls = ScopedAdaptiveSelection if uses_gap_scope(payload) else AdaptiveSelection
        extra = {"focus_gap_ids": []} if uses_gap_scope(payload) else {}
        selection = selection_cls(candidate_id=chosen["candidate_id"], strategy="NEXT",
                                 supporting_refs=[chosen["source"]["ref"]], reason_tags=["OPENING"],
                                 proposed_question=draft, **extra)
        artifact = resolve_selection(selection, payload)
        return artifact, validate_decision(artifact, payload)

    async def record_topic_mismatch(self, session_id, user_id, exc):
        """Persist why a NEXT question was rejected; never masks the error."""
        from interview_api.modules.audit.models import AuditLog
        try:
            async with self.factory() as db:
                db.add(AuditLog(actor_user_id=user_id, actor_role="USER",
                                action="ADAPTIVE_TOPIC_MISMATCH", resource_type="interview_session",
                                resource_id=str(session_id), metadata_json=exc.diagnostic,
                                status="REJECTED", error_message=exc.message[:500]))
                await db.commit()
        except Exception:
            logger.warning("topic mismatch audit write failed for session %s", session_id, exc_info=True)

    # The user-facing default lives in the HTTP router (INTRODUCTION) and the
    # web client; this service default stays MODEL for internal/scripted callers.
    async def start(self, session_id, user_id, *, opening="MODEL"):
        session, context, questions, active, _ = await self.read_state(session_id, user_id)
        if session.status == "IN_PROGRESS" and active:
            return question_data(active, session.question_count)
        if session.status != "READY":
            raise AppError("INTERVIEW_NOT_READY", "面试无法开始", 409)
        if opening == "INTRODUCTION":
            # The default real-interview opening: a scripted, zero-model
            # self-introduction. Its answer is never scored as a technical one.
            async with self.factory() as db:
                data = await SessionController(db).start_introduction(session_id, user_id, session.state_version)
                await db.commit()
            return data
        if context.get("adaptive_policy"):
            from interview_api.modules.interview.adaptive import planning_payload, validate_decision
            payload = planning_payload(context, questions, None, session.interview_plan_json)
            if opening == "REQUIREMENT":
                # Explicit, user-chosen fallback: no model call, no fabricated
                # question — the wording quotes the frozen JD requirement and
                # names the frozen target. It is still validated by the same gate.
                artifact, policy = self.requirement_opening(payload)
                result = SystemOpening(artifact=artifact)
                async with self.factory() as db:
                    data = await SessionController(db).start(session_id, user_id, session.state_version, result, policy)
                    await db.commit()
                return data
            result = await self.call_agent(self.main.adaptive(payload), session_id, user_id, session.state_version, payload)
            if result is None:
                raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "首题预算不足", 409)
            try:
                policy = validate_decision(result.artifact, payload)
            except AppError as exc:
                if getattr(exc, "diagnostic", None):
                    await self.record_topic_mismatch(session_id, user_id, exc)
                raise
            try:
                async with self.factory() as db:
                    data = await SessionController(db).start(session_id, user_id, session.state_version, result, policy)
                    await db.commit()
            except AppError as exc:
                if context.get('training') or context["adaptive_policy"].get("main_harness_version"):
                    from interview_api.modules.interview.training import controller_rejection
                    await controller_rejection(self.factory,result.run_id,exc.code)
                raise
            return data
        policy = session_policy(context, questions)
        result = await self.call_agent(self.main, session_id, user_id, session.state_version,
                                      self.payload(context, questions, None, "NEXT", policy))
        if result is None:
            raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "模型调用预算不足，未生成题目", 409)
        async with self.factory() as db:
            data = await SessionController(db).start(session_id, user_id, session.state_version, result, policy)
            await db.commit()
        return data

    async def end(self, session_id, user_id, *, ended_by="USER"):
        """User-initiated end. One transaction, no model, no question generation."""
        from interview_api.modules.audit.models import AuditLog
        async with self.factory() as db:
            result = await SessionController(db).end_session(session_id, user_id, ended_by=ended_by)
            db.add(AuditLog(actor_user_id=user_id, actor_role="USER", action="INTERVIEW_USER_ENDED",
                            resource_type="interview_session", resource_id=str(session_id),
                            metadata_json=dict(already_completed=result["already_completed"],
                                               completion_reason=(result["completion"] or {}).get("completion_reason"),
                                               early_terminated=(result["completion"] or {}).get("early_terminated"),
                                               answered_count=(result["completion"] or {}).get("answered_count"),
                                               excluded=result["excluded"]),
                            status="SUCCEEDED"))
            await db.commit()
            return result

    async def answer(self, session_id, user_id, question_id, key, content, on_stage=None, design_plan=None,
                     allow_unknown_retry=False, confirm_off_topic=False, restart_after_off_topic=False):
        _, initial_context, _, _, _ = await self.read_state(session_id, user_id)
        from interview_api.modules.interview.answer_relevance import classify
        pre = classify(content, "", job_title="", skill_label="")
        if pre.kind == "USER_CONTROL" and pre.reason == "CHANGE_TOPIC":
            # Deterministic control intent: never scored, never remembered.
            raise AppError("INTERVIEW_TOPIC_CHANGE", "检测到换题/暂停请求。确认后将跳过本题，本题不计分、不写入成长记忆。", 409)
        if end_intent(content):
            # Never becomes an answer: no message row, no evaluation, no charge.
            # The UI turns this into a confirmation and calls /sessions/{id}/end.
            error = AppError("INTERVIEW_END_INTENT",
                "这看起来是想结束面试。确认后会保留已完成的回答并生成报告，不再出新的技术问题。", 409)
            error.end_intent = end_intent_detail(content)
            raise error
        if bool(initial_context.get("system_design")) != (design_plan is not None):
            raise AppError("DESIGN_PLAN_REQUIRED", "系统设计面试须同时提交结构化方案；普通面试不接收方案", 422)
        # Retrying the same key resumes one persisted Answer, not a new user message.
        async with self.factory() as db:
            answer, expected, reused = await SessionController(db).prepare_answer(session_id, user_id, question_id, key, content,
                allow_unknown_retry=allow_unknown_retry, restart_after_off_topic=restart_after_off_topic)
            if design_plan is not None:
                revision = await SessionController(db).bind_design_plan(session_id, user_id, answer, expected, design_plan)
            await db.commit()
            answer_id, old_events = answer.id, answer.result_events
        if reused:
            return old_events
        if not confirm_off_topic:
            _, context, _, active, _ = await self.read_state(session_id, user_id)
            skill = (context.get("interviewer_config") or {}).get("skill") or {}
            verdict = classify(content, active.question if active else "",
                job_title=context.get("job_title") or "",
                skill_label=str(skill.get("label") or skill.get("name") or ""))
            if verdict.kind == "OFF_TOPIC_SUSPECTED":
                # Stop before Evaluation: an obvious paste must never become a
                # score, a technical-memory observation or a Coach proposal.
                from interview_api.modules.audit.models import AuditLog
                async with self.factory() as db:
                    db.add(AuditLog(actor_user_id=user_id, actor_role="USER",
                        action="OFF_TOPIC_CONFIRMATION_REQUIRED", resource_type="interview_answer",
                        resource_id=str(answer_id), metadata_json=verdict.detail,
                        status="REJECTED", error_message="obvious off-topic answer; not scored"))
                    await SessionController(db).failed_answer(session_id, user_id, answer_id, expected,
                        "OFF_TOPIC_CONFIRMATION_REQUIRED")
                    await db.commit()
                raise AppError("OFF_TOPIC_CONFIRMATION_REQUIRED",
                    "你这段回答似乎和刚才的问题不太对应，是不是粘贴错了？请确认按原文继续，或重新回答。", 409)
        try:
            session, context, questions, active, _ = await self.read_state(session_id, user_id)
            if active is not None and active.question_kind == "INTRODUCTION":
                return await self.introduction_answer(session_id, user_id, answer_id, expected, content, on_stage)
            if context.get("adaptive_policy"):
                return await self.adaptive_answer(session_id, user_id, answer_id, expected, content, on_stage)
            tool_payload = {}
            if context.get("system_design"):
                if on_stage: on_stage("validating_design")
                evidence = await self.design_tool_evidence(session_id, user_id, answer_id, expected, revision, context, questions, active, content)
                session, context, questions, active, _ = await self.read_state(session_id, user_id)
                tool_payload = dict(tool_evidence=evidence, design_revision=revision,
                    previous_tool_evidence=[i for i in (session.planner_trace_json or {}).get("design_tools", []) if i["status"] == "SUCCEEDED" and i["id"] != evidence["id"]])
            async with self.factory() as db:
                await SessionController(db).answer_stage(session_id, user_id, answer_id, expected, "generating_evaluation")
                await db.commit()
            if on_stage:
                on_stage("generating_evaluation")
            eval_result = await self.call_agent(self.evaluator, session_id, user_id, expected,
                {**self.payload(context, questions, active, "NEXT", {}, answer=content), **tool_payload}, answer_id=answer_id)
            if eval_result is None:
                raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "本场时间或模型预算已不足，未执行评价、未结束或推进面试。回答已保存；重试不会重置预算，请新建面试继续练习。", 409)
            # Audit token usage is included before choosing the next deterministic action.
            session, context, questions, active, tokens = await self.read_state(session_id, user_id)
            action = SessionController.choose_action(context, questions, active, eval_result.artifact.next_action_recommendation,
                rounds=session.turn_count, tokens=tokens, started_at=session.started_at, evaluation=eval_result.artifact.model_dump())
            policy = session_policy(context, questions, action)
            main_result = None
            if action != "COMPLETE":
                async with self.factory() as db:
                    await SessionController(db).answer_stage(session_id, user_id, answer_id, expected, "generating_next_question")
                    await db.commit()
                if on_stage:
                    on_stage("generating_next_question")
                main_result = await self.call_agent(self.main, session_id, user_id, expected,
                    {**self.payload(context, questions, active, action, policy, answer=content,
                                 evaluation=eval_result.artifact.model_dump()), **tool_payload}, answer_id=answer_id)
                if main_result is None:
                    raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "下一题调用预算已不足，未结束面试。回答已保存；重试不会重置预算，请新建面试继续练习。", 409)
            async with self.factory() as db:
                events = await SessionController(db).commit_answer(session_id, user_id, answer_id, expected,
                    eval_result, main_result, action, policy)
                await db.commit()
            return events
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                async with self.factory() as db:
                    await SessionController(db).failed_answer(session_id, user_id, answer_id, expected, "interview_request_cancelled")
                    await db.commit()
            raise
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            async with self.factory() as db:
                await SessionController(db).failed_answer(session_id, user_id, answer_id, expected, code)
                await db.commit()
            raise

    async def introduction_answer(self, sid, uid, aid, expected, content, on_stage):
        """Self-introduction answer: no evaluation, straight to the first target.

        Grading an introduction would either inflate failure statistics or fake
        technical evidence for a question that has none. The main agent still
        makes a fully validated NEXT decision, so provenance and topic legality
        are unchanged; there is simply no answer_evaluation call for this turn.
        """
        from interview_api.modules.interview.adaptive import planning_payload, validate_decision
        session, context, questions, active, _ = await self.read_state(sid, uid)
        payload = planning_payload(context, questions, active, session.interview_plan_json,
            answer=content, answer_id=aid)
        if on_stage: on_stage("generating_next_question")
        main = await self.call_agent(self.main.adaptive(payload), sid, uid, expected, payload, answer_id=aid)
        if main is None:
            raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "开场后的第一题预算不足，回答已保存，未推进", 409)
        policy = validate_decision(main.artifact, payload)
        try:
            async with self.factory() as db:
                events = await SessionController(db).commit_introduction(sid, uid, aid, expected, main, policy)
                await db.commit()
        except AppError as exc:
            if context.get('training') or context["adaptive_policy"].get("main_harness_version"):
                from interview_api.modules.interview.training import controller_rejection
                await controller_rejection(self.factory, main.run_id, exc.code)
            raise
        return events

    async def recovered_evaluation(self, sid, uid, aid):
        """Reuse a validated evaluation instead of paying for it a second time.

        Source order: this answer's checkpoint, then a SUCCEEDED evaluation run
        already linked to the same answer in the model ledger. A committed
        Evaluation row always wins — no score is ever added twice.
        """
        from interview_api.modules.interview.adaptive import AdaptiveEvaluation, bounded
        from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
        from interview_api.modules.interview.models import InterviewAgentCall
        async with self.factory() as db:
            session = await SessionController(db).owned(sid, uid, lock=True)
            if await db.scalar(select(InterviewEvaluation.id).where(InterviewEvaluation.answer_id == aid).limit(1)):
                return None
            recovery = (session.interview_plan_json or {}).get("answer_recovery") or {}
            if recovery.get("answer_id") == aid and recovery.get("artifact"):
                artifact = AdaptiveEvaluation.model_validate(recovery["artifact"])
                logger.info("reusing evaluation checkpoint for answer %s (stage=%s)", aid, recovery.get("stage"))
                return RecoveredEvaluation(artifact, provider=recovery.get("provider"), model=recovery.get("model"),
                    prompt_version=recovery.get("prompt_version"), retry=recovery.get("retry") or 0,
                    usage=recovery.get("usage") or {}, run_id=recovery.get("run_id"))
            runs = (await db.scalars(select(ExperienceAgentRun).join(InterviewAgentCall,
                InterviewAgentCall.agent_run_id == ExperienceAgentRun.id).where(
                InterviewAgentCall.answer_id == aid, ExperienceAgentRun.status == "SUCCEEDED")
                .order_by(ExperienceAgentRun.id.desc()))).all()
            for run in runs:
                output = run.output_json or {}
                if output.get("schema") != "AdaptiveEvaluation" or not output.get("validated_artifact"):
                    continue
                artifact = AdaptiveEvaluation.model_validate(output["validated_artifact"])
                step = await db.scalar(select(ExperienceAgentStepRun).where(
                    ExperienceAgentStepRun.agent_run_id == run.id).order_by(ExperienceAgentStepRun.id))
                result = RecoveredEvaluation(artifact,
                    provider=step.provider_name if step else None, model=step.model_name if step else None,
                    prompt_version=step.prompt_version if step else None,
                    retry=(step.structured_retry or 0) if step else 0,
                    usage=(step.token_usage_json or {}) if step else {}, run_id=run.id)
                plan = dict(session.interview_plan_json or {})
                plan["answer_recovery"] = dict(answer_id=aid, stage="RECOVERED_FROM_AUDIT_RUN",
                    artifact=artifact.model_dump(), provider=result.provider, model=result.model,
                    prompt_version=result.prompt_version, retry=result.retry, usage=result.usage,
                    run_id=run.id, source=f"experience_agent_run:{run.id}",
                    at=datetime.now(timezone.utc).isoformat())
                session.interview_plan_json = bounded(plan, maximum=192_000)
                await db.commit()
                logger.info("recovered evaluation for answer %s from run %s", aid, run.id)
                return result
        return None

    async def fallback_after_mismatch(self, sid, uid, aid, expected, evaluation, payload, exc,
                                      rejected_target=None):
        """Audit the rejected model question and replace it without another call."""
        from interview_api.modules.interview.adaptive import fallback_selection
        await self.record_topic_mismatch(sid, uid, exc)
        async with self.factory() as db:
            await SessionController(db).checkpoint_evaluation(sid, uid, aid, expected,
                evaluation, "NEXT_REJECTED", diagnostic=getattr(exc, "diagnostic", None), source="interview_main")
            await db.commit()
        fallback, policy = fallback_selection(payload, rejected_target)
        logger.info("replaced rejected NEXT with deterministic fallback for answer %s", aid)
        return SystemMain(fallback, policy), policy

    async def adaptive_answer(self, sid, uid, aid, expected, content, on_stage):
        from interview_api.modules.interview.adaptive import (
            fallback_selection, planning_payload, validate_decision, all_goals_demonstrated)
        session, context, questions, active, _ = await self.read_state(sid, uid)
        payload = planning_payload(context, questions, active, session.interview_plan_json, answer=content, answer_id=aid)
        evaluation = await self.recovered_evaluation(sid, uid, aid)
        if evaluation is None:
            if on_stage: on_stage("generating_evaluation")
            evaluation = await self.call_agent(self.evaluator.adaptive(), sid, uid, expected, payload, answer_id=aid)
            if evaluation is None:
                raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "评价预算不足，回答已保存，未推进或结束", 409)
            async with self.factory() as db:
                await SessionController(db).checkpoint_evaluation(sid, uid, aid, expected, evaluation, "EVALUATED")
                await db.commit()
        session, context, questions, active, tokens = await self.read_state(sid, uid)
        payload = planning_payload(context, questions, active, session.interview_plan_json, answer=content,
            answer_id=aid, evaluation=evaluation.artifact, evaluation_run_id=evaluation.run_id)
        exhausted = SessionController.choose_action(context, questions, active, "NEXT", rounds=session.turn_count,
            tokens=tokens, started_at=session.started_at) == "COMPLETE"
        main, policy, action = None, {}, "COMPLETE"
        terminal_detail = terminal_reason = None
        if not exhausted and not all_goals_demonstrated(context, payload["adaptive"]["knowledge_state"]) and payload["adaptive"]["choices"]:
            async with self.factory() as db:
                await SessionController(db).answer_stage(sid, uid, aid, expected, "generating_next_question")
                await db.commit()
            if on_stage: on_stage("generating_next_question")
            try:
                main = await self.call_agent(self.main.adaptive(payload), sid, uid, expected, payload, answer_id=aid)
            except AppError as exc:
                if exc.code != "ADAPTIVE_TOPIC_MISMATCH":
                    raise
                main, policy = await self.fallback_after_mismatch(sid, uid, aid, expected, evaluation, payload, exc)
            if main is None:
                # call_agent returns None only when the session refused this logical
                # call before any HTTP was sent. The evaluation above is already
                # validated and reused, so commit it and end with the real limit.
                session, _, _, _, _ = await self.read_state(sid, uid)
                refused = (session.interview_plan_json or {}).get("budget_exhausted_reason")
                if refused == "TOKENS":
                    terminal_detail, terminal_reason = "NEXT_QUESTION_RESERVATION", "TOKEN_LIMIT"
                elif refused == "DURATION":
                    terminal_detail, terminal_reason = "NEXT_QUESTION_DURATION", "DURATION_LIMIT"
                else:
                    # Unidentified refusal is not proof of a budget stop.
                    raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "下一题预算不足，回答已保存，未推进", 409)
            else:
                if isinstance(main, SystemMain):
                    policy = main.policy  # already passed the same gate in fallback_selection
                else:
                    try:
                        policy = validate_decision(main.artifact, payload)
                    except AppError as exc:
                        if exc.code != "ADAPTIVE_TOPIC_MISMATCH":
                            raise
                        main, policy = await self.fallback_after_mismatch(sid, uid, aid, expected,
                            evaluation, payload, exc, rejected_target=main.artifact.target_knowledge_id)
                action = main.artifact.recommendation
        try:
            async with self.factory() as db:
                events = await SessionController(db).commit_answer(sid, uid, aid, expected, evaluation, main, action, policy,
                    terminal_detail=terminal_detail, terminal_reason=terminal_reason)
                await db.commit()
        except AppError as exc:
            if main and (context.get('training') or context["adaptive_policy"].get("main_harness_version")):
                from interview_api.modules.interview.training import controller_rejection
                await controller_rejection(self.factory,main.run_id,exc.code)
            raise
        return events

    async def stream(self, session_id, user_id, request):
        yield sse("status", {"stage": "evaluating"})
        stages = asyncio.Queue()
        task = asyncio.create_task(self.answer(session_id, user_id, request.question_id,
            request.idempotency_key, request.content, on_stage=stages.put_nowait,
            design_plan=request.design_plan.model_dump() if request.design_plan else None,
            allow_unknown_retry=request.confirm_unknown_retry,
            confirm_off_topic=request.confirm_off_topic,
            restart_after_off_topic=request.restart_after_off_topic))
        stage = "evaluating"
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=3)
                while not stages.empty():
                    stage = stages.get_nowait()
                if not task.done():
                    yield sse("status", {"stage": stage})
            events = await task
            # All result events are emitted only after the Controller transaction commits.
            for event, data in events:
                yield sse(event, data)
        except Exception as exc:
            logger.warning("M4 answer failed: %s", getattr(exc, "code", type(exc).__name__))
            # A deterministic contract mismatch is never worth another model call:
            # the request would fail identically after paying for it again.
            from interview_api.modules.interview.context_projection import TERMINAL_CODES
            terminal = isinstance(exc, AppError) and (
                exc.status_code == 404 or exc.code in TERMINAL_CODES)
            yield sse("error", {"code": getattr(exc, "code", "INTERVIEW_MODEL_FAILED"),
                "message": exc.message if isinstance(exc, AppError) else "模型处理失败，未评分或推进面试；请使用同一回答重试",
                "retryable": not terminal and getattr(exc, "code", "") != "INTERVIEW_END_INTENT",
                "requires_confirmation": getattr(exc, "code", "") in ("INTERVIEW_OUTCOME_UNKNOWN", "INTERVIEW_END_INTENT"),
                "end_intent": getattr(exc, "end_intent", None),
                "idempotency_key": request.idempotency_key})
        finally:
            if not task.done():
                task.cancel()
            with anyio.CancelScope(shield=True):
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def skip(self, session_id, user_id, question_id):
        session, context, questions, active, tokens = await self.read_state(session_id, user_id)
        if not active or active.id != question_id or active.status != "ASKED":
            raise AppError("INTERVIEW_CONFLICT", "只能跳过当前未回答题目", 409)
        action = SessionController.choose_action(context, questions, active, "NEXT", rounds=session.turn_count + 1,
            tokens=tokens, started_at=session.started_at)
        policy = session_policy(context, questions)
        result = None
        if action != "COMPLETE":
            if context.get("adaptive_policy"):
                from interview_api.modules.interview.adaptive import planning_payload, validate_decision
                payload = planning_payload(context, questions, active, session.interview_plan_json)
                payload["adaptive"]["choices"] = [c for c in payload["adaptive"]["choices"] if "NEXT" in c["strategies"]]
                if context["adaptive_policy"].get("main_harness_version"):
                    payload["adaptive"]["candidates"] = [c for c in payload["adaptive"]["candidates"]
                        if "NEXT" in c["strategies"]]
                result = await self.call_agent(self.main.adaptive(payload), session_id, user_id, session.state_version, payload)
                if result:
                    policy = validate_decision(result.artifact, payload)
            else:
                result = await self.call_agent(self.main, session_id, user_id, session.state_version,
                                              self.payload(context, questions, active, "NEXT", policy))
        async with self.factory() as db:
            data = await SessionController(db).skip(session_id, user_id, question_id, session.state_version, result, policy)
            await db.commit()
            return data

    async def detail(self, session_id, user_id):
        async with self.factory() as db:
            session = await db.scalar(select(InterviewSession).where(InterviewSession.id == session_id, InterviewSession.user_id == user_id))
            if not session:
                raise AppError("INTERVIEW_NOT_FOUND", "面试会话不存在", 404)
            result = {column.name: getattr(session, column.name) for column in InterviewSession.__table__.columns}
            result["legacy"] = session.workflow_mode != "m4"
            messages = (await db.scalars(select(InterviewMessage).where(InterviewMessage.session_id == session_id).order_by(InterviewMessage.turn_index, InterviewMessage.id))).all()
            questions = (await db.scalars(select(InterviewSessionQuestion).where(InterviewSessionQuestion.session_id == session_id).order_by(InterviewSessionQuestion.question_index))).all()
            result["messages"] = [{c.name: getattr(m, c.name) for c in InterviewMessage.__table__.columns} for m in messages]
            result["questions"] = [question_data(q, session.question_count) for q in questions]
            result["total_questions"] = len(questions)
            result["topic_coverage"] = coverage(questions, session.question_count)
            result["current_question"] = next((q for q in result["questions"] if q["question_id"] == session.active_question_id), None)
            pending = await db.scalar(select(InterviewAnswer).where(InterviewAnswer.session_id == session_id,
                InterviewAnswer.session_question_id == session.active_question_id, InterviewAnswer.status != "COMPLETED"))
            result["pending_answer"] = None
            if pending:
                message = await db.get(InterviewMessage, pending.message_id)
                result["pending_answer"] = dict(question_id=pending.session_question_id, idempotency_key=pending.idempotency_key,
                    content=message.content, status=pending.status, error=pending.error)
                processing = (session.interview_plan_json or {}).get("answer_processing") or {}
                expires = processing.get("expires_at")
                deadline = datetime.fromisoformat(expires) if expires else pending.created_at
                if not expires:
                    from datetime import timedelta
                    deadline += timedelta(seconds=2 * settings.interview_model_timeout_seconds + 30)
                recovery = (session.interview_plan_json or {}).get("answer_recovery") or {}
                reusable = recovery.get("answer_id") == pending.id and bool(recovery.get("artifact"))
                diagnostic = recovery.get("diagnostic") if reusable else None
                stage = recovery.get("stage") if reusable else None
                if not reusable:
                    # Old sessions failed before checkpoints existed. Read the
                    # model ledger only: an already-paid evaluation is still
                    # reusable, and the UI must not call it "awaiting grading".
                    from interview_api.modules.experience.models import ExperienceAgentRun
                    from interview_api.modules.interview.models import InterviewAgentCall
                    runs = (await db.scalars(select(ExperienceAgentRun).join(InterviewAgentCall,
                        InterviewAgentCall.agent_run_id == ExperienceAgentRun.id).where(
                        InterviewAgentCall.answer_id == pending.id).order_by(ExperienceAgentRun.id))).all()
                    for run in runs:
                        output = run.output_json or {}
                        if run.status == "SUCCEEDED" and output.get("schema") == "AdaptiveEvaluation":
                            reusable = True
                            stage = "EVALUATION_AVAILABLE_IN_LEDGER"
                        if run.status == "FAILED" and output.get("validation", {}).get("error") == "ADAPTIVE_TOPIC_MISMATCH":
                            stage = "NEXT_REJECTED"
                            diagnostic = output["validation"]
                result["pending_answer"].update(processing_stage=processing.get("stage", "evaluating"),
                    retryable=pending.status != "RUNNING" or deadline <= datetime.now(timezone.utc),
                    processing_deadline=deadline.isoformat(),
                    recovery_stage=stage, evaluation_reusable=bool(reusable),
                    recovery_diagnostic=diagnostic)
                if (message.metadata_json or {}).get("design_revision"):
                    result["pending_answer"]["design_plan"] = message.metadata_json["design_revision"]["plan"]
            if session.context_snapshot_id:
                snapshot = await db.get(InterviewContextSnapshot, session.context_snapshot_id)
                result["context_snapshot"] = snapshot.context_json
                result["snapshot_fingerprint"] = snapshot.content_fingerprint
            return result

    async def design_tool_evidence(self, sid, uid, aid, expected, revision, context, questions, active, content):
        from interview_api.modules.interview import system_design as design
        session, _, _, _, _ = await self.read_state(sid, uid)
        for item in (session.planner_trace_json or {}).get("design_tools", []):
            if item["answer_id"] == aid and item["plan_fingerprint"] == revision["fingerprint"] and item["status"] == "SUCCEEDED":
                return item
        if len((session.planner_trace_json or {}).get("design_tools", [])) >= context["system_design"]["max_tool_calls"]:
            raise AppError("DESIGN_TOOL_BUDGET", "工具调用额度已耗尽，回答保留且不推进", 409)
        async with self.factory() as db:
            await SessionController(db).answer_stage(sid, uid, aid, expected, "validating_design")
            await db.commit()
        payload = {**self.payload(context, questions, active, "NEXT", {}, answer=content),
            "design_phase": "select_tool", "design_revision": revision,
            "previous_tool_evidence": (session.planner_trace_json or {}).get("design_tools", [])}
        decision = await self.call_agent(self.main.tool_selector(), sid, uid, expected, payload, answer_id=aid)
        if decision is None: raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "未取得工具决策，未推进", 409)
        async with self.factory() as db:
            item = await SessionController(db).reserve_design_tool(sid, uid, aid, expected, revision, decision.artifact, decision.run_id)
            await db.commit()
        try:
            result = await asyncio.wait_for(design.execute_tool(item["tool"], item["arguments"], revision["plan"]),
                                            timeout=context["system_design"]["tool_timeout_seconds"])
            async with self.factory() as db:
                completed = await SessionController(db).finish_design_tool(sid, uid, expected, item["id"], result=result)
                await db.commit()
            return completed
        except BaseException as exc:
            with anyio.CancelScope(shield=True):
                async with self.factory() as db:
                    try:
                        await SessionController(db).finish_design_tool(sid, uid, expected, item["id"], error="DESIGN_TOOL_TIMEOUT" if isinstance(exc, TimeoutError) else type(exc).__name__)
                        await db.commit()
                    except AppError:
                        await db.rollback()  # stale requests cannot overwrite current state
            raise
