"""M4 orchestration: read/close -> model -> short Controller transaction -> events."""
import json
import logging
from functools import cached_property
from interview_api.core.config import settings
from sqlalchemy import select

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import build_interview_router
from interview_api.modules.interview.agents import InterviewMainAgent, AnswerEvaluationAgent
from interview_api.modules.interview.controller import SessionController, question_data
from interview_api.modules.interview.context import question_policy
from interview_api.modules.interview.models import InterviewSession, InterviewMessage, InterviewSessionQuestion, InterviewAnswer, InterviewContextSnapshot, InterviewEvaluation

logger = logging.getLogger(__name__)


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
        async with self.factory() as db:
            session = await SessionController(db).create(user_id, request)
            await db.commit()
            session_id = session.id
        return await self.detail(session_id, user_id)

    async def read_state(self, session_id, user_id):
        async with self.factory() as db:
            return await SessionController(db).state(session_id, user_id)

    async def call_agent(self, agent, session_id, user_id, expected, payload, *, answer_id=None):
        # Replay consumes zero model tokens. Live reserves conservative UTF-8 input
        # units + output allowance for initial generation and the one repair.
        provider = self.router.resolve(agent.route)
        reservation = 0 if provider.provider_name == "replay" else 2 * (
            len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) +
            len(json.dumps(agent.schema.model_json_schema()).encode("utf-8")) +
            settings.interview_model_max_output_tokens + 2048)
        async with self.factory() as db:
            timeout = await SessionController(db).reserve_model_call(session_id, user_id, expected, reservation)
            await db.commit()
        if timeout is None:
            return None
        return await agent.call(session_id, payload, answer_id=answer_id, timeout_seconds=timeout)

    @staticmethod
    def payload(context, questions, active, action, policy, *, answer=None, evaluation=None):
        desired = None
        if action == "ADJUST_DIFFICULTY" and active and evaluation:
            desired = SessionController.adjusted_difficulty(active, evaluation)
        return dict(snapshot=context, requested_action=action, preferred_source=policy, desired_difficulty=desired,
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
        policy = question_policy(context, questions)
        result = await self.call_agent(self.main, session_id, user_id, session.state_version,
                                      self.payload(context, questions, None, "NEXT", policy))
        if result is None:
            raise AppError("INTERVIEW_BUDGET_EXHAUSTED", "模型调用预算不足，未生成题目", 409)
        async with self.factory() as db:
            data = await SessionController(db).start(session_id, user_id, session.state_version, result, policy)
            await db.commit()
        return data

    async def answer(self, session_id, user_id, question_id, key, content):
        # Retrying the same key resumes one persisted Answer, not a new user message.
        async with self.factory() as db:
            answer, expected, reused = await SessionController(db).prepare_answer(session_id, user_id, question_id, key, content)
            await db.commit()
            answer_id, old_events = answer.id, answer.result_events
        if reused:
            return old_events
        try:
            session, context, questions, active, _ = await self.read_state(session_id, user_id)
            eval_result = await self.call_agent(self.evaluator, session_id, user_id, expected,
                self.payload(context, questions, active, "NEXT", {}, answer=content), answer_id=answer_id)
            if eval_result is None:
                async with self.factory() as db:
                    events = await SessionController(db).budget_complete(session_id, user_id, answer_id, expected)
                    await db.commit()
                return events
            # Audit token usage is included before choosing the next deterministic action.
            session, context, questions, active, tokens = await self.read_state(session_id, user_id)
            action = SessionController.choose_action(context, questions, active, eval_result.artifact.next_action_recommendation,
                rounds=session.turn_count, tokens=tokens, started_at=session.started_at)
            policy = question_policy(context, questions, action)
            main_result = None
            if action != "COMPLETE":
                main_result = await self.call_agent(self.main, session_id, user_id, expected,
                    self.payload(context, questions, active, action, policy, answer=content,
                                 evaluation=eval_result.artifact.model_dump()), answer_id=answer_id)
            async with self.factory() as db:
                events = await SessionController(db).commit_answer(session_id, user_id, answer_id, expected,
                    eval_result, main_result, action, policy)
                await db.commit()
            return events
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            async with self.factory() as db:
                await SessionController(db).failed_answer(session_id, user_id, answer_id, expected, code)
                await db.commit()
            raise

    async def stream(self, session_id, user_id, request):
        yield sse("status", {"stage": "evaluating"})
        try:
            events = await self.answer(session_id, user_id, request.question_id, request.idempotency_key, request.content)
            # All result events are emitted only after the Controller transaction commits.
            for event, data in events:
                yield sse(event, data)
        except Exception as exc:
            logger.warning("M4 answer failed: %s", getattr(exc, "code", type(exc).__name__))
            yield sse("error", {"code": getattr(exc, "code", "INTERVIEW_MODEL_FAILED"),
                "message": exc.message if isinstance(exc, AppError) else "模型处理失败，未评分或推进面试；请使用同一回答重试",
                "retryable": not isinstance(exc, AppError) or exc.status_code != 404,
                "idempotency_key": request.idempotency_key})

    async def skip(self, session_id, user_id, question_id):
        session, context, questions, active, tokens = await self.read_state(session_id, user_id)
        if not active or active.id != question_id or active.status != "ASKED":
            raise AppError("INTERVIEW_CONFLICT", "只能跳过当前未回答题目", 409)
        action = SessionController.choose_action(context, questions, active, "NEXT", rounds=session.turn_count + 1,
            tokens=tokens, started_at=session.started_at)
        policy = question_policy(context, questions)
        result = None
        if action != "COMPLETE":
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
            result["current_question"] = next((q for q in result["questions"] if q["question_id"] == session.active_question_id), None)
            pending = await db.scalar(select(InterviewAnswer).where(InterviewAnswer.session_id == session_id,
                InterviewAnswer.session_question_id == session.active_question_id, InterviewAnswer.status != "COMPLETED"))
            result["pending_answer"] = None
            if pending:
                message = await db.get(InterviewMessage, pending.message_id)
                result["pending_answer"] = dict(question_id=pending.session_question_id, idempotency_key=pending.idempotency_key,
                    content=message.content, status=pending.status, error=pending.error)
            if session.context_snapshot_id:
                snapshot = await db.get(InterviewContextSnapshot, session.context_snapshot_id)
                result["context_snapshot"] = snapshot.context_json
                result["snapshot_fingerprint"] = snapshot.content_fingerprint
            return result
