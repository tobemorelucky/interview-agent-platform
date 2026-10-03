"""The sole M4 business-state writer. Model calls never run inside its transactions."""
from datetime import datetime, timezone
from copy import deepcopy
from sqlalchemy import select, func
from interview_api.core.config import settings

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.models import (
    InterviewSession, InterviewSessionQuestion, InterviewMessage, InterviewContextSnapshot,
    InterviewAnswer, InterviewEvaluation, InterviewAgentCall,
)
from interview_api.modules.experience.models import ExperienceAgentStepRun
from interview_api.modules.interview.artifacts import DIMENSIONS
from interview_api.modules.interview.context import build_context, question_policy, validate_question


def conflict(message="会话或题目已变化，请刷新后重试"):
    return AppError("INTERVIEW_CONFLICT", message, 409)


def question_data(q, budget):
    return dict(question_id=q.id, question_index=q.question_index, total_questions=budget,
                question=q.question, dimension=q.dimension, difficulty=q.difficulty,
                source=q.source, source_type=q.source, source_question_id=q.source_question_id,
                job_requirement_id=q.job_requirement_id, source_fingerprint=q.source_fingerprint,
                evidence=q.candidate_refs_json, parent_question_id=q.parent_question_id,
                question_kind=q.question_kind, status=q.status, is_dynamic=q.is_dynamic)


