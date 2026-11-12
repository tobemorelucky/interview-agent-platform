"""The sole M4 business-state writer. Model calls never run inside its transactions."""
from datetime import datetime, timezone, timedelta
from copy import deepcopy
from sqlalchemy import select, func
from interview_api.core.config import settings

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.models import (
    InterviewSession, InterviewSessionQuestion, InterviewMessage, InterviewContextSnapshot,
    InterviewAnswer, InterviewEvaluation, InterviewAgentCall,
)
from interview_api.modules.experience.models import ExperienceAgentStepRun, ExperienceAgentRun
from interview_api.modules.interview.artifacts import DIMENSIONS
from interview_api.modules.interview.context import build_context, question_policy, validate_question
from interview_api.modules.interview.topics import coverage, classify, POLICY_VERSION, mentioned_topics, TOPICS
from interview_api.modules.interview.skill_engine import style_recommendation


def conflict(message="会话或题目已变化，请刷新后重试"):
    return AppError("INTERVIEW_CONFLICT", message, 409)


# Reasons the service may end an adaptive session when no next question fits.
# BUDGET_LIMIT is only accepted for rows/paths written before the split; new
# sessions always carry the specific stop reason below.
TERMINAL_REASONS = ("BUDGET_LIMIT", "TOKEN_LIMIT", "DURATION_LIMIT")
# Deterministic stop causes, in classification order. Finishing the planned
# question count is normal completion, so it wins over any budget also spent.
STOP_REASONS = ("QUESTION_COUNT_REACHED", "ROUND_LIMIT", "TOKEN_LIMIT", "DURATION_LIMIT")


def question_data(q, budget):
    result = dict(question_id=q.id, question_index=q.question_index, total_questions=budget,
                question=q.question, dimension=q.dimension, difficulty=q.difficulty,
                source=q.source, source_type=q.source, source_question_id=q.source_question_id,
                job_requirement_id=q.job_requirement_id, source_fingerprint=q.source_fingerprint,
                evidence=q.candidate_refs_json, parent_question_id=q.parent_question_id,
                question_kind=q.question_kind, status=q.status, is_dynamic=q.is_dynamic)
    guidance = (q.candidate_refs_json or {}).get("interviewer_guidance")
    if guidance:
        result["interviewer_guidance"] = guidance
    decision = (q.candidate_refs_json or {}).get("adaptive_decision")
    if decision:
        from interview_api.modules.interview.adaptive import LABELS
        result["assessment_topic"] = LABELS.get(decision["target_knowledge_id"].rsplit(":", 1)[-1], "技术实践")
    return result


