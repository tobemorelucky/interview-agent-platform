"""One explicitly authorized LIVE acceptance batch; fixture data, real model/tools.

Never changes .env, validators or the formal database. Refuses attempt overwrite.
All actual HTTP attempts, including repair, share one conservative budget/clock.
"""
import argparse
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import msvcrt
from pathlib import Path
import sys
import time
from uuid import uuid4

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
sys.path[:0] = [str(API / "src"), str(API / "tests")]

import httpx
from pydantic import BaseModel, ConfigDict
from typing import Literal
from sqlalchemy import select, func
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from interview_api.core.config import settings
from interview_api.infrastructure.models.provider import CompatibleModelProvider, ModelProvider
from interview_api.infrastructure.models.router import (
    ModelRouter, build_interview_router, build_interview_coach_router,
    build_resume_profile_router, build_job_matching_router, build_model_router,
)
from interview_api.infrastructure.models.structured import generate_structured
from interview_api.infrastructure.models.scope import model_scope_for

TARGET = "deepseek-v4-pro-ga-260813"
CALL = ContextVar("acceptance_call", default=None)


class BudgetStop(RuntimeError):
    pass


class Batch:
    def __init__(self, output):
        self.output = output
        self.start = time.monotonic()
        self.phase = "probe"
        self.logical = 0
        self.charged = 0
        self.reserved = 0
        self.records = []
        self.results = []
        self.stop_reason = None

    def save(self, **extra):
        value = dict(model=TARGET, model_mode="LIVE", data_mode="LOCAL_FIXTURE",
            limits=dict(logical=14, http=28, tokens=300000, seconds=900),
            logical_calls=self.logical, http_attempts=len(self.records),
            conservative_charged_tokens=self.charged, inflight_reserved_tokens=self.reserved,
            elapsed_seconds=round(time.monotonic()-self.start, 3),
            http=self.records, scenarios=self.results, stop_reason=self.stop_reason, **extra)
        self.output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(self):
        if self.stop_reason:
            raise BudgetStop(self.stop_reason)
        if time.monotonic()-self.start >= 900:
            raise BudgetStop("BATCH_DEADLINE")

    async def on_request(self, request):
        self.check()
        body = json.loads(request.content)
        if body.get("model") != TARGET:
            raise BudgetStop("UNEXPECTED_MODEL")
        estimate = len(request.content) + int(body["max_tokens"]) + 1024
        if len(self.records) >= 28 or self.charged+self.reserved+estimate > 300000:
            raise BudgetStop("HTTP_OR_TOKEN_BUDGET")
        self.reserved += estimate
        call = CALL.get()
        record = dict(number=len(self.records)+1, logical=call["logical"], route=call["route"],
            schema=call["schema"], repair=call["repair"], phase=self.phase, model=TARGET,
            thinking=body.get("thinking"), output_limit=body["max_tokens"],
            input_chars=sum(len(m.get("content", "")) for m in body["messages"]),
            estimate=estimate, accounting="RESERVED", http_status=None)
        self.records.append(record)
        request.extensions["acceptance"] = (record, time.monotonic())
        self.save(status="RUNNING")

    async def on_response(self, response):
        record, started = response.request.extensions["acceptance"]
        await response.aread()
        try:
            data = response.json()
        except ValueError:
            data = {}
        usage = data.get("usage") or {}
        total = usage.get("total_tokens")
        measured = type(total) is int and total >= 0
        self.reserved -= record["estimate"]
        self.charged += total if measured else record["estimate"]
        detail = usage.get("prompt_tokens_details") or {}
        record.update(http_status=response.status_code, latency_ms=round((time.monotonic()-started)*1000),
            input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
            total_tokens=total, reasoning_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            cached_tokens=detail.get("cached_tokens", usage.get("prompt_cache_hit_tokens")),
            finish_reason=((data.get("choices") or [{}])[0]).get("finish_reason"),
            accounting="ACTUAL_USAGE" if measured else "UNKNOWN_CONSERVATIVE")
        error = data.get("error") or {}
        if response.status_code >= 400:
            record["provider_error_code"] = str(error.get("code") or "")[:100]
            self.stop_reason = "PROVIDER_HTTP_" + str(response.status_code)
        self.save(status="RUNNING")

    def finalize_unknown(self):
        for record in self.records:
            if record["accounting"] == "RESERVED":
                self.charged += record["estimate"]
                self.reserved -= record["estimate"]
                record["accounting"] = "UNKNOWN_CONSERVATIVE"


class MeteredProvider(ModelProvider):
    def __init__(self, inner, batch, route, client):
        self.inner, self.batch, self.route = inner, batch, route
        self.inner.client = client
        for attr in ("provider_name", "model_name", "base_url", "timeout", "max_output_tokens", "thinking_type", "governed"):
            setattr(self, attr, getattr(inner, attr))

    async def generate(self, messages, *, schema):
        self.batch.check()
        repair = messages[0]["content"].startswith("Repair JSON syntax/structural fields only.")
        if not repair:
            if self.batch.logical >= 14:
                raise BudgetStop("LOGICAL_BUDGET")
            self.batch.logical += 1
        token = CALL.set(dict(logical=self.batch.logical, repair=repair, route=self.route, schema=schema.get("title")))
        try:
            return await self.inner.generate(messages, schema=schema)
        finally:
            CALL.reset(token)


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ok"]
    ready: bool


