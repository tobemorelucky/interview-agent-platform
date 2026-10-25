"""One authorized V2.2 batch: Replay history + LIVE decisions, never full Live history.

Acceptance instrumentation only. Reuses business services, prompts and quota ledger.
"""
import argparse
import asyncio
import json
import msvcrt
from pathlib import Path
import time
from uuid import uuid4

from accept_deepseek_v12_v13 import Batch, BudgetStop, MeteredProvider, TARGET
from prepare_v22_live import configured_routes
from review_v22_migration import inspect as inspect_database
from interview_api.core.config import settings
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.infrastructure.models.router import build_interview_router, ModelRouter
from interview_api.infrastructure.models.governance_models import ModelAdmission
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation, InterviewSessionQuestion
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import httpx

ROOT = Path(__file__).resolve().parents[3]
DATABASE = "interview_agent_v21_v22_live_prepare_20261023_a"


class V22Batch(Batch):
    def check(self):
        if self.stop_reason:
            raise BudgetStop(self.stop_reason)
        if time.monotonic() - self.start >= 480:
            raise BudgetStop("BATCH_DEADLINE")

    def save(self, **extra):
        value = dict(model=TARGET, model_mode="LIVE", data_mode="REPLAY_HISTORY_PLUS_LIVE_DECISION",
            limits=dict(logical=6, http=10, tokens=60000, seconds=480, repairs=1),
            logical_calls=self.logical, http_attempts=len(self.records),
            conservative_charged_tokens=self.charged, inflight_reserved_tokens=self.reserved,
            elapsed_seconds=round(time.monotonic()-self.start, 3), http=self.records,
            scenarios=self.results, stop_reason=self.stop_reason, **extra)
        self.output.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    async def on_request(self, request):
        self.check()
        body = json.loads(request.content)
        reserve = len(request.content) + int(body["max_tokens"]) + 1024
        if len(self.records) >= 10 or self.charged + self.reserved + reserve > 60000:
            raise BudgetStop("HTTP_OR_TOKEN_BUDGET")
        await super().on_request(request)
        # DEMO messages only; never headers, credentials, or hidden reasoning.
        self.records[-1]["request_messages"] = body["messages"]
        self.save(status="RUNNING")


class V22Provider(MeteredProvider):
    async def generate(self, messages, *, schema):
        repair = messages[0]["content"].startswith("Repair JSON syntax/structural fields only.")
        if repair and sum(bool(r["repair"]) for r in self.batch.records) >= 1:
            raise BudgetStop("BATCH_REPAIR_LIMIT")
        if not repair and self.batch.logical >= 6:
            raise BudgetStop("LOGICAL_BUDGET")
        result = await super().generate(messages, schema=schema)
        self.batch.records[-1]["business_structured_output"] = json.loads(result.text)
        self.batch.records[-1]["schema"] = schema
        self.batch.save(status="RUNNING")
        return result


async def history_fingerprints(factory):
    values = {}
    async with factory() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        for table, column in (("interview_sessions", "id"), ("interview_answers", "session_id"),
                              ("interview_evaluations", "session_id"), ("interview_session_questions", "session_id"),
                              ("interview_context_snapshots", "interview_session_id")):
            clause = "" if table == "interview_context_snapshots" else f"WHERE {column} NOT IN (4,9)"
            rows = await db.scalars(text(f"SELECT row_to_json(t)::text FROM {table} t {clause} ORDER BY id"))
            values[table] = digest(list(rows))
    return values


def answer_for(knowledge_id):
    # Test input follows the ACTUAL chosen topic; does not dictate the next strategy.
    answers = {
        "kafka_reliability": "[DEMO Live 测试回答] Kafka 消费失败可以重试，成功以后手动 ACK，保证消息不丢失。我还没有说明业务事务失败和重复投递的处理。",
        "redis": "[DEMO Live 测试回答] 我会使用 Redis 缓存热点查询，缓存未命中读取 MySQL，再写回缓存，并设置过期时间。更新时删除缓存，但还没有完整设计并发更新和缓存失效后的保护。",
        "java": "[DEMO Live 测试回答] Java 请求使用线程池执行，设置固定线程数和队列，异常记录日志。我尚未明确拒绝策略、共享状态同步和超时传播。",
        "mysql": "[DEMO Live 测试回答] MySQL 使用事务与唯一约束保证业务数据一致性，失败回滚，查询添加索引。我尚未明确并发隔离和锁冲突处理。",
    }
    suffix = knowledge_id.split(":")[-1]
    if suffix not in answers:
        return "[DEMO Live 测试回答] 我会先明确输入输出和业务边界，将核心写入放在事务中，记录失败并进行有限重试；具体并发与故障恢复细节还没有验证。"
    return answers[suffix]


