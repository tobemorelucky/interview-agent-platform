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
from interview_api.modules.interview.topics import coverage
from interview_api.modules.interview.models import InterviewSession, InterviewMessage, InterviewSessionQuestion, InterviewAnswer, InterviewContextSnapshot, InterviewEvaluation

logger = logging.getLogger(__name__)


def session_policy(context, questions, action="NEXT"):
    if context.get("system_design"):
        from interview_api.modules.interview.system_design import design_policy
        return design_policy(context, questions, action)
    return question_policy(context, questions, action)


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
        if request.training_mode and request.training_request_key:
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
        reservation = 0 if provider.provider_name == "replay" else (
            len(json.dumps(admission_payload, ensure_ascii=False).encode("utf-8")) + system_bytes +
            len(json.dumps(agent.schema.model_json_schema()).encode("utf-8")) +
            2 * (settings.interview_model_max_output_tokens + 2048))
        call_key = uuid4().hex
        async with self.factory() as db:
            timeout = await SessionController(db).reserve_model_call(session_id, user_id, expected, reservation, call_key)
            await db.commit()
        if timeout is None:
            return None
        usage_reported = provider.provider_name == "replay"
        known_tokens = 0
        try:
            result = await agent.call(session_id, payload, answer_id=answer_id, timeout_seconds=timeout)
            known_tokens = result.usage.get('total_tokens') or 0
            usage_reported = usage_reported or (type(result.usage.get('total_tokens')) is int and result.usage_complete)
            return result
        except BaseException as exc:
            total = (getattr(exc, 'usage', {}) or {}).get('total_tokens')
            audited = getattr(exc, 'usage_audited', True)
            known_tokens = total if audited and type(total) is int and total >= 0 else 0
            usage_reported = usage_reported or (audited and type(total) is int and total >= 0 and getattr(exc, 'usage_complete', True))
            raise
        finally:
            with anyio.CancelScope(shield=True):
                async with self.factory() as db:
                    await SessionController(db).settle_model_call(session_id, user_id, call_key, usage_reported, known_tokens=known_tokens)
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

    async def start(self, session_id, user_id):
        session, context, questions, active, _ = await self.read_state(session_id, user_id)
        if session.status == "IN_PROGRESS" and active:
            return question_data(active, session.question_count)
        if session.status != "READY":
            raise AppError("INTERVIEW_NOT_READY", "面试无法开始", 409)
        if context.get("adaptive_policy"):
            from interview_api.modules.interview.adaptive import planning_payload, validate_decision
            payload = planning_payload(context, questions, None, session.interview_plan_json)
            result = await self.call_agent(self.main.adaptive(payload), session_id, user_id, session.state_version, payload)
            if result is None:
                raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "首题预算不足", 409)
            policy = validate_decision(result.artifact, payload)
            try:
                async with self.factory() as db:
                    data = await SessionController(db).start(session_id, user_id, session.state_version, result, policy)
                    await db.commit()
            except AppError as exc:
                if context.get('training'):
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

    async def answer(self, session_id, user_id, question_id, key, content, on_stage=None, design_plan=None):
        _, initial_context, _, _, _ = await self.read_state(session_id, user_id)
        if bool(initial_context.get("system_design")) != (design_plan is not None):
            raise AppError("DESIGN_PLAN_REQUIRED", "系统设计面试须同时提交结构化方案；普通面试不接收方案", 422)
        # Retrying the same key resumes one persisted Answer, not a new user message.
        async with self.factory() as db:
            answer, expected, reused = await SessionController(db).prepare_answer(session_id, user_id, question_id, key, content)
            if design_plan is not None:
                revision = await SessionController(db).bind_design_plan(session_id, user_id, answer, expected, design_plan)
            await db.commit()
            answer_id, old_events = answer.id, answer.result_events
        if reused:
            return old_events
        try:
            session, context, questions, active, _ = await self.read_state(session_id, user_id)
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

    async def adaptive_answer(self, sid, uid, aid, expected, content, on_stage):
        from interview_api.modules.interview.adaptive import planning_payload, validate_decision, all_goals_demonstrated
        session, context, questions, active, _ = await self.read_state(sid, uid)
        payload = planning_payload(context, questions, active, session.interview_plan_json, answer=content, answer_id=aid)
        if on_stage: on_stage("generating_evaluation")
        evaluation = await self.call_agent(self.evaluator.adaptive(), sid, uid, expected, payload, answer_id=aid)
        if evaluation is None:
            raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "评价预算不足，回答已保存，未推进或结束", 409)
        session, context, questions, active, tokens = await self.read_state(sid, uid)
        payload = planning_payload(context, questions, active, session.interview_plan_json, answer=content,
            answer_id=aid, evaluation=evaluation.artifact, evaluation_run_id=evaluation.run_id)
        exhausted = SessionController.choose_action(context, questions, active, "NEXT", rounds=session.turn_count,
            tokens=tokens, started_at=session.started_at) == "COMPLETE"
        main, policy, action = None, {}, "COMPLETE"
        if not exhausted and not all_goals_demonstrated(context, payload["adaptive"]["knowledge_state"]) and payload["adaptive"]["choices"]:
            async with self.factory() as db:
                await SessionController(db).answer_stage(sid, uid, aid, expected, "generating_next_question")
                await db.commit()
            if on_stage: on_stage("generating_next_question")
            main = await self.call_agent(self.main.adaptive(payload), sid, uid, expected, payload, answer_id=aid)
            if main is None:
                raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "下一题预算不足，回答已保存，未推进", 409)
            policy = validate_decision(main.artifact, payload)
            action = main.artifact.recommendation
        try:
            async with self.factory() as db:
                events = await SessionController(db).commit_answer(sid, uid, aid, expected, evaluation, main, action, policy)
                await db.commit()
        except AppError as exc:
            if context.get('training') and main:
                from interview_api.modules.interview.training import controller_rejection
                await controller_rejection(self.factory,main.run_id,exc.code)
            raise
        return events

    async def stream(self, session_id, user_id, request):
        yield sse("status", {"stage": "evaluating"})
        stages = asyncio.Queue()
        task = asyncio.create_task(self.answer(session_id, user_id, request.question_id,
            request.idempotency_key, request.content, on_stage=stages.put_nowait,
            design_plan=request.design_plan.model_dump() if request.design_plan else None))
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
            yield sse("error", {"code": getattr(exc, "code", "INTERVIEW_MODEL_FAILED"),
                "message": exc.message if isinstance(exc, AppError) else "模型处理失败，未评分或推进面试；请使用同一回答重试",
                "retryable": not isinstance(exc, AppError) or exc.status_code != 404,
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
                result["pending_answer"].update(processing_stage=processing.get("stage", "evaluating"),
                    retryable=pending.status != "RUNNING" or deadline <= datetime.now(timezone.utc),
                    processing_deadline=deadline.isoformat())
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
