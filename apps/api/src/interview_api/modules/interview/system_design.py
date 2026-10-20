"""V1.2 bounded executable schedule simulation; NOT a production load test/sandbox."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from interview_api.core.exceptions import AppError
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.interview.models import InterviewAnswer, InterviewMessage


class DesignPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    deduplication: Literal["NONE", "CHECK_THEN_WRITE", "ATOMIC_KEY"]
    capacity_guard: Literal["NONE", "CHECK_THEN_WRITE", "ATOMIC_RESERVATION"]
    capacity: int = Field(ge=1, le=20)


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_version: int = Field(ge=1)
    requests: int = Field(ge=2, le=40)


class ToolDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: Literal["verify_idempotency", "verify_capacity"]
    arguments: ToolArguments
    reason: str = Field(min_length=1, max_length=400)


SCENARIO = dict(id="reservation_v1", version="1.0.0", title="限量名额预约系统",
    description="设计一个限量预约接口：同一请求重试不能重复创建预约，多用户并发不能超售。用户明确填写方案，工具只模拟声明的策略，不证明真实代码可靠。",
    max_tool_calls=6, tool_timeout_seconds=2,
    tools=[dict(name="verify_idempotency", description="同一业务请求重复及竞争提交，检测重复预约"),
           dict(name="verify_capacity", description="不同业务请求竞争名额，检测超售")])


async def execute_tool(tool, arguments, plan):
    """Explicit interleaving: check phase of all requests precedes write phase.

    Atomic strategies use one indivisible transition. No SQL/shell/network or user
    expressions. Returned counters derive from the simulated event trace.
    """
    args = ToolArguments.model_validate(arguments)
    model = DesignPlan.model_validate(plan)
    if tool not in {t["name"] for t in SCENARIO["tools"]}:
        raise AppError("DESIGN_TOOL_UNKNOWN", "未授权的验证工具", 422)
    if tool == "verify_capacity" and args.requests <= model.capacity:
        raise AppError("DESIGN_PARAMETERS_INSUFFICIENT", "并发验证的请求数必须超过容量", 422)
    events, accepted = [], []
    for index in range(args.requests):
        await asyncio.sleep(0)
        if tool == "verify_idempotency":
            ok = not accepted if model.deduplication == "ATOMIC_KEY" else True
        else:
            ok = len(accepted) < model.capacity if model.capacity_guard == "ATOMIC_RESERVATION" else True
        if ok: accepted.append(index)
        events.append(dict(request=index + 1, accepted=ok))
    violation = len(accepted) > (1 if tool == "verify_idempotency" else model.capacity)
    return dict(simulator_version="1.0.0", execution_mode="DETERMINISTIC_SIMULATION", passed=not violation,
        submitted_count=args.requests, accepted_count=len(accepted), capacity=model.capacity,
        violation="DUPLICATE_RESERVATION" if tool == "verify_idempotency" and violation else "OVERSELL" if violation else None,
        schedule="all_checks_before_writes; atomic_operations_serialized", events=events,
        limitation="只验证所声明策略下的有界交错，不代表真实部署、压测或所有可能调度。")


async def bind_plan(controller, session_id, user_id, answer, expected, plan):
    plan = DesignPlan.model_validate(plan).model_dump()
    session = await controller.owned(session_id, user_id, lock=True)
    if session.state_version != expected: raise AppError("DESIGN_STALE", "方案状态已变化", 409)
    message = await controller.db.get(InterviewMessage, answer.message_id)
    metadata = deepcopy(message.metadata_json or {})
    old = metadata.get("design_revision")
    ledger = deepcopy(session.planner_trace_json or {})
    for invocation in ledger.get("design_tools", []):
        if invocation["answer_id"] == answer.id and invocation["status"] == "RUNNING" and invocation["state_version"] != expected:
            invocation.update(status="FAILED", error="DESIGN_REQUEST_INTERRUPTED")
    session.planner_trace_json = ledger
    if old:
        if old["plan"] != plan: raise AppError("DESIGN_RETRY_CHANGED", "重试不能修改已提交方案", 409)
        return old
    revisions = ledger.get("design_revisions", [])
    revision = dict(version=len(revisions) + 1, answer_id=answer.id, plan=plan, fingerprint=digest(plan))
    message.metadata_json = {**metadata, "design_revision": revision}
    session.planner_trace_json = {**ledger, "design_revisions": revisions + [revision]}
    from datetime import timedelta
    from interview_api.core.config import settings
    session.interview_plan_json = {**(session.interview_plan_json or {}), "answer_processing": dict(
        answer_id=answer.id, stage="validating_design", expires_at=(datetime.now(timezone.utc) + timedelta(
            seconds=3 * settings.interview_model_timeout_seconds + SCENARIO["tool_timeout_seconds"] + 30)).isoformat())}
    return revision


async def reserve_tool(controller, sid, uid, aid, expected, revision, decision, run_id):
    session = await controller.owned(sid, uid, lock=True)
    ledger = deepcopy(session.planner_trace_json or {})
    if session.state_version != expected or not ledger.get("design_revisions") or ledger["design_revisions"][-1] != revision:
        raise AppError("DESIGN_STALE", "旧方案不能执行或覆盖新方案", 409)
    if decision.arguments.plan_version != revision["version"]:
        raise AppError("DESIGN_VERSION_MISMATCH", "工具必须验证当前方案版本", 422)
    invocations = ledger.get("design_tools", [])
    if len(invocations) >= SCENARIO["max_tool_calls"]:
        raise AppError("DESIGN_TOOL_BUDGET", "本场工具调用额度已耗尽；未评分或推进", 409)
    item = dict(id="dt_" + digest([sid, aid, expected, len(invocations)])[:24], answer_id=aid,
        plan_version=revision["version"], plan_fingerprint=revision["fingerprint"], state_version=expected,
        tool=decision.tool, arguments=decision.arguments.model_dump(), reason=decision.reason,
        agent_run_id=run_id, status="RUNNING", started_at=datetime.now(timezone.utc).isoformat(), result=None, error=None)
    session.planner_trace_json = {**ledger, "design_tools": invocations + [item]}
    return item


async def finish_tool(controller, sid, uid, expected, invocation_id, result=None, error=None):
    session = await controller.owned(sid, uid, lock=True)
    ledger = deepcopy(session.planner_trace_json or {})
    item = next((i for i in ledger.get("design_tools", []) if i["id"] == invocation_id), None)
    if not item or session.state_version != expected or ledger["design_revisions"][-1]["version"] != item["plan_version"]:
        raise AppError("DESIGN_STALE", "迟到的旧版本工具结果已拒绝", 409)
    if item["status"] != "RUNNING": raise AppError("DESIGN_TOOL_FINALIZED", "工具结果已结算", 409)
    item.update(status="FAILED" if error else "SUCCEEDED", result=result, error=error,
        finished_at=datetime.now(timezone.utc).isoformat())
    session.planner_trace_json = ledger
    if not error:
        answer = await controller.db.get(InterviewAnswer, item["answer_id"])
        message = await controller.db.get(InterviewMessage, answer.message_id)
        message.metadata_json = {**(message.metadata_json or {}), "tool_evidence": deepcopy(item)}
    return item


def validate_tool_citations(artifact, payload):
    evidence = payload.get("tool_evidence")
    if not evidence and (artifact.tool_result_ids or artifact.tool_observed_outcome is not None):
        raise AppError("DESIGN_EVIDENCE_REQUIRED", "不存在工具执行记录，不允许虚构引用或结论", 422)
    if evidence:
        refs = set(artifact.tool_result_ids)
        if refs != {evidence["id"]}:
            raise AppError("DESIGN_EVIDENCE_REQUIRED", "必须精确引用本轮真实工具结果，不得编造结果 ID", 422)
        if artifact.tool_observed_outcome != ("PASS" if evidence["result"]["passed"] else "FAIL"):
            raise AppError("DESIGN_RESULT_MISMATCH", "模型观察结论与真实工具结果不符", 422)


def design_policy(context, questions, action="NEXT"):
    from interview_api.modules.interview.context import question_policy
    policy = question_policy(context, questions, action)
    return {**policy, "source_type": "DYNAMIC_GENERATED", "source_id": None, "target_category": "SCENARIO", "target_topic": "design_validation"}