async def verify_rows(factory, core, uid, sid, expected_answers):
    detail = await core.detail(sid, uid)
    async with factory() as db:
        answers = list(await db.scalars(select(InterviewAnswer).where(InterviewAnswer.session_id == sid)))
        evaluations = list(await db.scalars(select(InterviewEvaluation).where(InterviewEvaluation.session_id == sid)))
        questions = list(await db.scalars(select(InterviewSessionQuestion).where(InterviewSessionQuestion.session_id == sid)))
        admissions = list(await db.scalars(select(ModelAdmission).where(ModelAdmission.owner_key == f"user:{uid}")))
        assert len(answers) == len(evaluations) == expected_answers
        assert len(questions) == expected_answers + 1
        assert len({a.session_question_id for a in answers}) == len(answers)
        assert all(a.status == "COMPLETED" for a in answers)
        assert all(e.provider == "openai_compatible" and e.model == TARGET for e in evaluations)
        assert all(a.status == "SETTLED" for a in admissions), "UNSETTLED_QUOTA"
        return dict(status=detail["status"], state_version=detail["state_version"],
            questions=[dict(id=q.id, question=q.question, status=q.status, refs=q.candidate_refs_json) for q in questions],
            answers=[dict(id=a.id, status=a.status) for a in answers],
            evaluations=[dict(id=e.id, answer_id=e.answer_id, artifact=e.artifact_json, usage=e.token_usage_json) for e in evaluations],
            knowledge=detail["interview_plan_json"].get("adaptive_knowledge"),
            quota=[dict(key=a.call_key, route=a.route, model=a.model, status=a.status,
                        actual=a.actual_tokens, charged=a.charged_tokens) for a in admissions])


async def run(batch):
    routes = configured_routes()
    prepared = await inspect_database(DATABASE)
    assert prepared["revision"] == "0026"
    assert {s["id"] for s in prepared["prepared_sessions"]} == {4, 9}
    assert all(s["snapshot_verified"] and s["prompt_version"] == "2.2.0" for s in prepared["prepared_sessions"])
    engine = create_async_engine(make_url(settings.database_url).set(database=DATABASE))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    old = await history_fingerprints(factory)
    batch.save(status="PREFLIGHT", routes=routes, history_before=old)
    try:
        async with httpx.AsyncClient(event_hooks={"request": [batch.on_request], "response": [batch.on_response]}) as client:
            base = build_interview_router()
            router = ModelRouter({r: V22Provider(p, batch, r, client) for r, p in base.routes.items()}, concurrency=base.concurrency)
            core = MockInterviewService(factory, router)
            for name, uid, sid in (("A_VERIFIED", 1, 4), ("B_CONFLICTED", 2, 9)):
                before = await core.detail(sid, uid)
                pinned = before["context_snapshot"]
                result = dict(name=name, user_id=uid, session_id=sid, memory_guided_plan=pinned["memory_guided_plan"],
                    snapshot_fingerprint=digest(pinned), status="RUNNING")
                batch.results.append(result)
                batch.phase = name + ".opening"
                first = len(batch.records)
                opening = await core.start(sid, uid)
                result["opening"] = opening
                state = await core.detail(sid, uid)
                _, _, _, active, _ = await core.read_state(sid, uid)
                decision = active.candidate_refs_json["adaptive_decision"]
                result["opening_decision"] = decision
                await verify_rows(factory, core, uid, sid, 0)
                for r in batch.records[first:]: r["business_validation"] = "CONTROLLER_COMMITTED"
                batch.save(status="RUNNING")
                print(name + " opening committed", flush=True)
                answer = answer_for(decision["target_knowledge_id"])
                result["test_answer"] = answer
                batch.phase = name + ".answer_evaluation_next"
                first = len(batch.records)
                key = "V22-LIVE-" + uuid4().hex
                events = await core.answer(sid, uid, opening["question_id"], key, answer)
                state = await core.detail(sid, uid)
                result["next_decision"] = state["planner_trace_json"]["adaptive_decisions"][-1]
                result["events"] = events
                result["database"] = await verify_rows(factory, core, uid, sid, 1)
                assert state["context_snapshot"] == pinned, "SNAPSHOT_DRIFT"
                count = batch.logical
                assert events == await core.answer(sid, uid, opening["question_id"], key, answer)
                assert batch.logical == count, "IDEMPOTENCY_CALLED_MODEL"
                result["idempotency"] = True
                for r in batch.records[first:]: r["business_validation"] = "CONTROLLER_COMMITTED"
                result["status"] = "FIRST_ANSWER_AND_NEXT_COMMITTED"
                batch.save(status="RUNNING")
                print(name + " evaluation and next committed", flush=True)
        current = await history_fingerprints(factory)
        assert old == current, "HISTORICAL_DATA_CHANGED"
        quota = [q for result in batch.results for q in result["database"]["quota"]]
        assert len(quota) == len(batch.records), "QUOTA_COUNT_MISMATCH"
        assert sum(q["actual"] for q in quota) == sum(r["total_tokens"] for r in batch.records), "QUOTA_TOKEN_MISMATCH"
        batch.save(status="COMPLETED", routes=routes, history_before=old, history_after=current,
            historical_data_unchanged=True, quota_reconciled=True)
    finally:
        await engine.dispose()


async def bounded(batch):
    try:
        async with asyncio.timeout(480):
            await run(batch)
    except BaseException as exc:
        batch.finalize_unknown()
        batch.stop_reason = batch.stop_reason or getattr(exc, "code", type(exc).__name__)
        batch.save(status="STOPPED", error_type=type(exc).__name__, error_message=str(exc)[:300])
        print("BATCH_STOPPED " + batch.stop_reason, flush=True)
    else:
        print("BATCH_COMPLETED", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists() or not output.is_relative_to((ROOT / ".runtime/acceptance").resolve()):
        raise SystemExit("Use a new ignored attempt path")
    with (output.parent / "deepseek_v22_live.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            asyncio.run(bounded(V22Batch(output)))
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