async def run(batch, database):
    url = make_url(settings.database_url).set(database=database)
    assert database.startswith("interview_agent_v12_acceptance_live_") and database != make_url(settings.database_url).database
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    from interview_api.modules.users.models import User
    from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation, InterviewSessionQuestion
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview_report.service import InterviewReportService
    from interview_api.infrastructure.models.governance_models import ModelAdmission
    try:
        configs = [build_interview_router(), build_interview_coach_router(), build_model_router(),
                   build_resume_profile_router(), build_job_matching_router()]
        batch.routes = [{"route": r, "model": p.model_name, "provider": p.provider_name,
            "thinking": p.thinking_type, "timeout": p.timeout} for router in configs for r, p in router.routes.items()]
        if settings.llm_model != TARGET or any(p.model_name != TARGET or router.fallback_routes for router in configs for p in router.routes.values()):
            raise BudgetStop("ROUTE_OR_FALLBACK_MISMATCH")
        print(json.dumps(dict(routes=batch.routes, fallback=False), ensure_ascii=False), flush=True)
        async with factory() as db:
            uid = await db.scalar(select(User.id).where(User.email == "v12.demo@example.com"))
            from interview_api.modules.interview.models import InterviewContextSnapshot
            snapshot = await db.scalar(select(InterviewContextSnapshot).order_by(InterviewContextSnapshot.id))
            context = snapshot.context_json
        assert uid and context
        async with httpx.AsyncClient(event_hooks={"request": [batch.on_request], "response": [batch.on_response]}) as client:
            base = configs[0].primary("interview_main")
            probe_inner = CompatibleModelProvider(base_url=base.base_url, api_key=base.api_key, model=base.model_name,
                timeout_seconds=45, max_output_tokens=128, thinking_type=base.thinking_type, governed=True)
            probe_router = ModelRouter({"acceptance_probe": MeteredProvider(probe_inner, batch, "acceptance_probe", client)})
            with model_scope_for(factory, user_id=uid, route="acceptance_probe"):
                result = await generate_structured(probe_router, "acceptance_probe", system="Return JSON matching the provided schema. status=ok and ready=true.",
                    payload={}, response_model=Probe, timeout_seconds=45)
                assert result.parsed.status == "ok" and result.parsed.ready is True
                batch.records[-1]["business_validation"] = "PROBE_PASSED"
                batch.save(status="PROBE_PASSED", routes=batch.routes)
                print("PROBE_PASSED", flush=True)
            routes = {r: MeteredProvider(p, batch, r, client) for router in configs[:2] for r, p in router.routes.items()}
            router = ModelRouter(routes, concurrency=configs[0].concurrency)
            core = MockInterviewService(factory, router)
            scenarios = [
                ("A_missing_idempotency", "verify_idempotency", [
                    ({"deduplication":"NONE","capacity_guard":"ATOMIC_RESERVATION","capacity":1},
                     "TEST 场景 A：同一用户可能重试同一个预约请求。数据库按剩余名额大于零进行原子条件扣减，并在事务中插入预约；目前没有业务请求唯一键，也没有重试去重，超时后客户端直接重新发起相同请求。请检验这个方案。"),
                    ({"deduplication":"ATOMIC_KEY","capacity_guard":"ATOMIC_RESERVATION","capacity":1},
                     "TEST 修订 A：为预约引入稳定的业务请求键和唯一约束，唯一键插入与原子名额扣减在同一事务内完成。重复键返回原预约结果，不重复扣减；事务失败整体回滚。这里只声明策略，请再次检验，不把模拟通过视为完整生产正确性。")]),
                ("B_capacity_race", "verify_capacity", [
                    ({"deduplication":"ATOMIC_KEY","capacity_guard":"CHECK_THEN_WRITE","capacity":1},
                     "TEST 场景 B：使用数据库唯一业务请求键保证同一请求不重复预约。但不同用户请求会各自先读取剩余名额，发现还有一个名额就创建预约并扣减，检查和更新不是一个原子操作。希望检验不同请求争抢最后名额的风险。"),
                    ({"deduplication":"ATOMIC_KEY","capacity_guard":"ATOMIC_RESERVATION","capacity":1},
                     "TEST 修订 B：保留业务唯一键，把名额保护改为同一事务中的条件更新，仅当剩余量大于零才减一，并检查影响行数。失败回滚预约记录；重复请求读取已保存结果。请重新验证并说明模拟的适用边界。")]),
            ]
            for index, (name, expected_tool, answers) in enumerate(scenarios):
                batch.phase = name + ".create"
                session = await core.create(uid, CreateSessionRequest(company_id=context["company_id"], job_id=context["job_id"],
                    question_count=2, interviewer_skill_id="java_backend", interview_round="project_deep_dive", design_interview=True))
                result = dict(name=name, session_id=session["id"], user_id=uid, shared_jd_id=session["context_snapshot"]["job_description_version_id"],
                    status="RUNNING", rounds=[])
                batch.results.append(result)
                pinned = session["snapshot_fingerprint"]
                batch.phase = name + ".start"
                opening = await core.start(session["id"], uid)
                result["opening"] = opening["question"]
                for ordinal, (plan, answer) in enumerate(answers, 1):
                    batch.phase = f"{name}.answer{ordinal}"
                    _, _, _, active, _ = await core.read_state(session["id"], uid)
                    key = "LIVE-ACCEPTANCE-" + uuid4().hex
                    events = await core.answer(session["id"], uid, active.id, key, answer, design_plan=plan)
                    detail = await core.detail(session["id"], uid)
                    assert detail["snapshot_fingerprint"] == pinned
                    tool = detail["planner_trace_json"]["design_tools"][-1]
                    assert tool["status"] == "SUCCEEDED"
                    if ordinal == 1:
                        assert tool["tool"] == expected_tool, "LIVE_TOOL_CHOICE_MISMATCH"
                        assert tool["result"]["passed"] is False, "EXPECTED_INITIAL_VIOLATION"
                    else:
                        assert tool["result"]["passed"] is True, "EXPECTED_REVISED_PASS"
                    result["rounds"].append(dict(ordinal=ordinal, question=active.question,
                        tool=tool["tool"], result_id=tool["id"], passed=tool["result"]["passed"],
                        accepted_count=tool["result"]["accepted_count"], decision_reason=tool.get("reason"),
                        next_question=(detail.get("current_question") or {}).get("question"), event_types=[e[0] for e in events]))
                    before = batch.logical
                    assert await core.answer(session["id"], uid, active.id, key, answer, design_plan=plan) == events
                    assert batch.logical == before
                    result["idempotency"] = True
                    batch.save(status="RUNNING", routes=batch.routes)
                    print(name + " answer " + str(ordinal) + " committed", flush=True)
                assert detail["status"] == "COMPLETED"
                async with factory() as db:
                    result["answers"] = await db.scalar(select(func.count()).select_from(InterviewAnswer).where(InterviewAnswer.session_id == session["id"]))
                    result["evaluations"] = await db.scalar(select(func.count()).select_from(InterviewEvaluation).where(InterviewEvaluation.session_id == session["id"]))
                    result["open_questions"] = await db.scalar(select(func.count()).select_from(InterviewSessionQuestion).where(
                        InterviewSessionQuestion.session_id == session["id"], InterviewSessionQuestion.status.in_(["PENDING","ASKED"])))
                    evals = (await db.scalars(select(InterviewEvaluation).where(InterviewEvaluation.session_id == session["id"]).order_by(InterviewEvaluation.id))).all()
                    result["evaluation_summaries"] = [dict(id=e.id, tool_result_ids=e.artifact_json.get("tool_result_ids"),
                        tool_observed_outcome=e.artifact_json.get("tool_observed_outcome"), evaluation=e.artifact_json.get("evaluation")) for e in evals]
                assert result["answers"] == result["evaluations"] == 2 and result["open_questions"] == 0
                result["status"] = "COMPLETED"
                if batch.logical < 14:
                    batch.phase = name + ".coach"
                    report = await InterviewReportService(factory, router).generate(session["id"], uid)
                    result["report_id"], result["report_status"] = report["id"], report["generation_status"]
                    if report["generation_status"] != "COMPLETED":
                        raise BudgetStop("COACH_VALIDATION_FAILED:" + str(report.get("error")))
                    result["statistics"] = report["statistics"]
                else:
                    result["report_status"] = "NOT_RUN_LOGICAL_BUDGET"
                batch.save(status="RUNNING", routes=batch.routes)
        async with factory() as db:
            admissions = (await db.scalars(select(ModelAdmission).where(ModelAdmission.owner_key == f"user:{uid}"))).all()
            assert len(admissions) == len(batch.records)
            result = dict(rows=len(admissions), statuses=[r.status for r in admissions],
                charged_tokens=sum(r.charged_tokens for r in admissions), actual_tokens=sum(r.actual_tokens or 0 for r in admissions))
            batch.save(status="COMPLETED_WITH_BUDGET_LIMIT" if batch.logical == 14 else "COMPLETED", routes=batch.routes, quota=result)
        print("BATCH_FINISHED", flush=True)
    finally:
        await engine.dispose()


async def bounded(batch, database):
    try:
        async with asyncio.timeout(900):
            await run(batch, database)
    except BaseException as exc:
        batch.finalize_unknown()
        batch.stop_reason = batch.stop_reason or getattr(exc, "code", type(exc).__name__) + ":" + str(exc)[:180]
        batch.save(status="STOPPED", error_type=type(exc).__name__, routes=getattr(batch,"routes",[]))
        print("BATCH_STOPPED: " + batch.stop_reason, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    output = ROOT / ".runtime/acceptance/deepseek_v12_v13_live_20261019_attempt1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise SystemExit("Attempt already exists; refusing overwrite or another model batch")
    with (output.parent / "deepseek_v12_v13_live.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0"); lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            asyncio.run(bounded(Batch(output), args.database))
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