class SessionController:
    def __init__(self, db):
        self.db = db

    async def bind_design_plan(self, *args):
        from interview_api.modules.interview.system_design import bind_plan
        return await bind_plan(self, *args)

    async def reserve_design_tool(self, *args):
        from interview_api.modules.interview.system_design import reserve_tool
        return await reserve_tool(self, *args)

    async def finish_design_tool(self, *args, **kwargs):
        from interview_api.modules.interview.system_design import finish_tool
        return await finish_tool(self, *args, **kwargs)

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
            job_target_id=(context.get("job_target") or {}).get("id"),
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
        plan = session.interview_plan_json or {}
        # Completed calls are charged from their audit usage, not a cumulative
        # UTF-8 reservation. Unreported failures remain conservatively charged.
        tokens += plan.get("unreported_model_tokens", 0)
        tokens += sum(plan.get("model_call_reservations", {}).values())
        return session, deepcopy(snapshot.context_json), questions, active, tokens

    async def reserve_model_call(self, session_id, user_id, expected_version, reservation, call_key="legacy_pending"):
        """Reserve only while a call is in flight; do not confuse units with usage."""
        session = await self.owned(session_id, user_id, lock=True)
        if session.state_version != expected_version:
            raise conflict()
        _, context, _, _, used = await self.state(session_id, user_id)
        remaining_time = context["budgets"]["max_duration_seconds"]
        if session.started_at:
            remaining_time -= (datetime.now(timezone.utc) - session.started_at).total_seconds()
        if remaining_time <= 0 or used >= context["budgets"]["max_model_tokens"] or used + reservation > context["budgets"]["max_model_tokens"]:
            # Record which limit refused this call. "Budget exhausted" alone would
            # report a duration stop as a token stop to the terminal reason.
            session.interview_plan_json = {**(session.interview_plan_json or {}), "budget_exhausted": True,
                "budget_exhausted_reason": "DURATION" if remaining_time <= 0 else "TOKENS"}
            return None
        plan = dict(session.interview_plan_json or {})
        plan.pop("budget_exhausted_reason", None)
        reservations = dict(plan.get("model_call_reservations", {}))
        reservations[call_key] = reservation
        session.interview_plan_json = {**plan, "model_call_reservations": reservations,
                                      "reserved_model_tokens": sum(reservations.values()), "budget_exhausted": False}
        return min(settings.interview_model_timeout_seconds, remaining_time)

    async def settle_model_call(self, session_id, user_id, call_key, usage_reported, *, known_tokens=0,
                                request_sent=None, answer_id=None):
        """Audit holds actual usage; unknown failure usage cannot create free retries."""
        session = await self.owned(session_id, user_id, lock=True)
        plan = dict(session.interview_plan_json or {})
        reservations = dict(plan.get("model_call_reservations", {}))
        reserved = reservations.pop(call_key, 0)
        unknown = dict(plan.get("unknown_model_calls") or {})
        if reserved > 0 and not usage_reported and request_sent is not False:
            # The call left this process without a durable usage receipt. Keep
            # it so a later request cannot silently resend the same operation.
            unknown[call_key] = {"answer_id": answer_id, "reserved_tokens": reserved,
                                 "recorded_at": datetime.now(timezone.utc).isoformat()}
            while len(unknown) > 20:
                unknown.pop(next(iter(unknown)))
        session.interview_plan_json = {**plan, "model_call_reservations": reservations,
            "reserved_model_tokens": sum(reservations.values()),
            "unknown_model_calls": unknown,
            # Known response usage is already in StepRun. Retain only the net
            # unreported reservation, never count that known portion twice.
            "unreported_model_tokens": plan.get("unreported_model_tokens", 0) + (0 if usage_reported else max(0, reserved - known_tokens))}

    @staticmethod
    def adjusted_difficulty(active, evaluation):
        levels = ["EASY", "MEDIUM", "HARD"]
        current = levels.index(active.difficulty) if active.difficulty in levels else 1
        average = sum(evaluation[key] for key in DIMENSIONS) / len(DIMENSIONS)
        shift = 1 if average >= 4 else -1 if average <= 2 else 0
        return levels[max(0, min(2, current + shift))]

    @staticmethod
    def follow_up_consumption(questions, active):
        by_id = {q.id: q for q in questions}
        def root_id(q):
            while q and q.parent_question_id:
                q = by_id.get(q.parent_question_id)
            return q.id if q else None
        root = root_id(active)
        return sum(q.question_kind in ("FOLLOW_UP", "DEEP_DIVE") and root_id(q) == root for q in questions)

    @staticmethod
    def stop_reason(context, questions, *, rounds, tokens, started_at):
        """The single deterministic stop test, and the reason it stopped.

        Order is the classification order: the planned question count is the
        normal end of an interview, so it is reported as completion rather than
        as an early budget stop even when a budget happens to be spent too.
        """
        budgets = context["budgets"]
        if len(questions) >= budgets["question_count"]:
            return "QUESTION_COUNT_REACHED"
        if rounds >= budgets["max_rounds"]:
            return "ROUND_LIMIT"
        if tokens >= budgets["max_model_tokens"]:
            return "TOKEN_LIMIT"
        if started_at:
            elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
            if elapsed >= budgets["max_duration_seconds"]:
                return "DURATION_LIMIT"
        return None

    @staticmethod
    def choose_action(context, questions, active, recommendation, *, rounds, tokens, started_at, evaluation=None):
        budgets = context["budgets"]
        if SessionController.stop_reason(context, questions, rounds=rounds, tokens=tokens, started_at=started_at):
            return "COMPLETE"
        recommendation = style_recommendation(context, recommendation, evaluation)
        count = SessionController.follow_up_consumption(questions, active)
        if recommendation in ("FOLLOW_UP", "DEEP_DIVE"):
            pacing = coverage(questions, budgets["question_count"])
            if pacing["recent_topic"] in pacing["blocked_topics"]:
                return "NEXT"
            return recommendation if count < budgets["max_follow_ups"] else "NEXT"
        # An agent cannot end an under-budget interview just by recommending COMPLETE.
        return "ADJUST_DIFFICULTY" if recommendation == "ADJUST_DIFFICULTY" else "NEXT"

    async def _new_question(self, session, context, questions, artifact, policy, *, kind="NEXT", parent=None):
        validate_question(artifact, context, policy)
        if context.get("interviewer_config") and kind != "ADJUST_DIFFICULTY":
            expected_difficulty = questions[-1].difficulty if questions else "MEDIUM"
            if artifact.difficulty != expected_difficulty:
                raise AppError("DIFFICULTY_POLICY", "风格不能改变难度；仅允许依据有效评价调整", 422)
        subject = classify(artifact.question)
        if parent and subject['category'] != 'BACKGROUND' and kind in ('FOLLOW_UP', 'DEEP_DIVE'):
            # Parent-bound deepening is still its subject, even if wording uses
            # a secondary term such as “消费线程” rather than repeating Kafka.
            target = policy.get('target_topic')
            if target in TOPICS:
                subject['topic'] = target
        if policy.get("source_type") == "CANDIDATE_PROFILE" and policy.get("target_topic") in mentioned_topics(artifact.question):
            subject["topic"] = policy["target_topic"]
        if subject["category"] != "BACKGROUND" and policy.get("target_category") in ("PROJECT", "FOUNDATION", "JOB_TECHNICAL", "SCENARIO"):
            subject["category"] = policy["target_category"]
        public = next((q for q in context["public_questions"] if q["id"] == artifact.source_question_id), None)
        evidence = [e for e in context["candidate_evidence"] if e["key"] in artifact.candidate_evidence_ids]
        fact_ids = list(artifact.candidate_fact_ids)
        evidence_ids = list(artifact.candidate_evidence_ids)
        if parent:
            inherited = parent.candidate_refs_json or {}
            fact_ids = list(dict.fromkeys(fact_ids + inherited.get("fact_ids", [])))
            evidence_ids = list(dict.fromkeys(evidence_ids + inherited.get("evidence_ids", [])))
            evidence = [e for e in context["candidate_evidence"] if e["key"] in evidence_ids]
        # A private target's requirement ids are local indices, not rows in
        # job_requirements: persisting one would point at an unrelated public
        # requirement. Keep the ref in the snapshot refs instead.
        private_target = context.get("job_target") is not None
        requirement_id = None if private_target else artifact.job_requirement_id
        q = InterviewSessionQuestion(session_id=session.id, question_index=max((q.question_index for q in questions), default=-1) + 1,
            question=artifact.question, standard_answer="\n".join(artifact.expected_points),
            dimension=artifact.dimension, difficulty=artifact.difficulty, source=artifact.source_type,
            source_question_id=artifact.source_question_id, job_requirement_id=requirement_id,
            source_fingerprint=public["fingerprint"] if public else None,
            candidate_refs_json=dict(fact_ids=fact_ids, evidence_ids=evidence_ids, evidence=evidence,
                **({"job_target_requirement": artifact.job_requirement_id} if private_target
                   and artifact.job_requirement_id is not None else {}),
                **({"interviewer_guidance": policy["interaction"]["guidance"]}
                   if (policy.get("interaction") or {}).get("guidance") else {}),
                project_ids=[p["id"] for p in context["normalized"]["projects"] if p["fact_id"] in fact_ids],
                topic_policy={**subject, "policy_version": POLICY_VERSION}),
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
        if snapshot.context_json.get("adaptive_policy"):
            from interview_api.modules.interview.adaptive import planning_payload, validate_decision
            fresh = planning_payload(snapshot.context_json, [], None, session.interview_plan_json)
            if validate_decision(artifact, fresh) != policy:
                raise conflict("首题决策与本场冻结策略不一致")
        if artifact.recommendation != "NEXT" or not artifact.question:
            raise AppError("INVALID_START_ARTIFACT", "首题输出无效", 422)
        q = await self._new_question(session, snapshot.context_json, [], artifact.question, policy)
        if snapshot.context_json.get("adaptive_policy"):
            q.candidate_refs_json = {**q.candidate_refs_json, "adaptive_decision": artifact.model_dump(), 'main_agent_run_id': result.run_id}
            q.candidate_refs_json["topic_policy"]["topic"] = artifact.target_knowledge_id.rsplit(":", 1)[-1]
        session.status, session.started_at = "IN_PROGRESS", datetime.now(timezone.utc)
        session.state_version += 1
        data = question_data(q, session.question_count)
        data["mode"] = "replay" if result.provider == "replay" else "live"
        await self._message(session, "QUESTION", q.question, data)
        return data

    async def prepare_answer(self, session_id, user_id, question_id, key, content, *, allow_unknown_retry=False):
        session = await self.owned(session_id, user_id, lock=True)
        answer = await self.db.scalar(select(InterviewAnswer).where(InterviewAnswer.session_id == session_id, InterviewAnswer.idempotency_key == key))
        if answer:
            message = await self.db.get(InterviewMessage, answer.message_id)
            if answer.session_question_id != question_id or message.content != content:
                raise conflict("幂等键已用于其他题目或回答")
            if answer.status == "COMPLETED":
                return answer, session.state_version, True
            if answer.status == "RUNNING":
                lease = (session.interview_plan_json or {}).get("answer_processing") or {}
                expires = datetime.fromisoformat(lease["expires_at"]) if lease.get("answer_id") == answer.id and lease.get("expires_at") else answer.created_at + timedelta(seconds=2 * settings.interview_model_timeout_seconds + 30)
                if expires > datetime.now(timezone.utc):
                    raise AppError("INTERVIEW_ANSWER_RUNNING", "回答正在评价或生成追问，请等待或刷新查看；不会重复提交。", 409)
                plan = dict(session.interview_plan_json or {})
                calls = plan.get("unknown_model_calls") or {}
                uncertain = bool(plan.get("model_call_reservations")) or any(
                    (row or {}).get("answer_id") == answer.id for row in calls.values())
                if uncertain and not allow_unknown_retry:
                    # The vanished process reserved (and may have sent) a call for
                    # this answer. Lease expiry is not evidence the request never
                    # left, so a resend needs the user's explicit acceptance.
                    answer.status, answer.error = "FAILED", "interview_outcome_unknown"
                    raise AppError("INTERVIEW_OUTCOME_UNKNOWN",
                        "上一次回答的模型调用未能确认是否已发出，系统不会自动重发；请新建一场面试，或确认后重试", 409)
                if uncertain:
                    # Explicit, user-confirmed resend. Keep a bounded audit trail
                    # instead of silently forgetting that a call is unaccounted.
                    retries = list(plan.get("unknown_model_retries") or [])
                    retries.append({"answer_id": answer.id,
                                    "call_keys": sorted(plan.get("model_call_reservations") or {}),
                                    "at": datetime.now(timezone.utc).isoformat()})
                    plan["unknown_model_retries"] = retries[-20:]
                    plan["unknown_model_calls"] = {k: v for k, v in calls.items()
                                                   if (v or {}).get("answer_id") != answer.id}
                    session.interview_plan_json = plan
                # Fence a vanished request. Keep the exact Answer and all successful audit history.
                runs = (await self.db.scalars(select(ExperienceAgentRun).join(InterviewAgentCall,
                    InterviewAgentCall.agent_run_id == ExperienceAgentRun.id).where(
                    InterviewAgentCall.answer_id == answer.id, ExperienceAgentRun.status == "RUNNING"))).all()
                for run in runs:
                    run.status, run.error_message, run.finished_at = "FAILED", "interview_request_interrupted", datetime.now(timezone.utc)
                    for step in (await self.db.scalars(select(ExperienceAgentStepRun).where(
                            ExperienceAgentStepRun.agent_run_id == run.id, ExperienceAgentStepRun.status == "RUNNING"))).all():
                        step.status, step.error_message = "FAILED", "interview_request_interrupted"
                plan = dict(session.interview_plan_json or {})
                # Unknown usage remains conservatively charged, not silently refunded.
                unreported = sum(plan.get("model_call_reservations", {}).values())
                session.interview_plan_json = {**plan, "model_call_reservations": {}, "reserved_model_tokens": 0,
                    "unreported_model_tokens": plan.get("unreported_model_tokens", 0) + unreported}
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
        session.interview_plan_json = {**(session.interview_plan_json or {}), "answer_processing": dict(
            answer_id=answer.id, stage="evaluating", expires_at=(datetime.now(timezone.utc) +
            timedelta(seconds=2 * settings.interview_model_timeout_seconds + 30)).isoformat())}
        return answer, session.state_version, False

    async def answer_stage(self, session_id, user_id, answer_id, expected_version, stage):
        session = await self.owned(session_id, user_id, lock=True)
        if session.state_version != expected_version:
            raise conflict()
        processing = dict((session.interview_plan_json or {}).get("answer_processing") or {})
        if processing.get("answer_id") == answer_id:
            session.interview_plan_json = {**session.interview_plan_json, "answer_processing": {**processing, "stage": stage}}

    async def failed_answer(self, session_id, user_id, answer_id, expected_version, error):
        session = await self.owned(session_id, user_id, lock=True)
        if session.state_version == expected_version:
            answer = await self.db.get(InterviewAnswer, answer_id)
            if answer.status != "COMPLETED":
                answer.status, answer.error = "FAILED", error[:500]
                session.interview_plan_json = {**(session.interview_plan_json or {}), "answer_processing": None}
                # ANSWERED is retained. Never mark EVALUATED or advance on model failure.

    async def commit_answer(self, session_id, user_id, answer_id, expected_version, evaluation, main, requested_action, policy,
                            *, terminal_detail=None, terminal_reason=None):
        session = await self.owned(session_id, user_id, lock=True)
        answer = await self.db.get(InterviewAnswer, answer_id)
        if session.state_version != expected_version or session.active_question_id != answer.session_question_id or answer.status != "RUNNING":
            raise conflict()
        _, context, questions, q, tokens = await self.state(session_id, user_id)
        artifact = evaluation.artifact
        adaptive = bool(context.get("adaptive_policy"))
        if (terminal_detail is None) != (terminal_reason is None):
            raise AppError("INVALID_TERMINAL_REQUEST", "预算终止须同时给出阶段与原因", 422)
        if terminal_reason is not None and terminal_reason not in TERMINAL_REASONS:
            raise AppError("INVALID_TERMINAL_REQUEST", "未知的预算终止原因", 422)
        if terminal_detail is not None and not adaptive:
            # Ordinary mode keeps its own budget rules; no caller may shorten it.
            raise AppError("INVALID_TERMINAL_REQUEST", "预算终止仅适用于自适应面试", 422)
        if adaptive:
            from interview_api.modules.interview.adaptive import planning_payload, validate_decision, resolve_observations, knowledge_state, apply_observations, all_goals_demonstrated
            message = await self.db.get(InterviewMessage, answer.message_id)
            base_payload = planning_payload(context, questions, q, session.interview_plan_json,
                answer=message.content, answer_id=answer_id)
            resolved = resolve_observations(artifact, base_payload)
            projected = apply_observations(knowledge_state(session.interview_plan_json), resolved,
                question_id=q.id, answer_id=answer_id, evaluation_id=None)
            stop = self.stop_reason(context, questions, rounds=session.turn_count,
                tokens=tokens, started_at=session.started_at)
            exhausted = stop is not None
            decision_payload = planning_payload(context, questions, q, session.interview_plan_json,
                answer=message.content, answer_id=answer_id, evaluation=artifact, evaluation_run_id=evaluation.run_id)
            terminal = exhausted or all_goals_demonstrated(context, projected) or not decision_payload["adaptive"]["choices"]
            if terminal_detail is not None:
                # Only the interview service may ask for this, and only when the
                # next question could not be admitted. The evaluated answer is
                # still committed below; no question is invented for the gap.
                if requested_action != "COMPLETE" or main is not None:
                    raise AppError("INVALID_TERMINAL_REQUEST", "预算终止只能用于没有下一题时的结束", 422)
                terminal = True
            if requested_action == "COMPLETE" and not terminal:
                raise conflict("尚有合法考察目标且预算未耗尽，不能直接结束")
            action = "COMPLETE" if terminal else requested_action
            if action != "COMPLETE":
                if not main:
                    raise AppError("INVALID_MAIN_ARTIFACT", "缺少有效面试决策，未推进会话", 422)
                checked_policy = validate_decision(main.artifact, decision_payload)
                if checked_policy != policy:
                    raise conflict("本轮决策空间已变化，未覆盖原策略")
        else:
            action = self.choose_action(context, questions, q, artifact.next_action_recommendation,
                rounds=session.turn_count, tokens=tokens, started_at=session.started_at, evaluation=artifact.model_dump())
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
        if adaptive:
            state = apply_observations(knowledge_state(session.interview_plan_json), resolved,
                question_id=q.id, answer_id=answer_id, evaluation_id=ev.id)
            session.interview_plan_json = {**(session.interview_plan_json or {}), "adaptive_knowledge": state}
            trace = deepcopy(session.planner_trace_json or {})
            decisions = trace.get("adaptive_decisions", [])
            decisions.append(dict(answer_id=answer_id, evaluation_id=ev.id, agent_run_id=main.run_id if main else None,
                decision=main.artifact.model_dump() if main else None, executed_action=action,
                verdict=("TERMINATED_BUDGET" if terminal_detail else
                         "NOT_EXECUTED_BUDGET" if main and action == "COMPLETE" else "ACCEPTED"),
                state_version=expected_version))
            from interview_api.modules.interview.adaptive import bounded
            session.planner_trace_json = bounded({**trace, "adaptive_decisions": decisions})
        q.status = "EVALUATED"
        score = round(sum(getattr(artifact, key) for key in DIMENSIONS) / len(DIMENSIONS), 2)
        eval_data = dict(artifact.model_dump(), score=score, question_id=q.id, answer_id=answer.id,
                         evaluation_id=ev.id, action=action, mode="replay" if evaluation.provider == "replay" else "live")
        events = [["evaluation", eval_data]]
        await self._message(session, "EVALUATION", artifact.evaluation, eval_data)
        q.status = "COMPLETED"
        if action == "COMPLETE":
            answer.status = "COMPLETED"
            summary = await self.complete(session)
            if adaptive:
                # Build a new dict: complete() stores the same object it returns,
                # so mutating it in place is invisible to the ORM's change check.
                summary = {**summary, "completion_reason": (terminal_reason or stop or (
                    "BLUEPRINT_COVERED" if all_goals_demonstrated(context, projected) else "NO_LEGAL_TARGET_WITHIN_POLICY"))}
                if terminal_detail:
                    # Stable sub-code so reports/UI can tell "no next question could
                    # be afforded" apart from "the whole session budget was spent".
                    summary = {**summary, "completion_detail": terminal_detail}
                session.completion_json = deepcopy(summary)
            events.append(["complete", summary])
            await self._message(session, "INTERVIEW_COMPLETE", "面试结束", summary)
        else:
            parent = q if action in ("FOLLOW_UP", "DEEP_DIVE") else None
            next_q = await self._new_question(session, context, questions, main.artifact.question, policy, kind=action, parent=parent)
            if adaptive:
                next_q.candidate_refs_json = {**next_q.candidate_refs_json, "adaptive_decision": main.artifact.model_dump(), 'main_agent_run_id': main.run_id}
                next_q.candidate_refs_json["topic_policy"]["topic"] = main.artifact.target_knowledge_id.rsplit(":", 1)[-1]
            if main.artifact.tool_result_ids:
                next_q.candidate_refs_json = {**(next_q.candidate_refs_json or {}), "tool_result_ids": main.artifact.tool_result_ids}
            if parent:
                parent.follow_up_count += 1
            data = question_data(next_q, session.question_count)
            data["mode"] = "replay" if main.provider == "replay" else "live"
            if action == "FOLLOW_UP":
                data.update(follow_up_count=self.follow_up_consumption(questions, q) + 1,
                            max_follow_ups=context["budgets"]["max_follow_ups"])
            events.append(["follow_up" if action == "FOLLOW_UP" else "question", data])
            await self._message(session, "FOLLOW_UP" if action == "FOLLOW_UP" else "QUESTION", next_q.question, data)
        session.state_version += 1
        events.append(["done", dict(message_id=answer.message_id, turn_index=session.turn_count, action=action, state_version=session.state_version)])
        answer.status, answer.result_events = "COMPLETED", events
        session.interview_plan_json = {**(session.interview_plan_json or {}), "answer_processing": None}
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
        await self.db.flush()
        snapshot = await self.db.get(InterviewContextSnapshot, session.context_snapshot_id) if session.context_snapshot_id else None
        if snapshot and snapshot.context_json.get("memory_guided_plan"):
            from interview_api.modules.memory.capability_service import CapabilityMemoryService
            await CapabilityMemoryService(self.db).refresh(session.user_id)
        if snapshot and snapshot.context_json.get('training'):
            from interview_api.modules.interview.training import completion_feedback
            feedback = await completion_feedback(self.db, session, snapshot.context_json)
            summary = {**summary, 'training_feedback': feedback}
            session.completion_json = summary
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
        if action == "COMPLETE":
            result = dict(skipped_question_id=q.id, next_question=None, completion=await self.complete(session))
            await self._message(session, "INTERVIEW_COMPLETE", "面试结束", result["completion"])
        else:
            if not main or main.artifact.recommendation != "NEXT" or not main.artifact.question:
                raise AppError("INVALID_MAIN_ARTIFACT", "下一题无效", 422)
            if context.get("adaptive_policy"):
                from interview_api.modules.interview.adaptive import planning_payload, validate_decision
                payload = planning_payload(context, questions, q, session.interview_plan_json)
                payload["adaptive"]["choices"] = [c for c in payload["adaptive"]["choices"] if "NEXT" in c["strategies"]]
                if validate_decision(main.artifact, payload) != policy:
                    raise conflict("跳题决策已变化")
            next_q = await self._new_question(session, context, questions, main.artifact.question, policy)
            if context.get("adaptive_policy"):
                next_q.candidate_refs_json = {**next_q.candidate_refs_json, "adaptive_decision": main.artifact.model_dump(), 'main_agent_run_id': main.run_id}
                next_q.candidate_refs_json["topic_policy"]["topic"] = main.artifact.target_knowledge_id.rsplit(":", 1)[-1]
            data = question_data(next_q, session.question_count)
            await self._message(session, "QUESTION", next_q.question, data)
            result = dict(skipped_question_id=q.id, next_question=data)
        session.state_version += 1
        return result

    async def _message(self, session, kind, content, data):
        self.db.add(InterviewMessage(session_id=session.id, role="ASSISTANT", content=content,
            metadata_json={**data, "type": kind, "source": data.get("source", "M4"),
                           "display_order": 1 if kind == "EVALUATION" else 2}, turn_index=session.turn_count))