class SessionController:
    def __init__(self, db):
        self.db = db

    async def owned(self, session_id, user_id, *, lock=False):
        query = select(InterviewSession).where(InterviewSession.id == session_id, InterviewSession.user_id == user_id)
        if lock:
            query = query.with_for_update()
        session = await self.db.scalar(query)
        if not session:
            raise AppError("INTERVIEW_NOT_FOUND", "面试会话不存在", 404)
        if session.workflow_mode != "m4":
            raise AppError("LEGACY_READ_ONLY", "历史面试只读，请新建 M4 面试", 409)
        return session

    async def create(self, user_id, request):
        context, resume_id, fingerprint = await build_context(self.db, user_id, request)
        session = InterviewSession(user_id=user_id, resume_id=resume_id, title=request.title or f"{context['company_name']} · {context['job_title']}",
            workflow_mode="m4", status="CREATED", target_position=context["job_title"], target_position_confirmed=True,
            question_count=request.question_count, question_generation_status="PENDING")
        self.db.add(session)
        await self.db.flush()
        snapshot = InterviewContextSnapshot(user_id=user_id, interview_session_id=session.id,
            candidate_profile_version_id=context["candidate_profile_version_id"], company_id=context["company_id"],
            job_id=context["job_id"], job_description_version_id=context["job_description_version_id"],
            context_json=context, content_fingerprint=fingerprint)
        self.db.add(snapshot)
        await self.db.flush()
        session.context_snapshot_id = snapshot.id
        session.status, session.question_generation_status = "READY", "READY"
        await self.db.flush()
        return session

    async def state(self, session_id, user_id):
        session = await self.owned(session_id, user_id)
        snapshot = await self.db.get(InterviewContextSnapshot, session.context_snapshot_id)
        questions = list((await self.db.scalars(select(InterviewSessionQuestion).where(
            InterviewSessionQuestion.session_id == session_id).order_by(InterviewSessionQuestion.question_index, InterviewSessionQuestion.id))).all())
        active = next((q for q in questions if q.id == session.active_question_id), None)
        usage = (await self.db.scalars(select(ExperienceAgentStepRun.token_usage_json).join(
            InterviewAgentCall, InterviewAgentCall.agent_run_id == ExperienceAgentStepRun.agent_run_id)
            .where(InterviewAgentCall.session_id == session_id))).all()
        tokens = sum((u or {}).get("total_tokens") or 0 for u in usage)
        tokens = max(tokens, (session.interview_plan_json or {}).get("reserved_model_tokens", 0))
        return session, deepcopy(snapshot.context_json), questions, active, tokens

    async def reserve_model_call(self, session_id, user_id, expected_version, reservation):
        """Conservative, non-refundable call allowance also bounds failed retries."""
        session = await self.owned(session_id, user_id, lock=True)
        if session.state_version != expected_version:
            raise conflict()
        _, context, _, _, used = await self.state(session_id, user_id)
        remaining_time = context["budgets"]["max_duration_seconds"]
        if session.started_at:
            remaining_time -= (datetime.now(timezone.utc) - session.started_at).total_seconds()
        if remaining_time <= 0 or used >= context["budgets"]["max_model_tokens"] or used + reservation > context["budgets"]["max_model_tokens"]:
            session.interview_plan_json = {**(session.interview_plan_json or {}), "budget_exhausted": True}
            return None
        session.interview_plan_json = {**(session.interview_plan_json or {}), "reserved_model_tokens": used + reservation, "budget_exhausted": False}
        return min(settings.interview_model_timeout_seconds, remaining_time)

    async def budget_complete(self, session_id, user_id, answer_id, expected_version):
        session = await self.owned(session_id, user_id, lock=True)
        if session.state_version != expected_version or not (session.interview_plan_json or {}).get("budget_exhausted"):
            raise conflict()
        answer = await self.db.get(InterviewAnswer, answer_id)
        if session.active_question_id != answer.session_question_id or answer.status != "RUNNING":
            raise conflict()
        q = await self.db.get(InterviewSessionQuestion, answer.session_question_id)
        q.status = "SKIPPED"
        summary = await self.complete(session)
        summary["termination_reason"] = "MODEL_OR_TIME_BUDGET"
        session.completion_json = summary
        await self._message(session, "INTERVIEW_COMPLETE", "预算已耗尽，面试结束；未生成虚假评分", summary)
        session.state_version += 1
        events = [["complete", summary], ["done", {"message_id": answer.message_id, "action": "COMPLETE", "turn_index": session.turn_count}]]
        answer.status, answer.result_events = "COMPLETED", events
        return events

    @staticmethod
    def adjusted_difficulty(active, evaluation):
        levels = ["EASY", "MEDIUM", "HARD"]
        current = levels.index(active.difficulty) if active.difficulty in levels else 1
        average = sum(evaluation[key] for key in DIMENSIONS) / len(DIMENSIONS)
        shift = 1 if average >= 4 else -1 if average <= 2 else 0
        return levels[max(0, min(2, current + shift))]

    @staticmethod
    def choose_action(context, questions, active, recommendation, *, rounds, tokens, started_at):
        budgets = context["budgets"]
        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds() if started_at else 0
        if len(questions) >= budgets["question_count"] or rounds >= budgets["max_rounds"] or tokens >= budgets["max_model_tokens"] or elapsed >= budgets["max_duration_seconds"]:
            return "COMPLETE"
        root = active
        by_id = {q.id: q for q in questions}
        while root and root.parent_question_id:
            root = by_id[root.parent_question_id]
        descendants = [q for q in questions if q.question_kind in ("FOLLOW_UP", "DEEP_DIVE")]
        def belongs(q):
            while q.parent_question_id:
                q = by_id[q.parent_question_id]
            return root and q.id == root.id
        count = sum(bool(belongs(q)) for q in descendants)
        if recommendation in ("FOLLOW_UP", "DEEP_DIVE"):
            return recommendation if count < budgets["max_follow_ups"] else "NEXT"
        # An agent cannot end an under-budget interview just by recommending COMPLETE.
        return "ADJUST_DIFFICULTY" if recommendation == "ADJUST_DIFFICULTY" else "NEXT"

    async def _new_question(self, session, context, questions, artifact, policy, *, kind="NEXT", parent=None):
        validate_question(artifact, context, policy)
        public = next((q for q in context["public_questions"] if q["id"] == artifact.source_question_id), None)
        evidence = [e for e in context["candidate_evidence"] if e["key"] in artifact.candidate_evidence_ids]
        fact_ids = list(artifact.candidate_fact_ids)
        evidence_ids = list(artifact.candidate_evidence_ids)
        if parent:
            inherited = parent.candidate_refs_json or {}
            fact_ids = list(dict.fromkeys(fact_ids + inherited.get("fact_ids", [])))
            evidence_ids = list(dict.fromkeys(evidence_ids + inherited.get("evidence_ids", [])))
            evidence = [e for e in context["candidate_evidence"] if e["key"] in evidence_ids]
        q = InterviewSessionQuestion(session_id=session.id, question_index=max((q.question_index for q in questions), default=-1) + 1,
            question=artifact.question, standard_answer="\n".join(artifact.expected_points),
            dimension=artifact.dimension, difficulty=artifact.difficulty, source=artifact.source_type,
            source_question_id=artifact.source_question_id, job_requirement_id=artifact.job_requirement_id,
            source_fingerprint=public["fingerprint"] if public else None,
            candidate_refs_json=dict(fact_ids=fact_ids, evidence_ids=evidence_ids, evidence=evidence,
                project_ids=[p["id"] for p in context["normalized"]["projects"] if p["fact_id"] in fact_ids]),
            parent_question_id=parent.id if parent else None, is_dynamic=kind == "DEEP_DIVE" or artifact.source_type == "DYNAMIC_GENERATED",
            question_kind=kind, status="PENDING")
        self.db.add(q)
        await self.db.flush()
        q.status = "ASKED"
        session.active_question_id, session.current_question_index = q.id, q.question_index
        await self.db.flush()
        return q

    async def start(self, session_id, user_id, expected_version, result, policy):
        session = await self.owned(session_id, user_id, lock=True)
        if session.state_version != expected_version or session.status != "READY":
            raise conflict()
        snapshot = await self.db.get(InterviewContextSnapshot, session.context_snapshot_id)
        artifact = result.artifact
        if artifact.recommendation != "NEXT" or not artifact.question:
            raise AppError("INVALID_START_ARTIFACT", "首题输出无效", 422)
        q = await self._new_question(session, snapshot.context_json, [], artifact.question, policy)
        session.status, session.started_at = "IN_PROGRESS", datetime.now(timezone.utc)
        session.state_version += 1
        data = question_data(q, session.question_count)
        data["mode"] = "replay" if result.provider == "replay" else "live"
        await self._message(session, "QUESTION", q.question, data)
        return data

    async def prepare_answer(self, session_id, user_id, question_id, key, content):
        session = await self.owned(session_id, user_id, lock=True)
        answer = await self.db.scalar(select(InterviewAnswer).where(InterviewAnswer.session_id == session_id, InterviewAnswer.idempotency_key == key))
        if answer:
            message = await self.db.get(InterviewMessage, answer.message_id)
            if answer.session_question_id != question_id or message.content != content:
                raise conflict("幂等键已用于其他题目或回答")
            if answer.status == "COMPLETED":
                return answer, session.state_version, True
        if session.status != "IN_PROGRESS" or session.active_question_id != question_id:
            raise conflict()
        q = await self.db.get(InterviewSessionQuestion, question_id)
        if not answer:
            if q.status != "ASKED":
                raise conflict("该题已有回答，请使用原幂等键重试")
            session.turn_count += 1
            msg = InterviewMessage(session_id=session_id, role="USER", content=content,
                metadata_json={"question_id": question_id, "idempotency_key": key, "display_order": 0}, turn_index=session.turn_count)
            self.db.add(msg)
            await self.db.flush()
            answer = InterviewAnswer(session_id=session_id, session_question_id=question_id,
                message_id=msg.id, idempotency_key=key, status="RUNNING")
            self.db.add(answer)
            q.status = "ANSWERED"
        answer.status, answer.error = "RUNNING", None
        session.state_version += 1
        await self.db.flush()
        return answer, session.state_version, False

    async def failed_answer(self, session_id, user_id, answer_id, expected_version, error):
        session = await self.owned(session_id, user_id, lock=True)
        if session.state_version == expected_version:
            answer = await self.db.get(InterviewAnswer, answer_id)
            if answer.status != "COMPLETED":
                answer.status, answer.error = "FAILED", error[:500]
                # ANSWERED is retained. Never mark EVALUATED or advance on model failure.

    async def commit_answer(self, session_id, user_id, answer_id, expected_version, evaluation, main, requested_action, policy):
        session = await self.owned(session_id, user_id, lock=True)
        answer = await self.db.get(InterviewAnswer, answer_id)
        if session.state_version != expected_version or session.active_question_id != answer.session_question_id or answer.status != "RUNNING":
            raise conflict()
        _, context, questions, q, tokens = await self.state(session_id, user_id)
        artifact = evaluation.artifact
        action = self.choose_action(context, questions, q, artifact.next_action_recommendation,
            rounds=session.turn_count, tokens=tokens, started_at=session.started_at)
        if (session.interview_plan_json or {}).get("budget_exhausted"):
            action = "COMPLETE"
        if action != "COMPLETE" and action != requested_action:
            raise conflict("预算或会话条件已变化，请重试")
        if action != "COMPLETE" and (not main or main.artifact.recommendation != action or not main.artifact.question):
            raise AppError("INVALID_MAIN_ARTIFACT", "面试主 Agent 输出与 Controller 动作不一致", 422)
        if action == "ADJUST_DIFFICULTY" and main.artifact.question.difficulty != self.adjusted_difficulty(q, artifact.model_dump()):
            raise AppError("DIFFICULTY_POLICY", "难度调整未遵循本轮评价及有限等级", 422)
        ev = InterviewEvaluation(session_id=session_id, session_question_id=q.id, answer_id=answer.id,
            artifact_json=artifact.model_dump(), **{key: getattr(artifact, key) for key in DIMENSIONS},
            provider=evaluation.provider, model=evaluation.model, prompt_version=evaluation.prompt_version,
            structured_retry=evaluation.retry, token_usage_json=evaluation.usage, agent_run_id=evaluation.run_id)
        self.db.add(ev)
        await self.db.flush()
        q.status = "EVALUATED"
        score = round(sum(getattr(artifact, key) for key in DIMENSIONS) / len(DIMENSIONS), 2)
        eval_data = dict(artifact.model_dump(), score=score, question_id=q.id, answer_id=answer.id,
                         evaluation_id=ev.id, action=action, mode="replay" if evaluation.provider == "replay" else "live")
        events = [["evaluation", eval_data]]
        await self._message(session, "EVALUATION", artifact.evaluation, eval_data)
        q.status = "COMPLETED"
        if action == "COMPLETE":
            summary = await self.complete(session)
            events.append(["complete", summary])
            await self._message(session, "INTERVIEW_COMPLETE", "面试结束", summary)
        else:
            parent = q if action in ("FOLLOW_UP", "DEEP_DIVE") else None
            next_q = await self._new_question(session, context, questions, main.artifact.question, policy, kind=action, parent=parent)
            if parent:
                parent.follow_up_count += 1
            data = question_data(next_q, session.question_count)
            data["mode"] = "replay" if main.provider == "replay" else "live"
            if action == "FOLLOW_UP":
                data.update(follow_up_count=sum(x.question_kind == "FOLLOW_UP" for x in questions) + 1,
                            max_follow_ups=context["budgets"]["max_follow_ups"])
            events.append(["follow_up" if action == "FOLLOW_UP" else "question", data])
            await self._message(session, "FOLLOW_UP" if action == "FOLLOW_UP" else "QUESTION", next_q.question, data)
        session.state_version += 1
        events.append(["done", dict(message_id=answer.message_id, turn_index=session.turn_count, action=action, state_version=session.state_version)])
        answer.status, answer.result_events = "COMPLETED", events
        await self.db.flush()
        return events

    async def complete(self, session):
        # Called only after deterministic budget exhaustion (or explicit user skip reaches it).
        rows = list((await self.db.scalars(select(InterviewEvaluation).where(InterviewEvaluation.session_id == session.id))).all())
        questions = list((await self.db.scalars(select(InterviewSessionQuestion).where(InterviewSessionQuestion.session_id == session.id))).all())
        for q in questions:
            if q.status in ("PENDING", "ASKED"):
                q.status = "SKIPPED"
        averages = {key: round(sum(getattr(e, key) for e in rows) / len(rows), 4) if rows else None for key in DIMENSIONS}
        providers = set((await self.db.scalars(select(ExperienceAgentStepRun.provider_name).join(
            InterviewAgentCall, InterviewAgentCall.agent_run_id == ExperienceAgentStepRun.agent_run_id)
            .where(InterviewAgentCall.session_id == session.id))).all())
        summary = dict(answered_count=len({e.session_question_id for e in rows}), evaluated_count=len(rows),
            skipped_count=sum(q.status == "SKIPPED" for q in questions),
            follow_up_count=sum(q.question_kind == "FOLLOW_UP" for q in questions),
            dynamic_question_count=sum(q.is_dynamic for q in questions),
            dimension_averages=averages, overall_average=round(sum(sum(getattr(e, key) for key in DIMENSIONS) for e in rows) / (len(rows) * len(DIMENSIONS)), 4) if rows else None,
            total_questions=len(questions), question_budget=session.question_count,
            mode="replay" if providers == {"replay"} else "live" if providers and "replay" not in providers else "mixed" if providers else "not_run")
        summary["avg_score"] = summary["overall_average"]
        session.status, session.active_question_id = "COMPLETED", None
        session.completed_at, session.completion_json = datetime.now(timezone.utc), summary
        return summary

    async def skip(self, session_id, user_id, question_id, expected_version, main, policy):
        session = await self.owned(session_id, user_id, lock=True)
        if session.state_version != expected_version or session.status != "IN_PROGRESS" or session.active_question_id != question_id:
            raise conflict()
        _, context, questions, q, tokens = await self.state(session_id, user_id)
        if q.status != "ASKED":
            raise conflict("已提交回答的题目不能跳过")
        q.status = "SKIPPED"
        session.turn_count += 1
        action = self.choose_action(context, questions, q, "NEXT", rounds=session.turn_count, tokens=tokens, started_at=session.started_at)
        if (session.interview_plan_json or {}).get("budget_exhausted"):
            action = "COMPLETE"
        if action == "COMPLETE":
            result = dict(skipped_question_id=q.id, next_question=None, completion=await self.complete(session))
            await self._message(session, "INTERVIEW_COMPLETE", "面试结束", result["completion"])
        else:
            if not main or main.artifact.recommendation != "NEXT" or not main.artifact.question:
                raise AppError("INVALID_MAIN_ARTIFACT", "下一题无效", 422)
            next_q = await self._new_question(session, context, questions, main.artifact.question, policy)
            data = question_data(next_q, session.question_count)
            await self._message(session, "QUESTION", next_q.question, data)
            result = dict(skipped_question_id=q.id, next_question=data)
        session.state_version += 1
        return result

    async def _message(self, session, kind, content, data):
        self.db.add(InterviewMessage(session_id=session.id, role="ASSISTANT", content=content,
            metadata_json={**data, "type": kind, "source": data.get("source", "M4"),
                           "display_order": 1 if kind == "EVALUATION" else 2}, turn_index=session.turn_count))
