"""One authorized V2.1 batch: <=12 logical /24 HTTP /100k tokens /720 seconds.

Reuses the proven HTTP meter; no changes to credentials, routes or validators.
All business inputs are explicit DEMO fixtures in a dedicated database.
"""
import argparse
import asyncio
import json
import msvcrt
from pathlib import Path
import sys
import time
from uuid import uuid4

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
sys.path[:0] = [str(API / "src"), str(API / "tests"), str(API / "scripts")]
from accept_deepseek_v12_v13 import Batch, MeteredProvider, BudgetStop, TARGET
from interview_api.core.config import settings
from interview_api.infrastructure.models.router import build_interview_router, build_model_router, build_resume_profile_router, build_job_matching_router, build_interview_coach_router, ModelRouter
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
import httpx


class V21Batch(Batch):
    def check(self):
        if self.stop_reason:
            raise BudgetStop(self.stop_reason)
        if time.monotonic() - self.start >= 720:
            raise BudgetStop("BATCH_DEADLINE")

    def save(self, **extra):
        value = dict(model=TARGET, model_mode="LIVE", data_mode="LOCAL_FIXTURE_DEMO",
            limits=dict(logical=12, http=24, tokens=100000, seconds=720), logical_calls=self.logical,
            http_attempts=len(self.records), conservative_charged_tokens=self.charged,
            inflight_reserved_tokens=self.reserved, elapsed_seconds=round(time.monotonic()-self.start, 3),
            http=self.records, scenarios=self.results, stop_reason=self.stop_reason, **extra)
        self.output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    async def on_request(self, request):
        body = json.loads(request.content)
        estimate = len(request.content) + int(body["max_tokens"]) + 1024
        if len(self.records) >= 24 or self.charged + self.reserved + estimate > 100000:
            raise BudgetStop("HTTP_OR_TOKEN_BUDGET")
        await super().on_request(request)


class V21Provider(MeteredProvider):
    async def generate(self, messages, *, schema):
        repair = messages[0]["content"].startswith("Repair JSON syntax/structural fields only.")
        if not repair and self.batch.logical >= 12:
            raise BudgetStop("LOGICAL_BUDGET")
        return await super().generate(messages, schema=schema)


async def run(batch, database):
    if not database.startswith("interview_agent_v21_") or not database.replace("_", "").isalnum():
        raise BudgetStop("ISOLATED_DATABASE_REQUIRED")
    # Inspect every configured route without printing secrets or changing defaults.
    inspected = [build_interview_router(), build_model_router(), build_resume_profile_router(),
        build_job_matching_router(), build_interview_coach_router()]
    routes = [{"route": route, "provider": p.provider_name, "model": p.model_name,
        "thinking": p.thinking_type} for router in inspected for route, p in router.routes.items()]
    if any(r["model"] != TARGET for r in routes):
        raise BudgetStop("CONFIGURED_ROUTES_NOT_ALL_DEEPSEEK")
    base = inspected[0]
    if any(p.model_name != TARGET for p in base.fallback_routes.values()):
        raise BudgetStop("UNEXPECTED_FALLBACK_CONFIGURATION")
    batch.save(status="PREFLIGHT", routes=routes)
    url = make_url(settings.database_url).set(database=database)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    from test_m4_interview import seed
    from interview_api.modules.public_career.models import JobDescriptionVersion, JobRequirement
    from interview_api.modules.experience.models import InterviewQuestion
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.infrastructure.models.governance_models import ModelAdmission
    from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation
    cases = [dict(name="A_JAVA", skill="java_backend", requirement="Kafka 消息消费的 ACK、业务幂等和数据库事务边界",
        public_question="Kafka 消费者应如何处理业务事务和 ACK？", topic="kafka_reliability",
        answers=["消费者收到 Kafka 消息后处理订单，然后 ACK，我没有考虑数据库提交失败和重试。",
            "我会把业务唯一键与订单写入放在同一个数据库事务内，事务提交成功后再 ACK。重复投递先依据唯一键查询原结果；提交前失败则回滚不确认，提交后 ACK 丢失导致重投时返回原结果，避免重复落库。",
            "DEMO 测试回答：我会先说明当前问题的实现边界，并结合事务提交结果、业务唯一约束、超时重试和失败恢复给出一个具体处理过程。"]),
        dict(name="B_AGENT", skill="ai_agent_application", requirement="Agent 工具调用的状态管理、工具权限和失败恢复；Agent 结构化决策与程序执行边界",
        public_question="Agent 选择工具后如何保证权限和失败恢复？", topic="agent",
        answers=["Agent 让模型选择工具后就执行，失败会再次请求，我没有设计权限边界和状态校验。",
            "模型仅返回白名单工具和参数，程序先验证 Schema、当前用户权限及会话版本，再在事务外执行。用业务幂等键关联执行记录，超时结果标记未知，不直接重复写操作；迟到结果经过版本栅栏校验，只读工具才允许有限重试。",
            "DEMO 测试回答：先区分模型建议与程序控制，给出明确输入输出、版本校验和有界重试，再说明失败恢复与具体工程取舍。"])]
    try:
        async with httpx.AsyncClient(timeout=180, event_hooks={"request": [batch.on_request], "response": [batch.on_response]}) as client:
            router = ModelRouter({r: V21Provider(p, batch, r, client) for r, p in base.routes.items()}, concurrency=base.concurrency)
            for case in cases:
                batch.check()
                _, _, _, uid, old = await seed(factory, public=False)
                snap = old["context_snapshot"]
                async with factory() as db:
                    jd = JobDescriptionVersion(job_id=snap["job_id"], version_number=2, raw_text="DEMO fixture: " + case["requirement"], publication_status="PUBLISHED")
                    db.add(jd)
                    await db.flush()
                    db.add(JobRequirement(job_description_version_id=jd.id, statement=case["requirement"], requirement_type="SKILL"))
                    db.add(InterviewQuestion(company_id=snap["company_id"], job_id=snap["job_id"],
                        question=case["public_question"], review_status="APPROVED", category="DEMO 技术", difficulty="MEDIUM"))
                    await db.commit()
                core = MockInterviewService(factory, router)
                detail = await core.create(uid, CreateSessionRequest(company_id=snap["company_id"], job_id=snap["job_id"],
                    interviewer_skill_id=case["skill"], adaptive_interview=True, question_count=3,
                    title="DEMO V2.1 LIVE " + case["name"]))
                sid = detail["id"]
                result = dict(name=case["name"], user_id=uid, session_id=sid, status="RUNNING", rounds=[])
                batch.results.append(result)
                batch.phase = case["name"] + ".opening"
                q = await core.start(sid, uid)
                result["opening"] = q["question"]
                for index, answer in enumerate(case["answers"]):
                    batch.phase = case["name"] + f".answer{index+1}"
                    key = uuid4().hex
                    events = await core.answer(sid, uid, q["question_id"], key, answer)
                    read = await core.detail(sid, uid)
                    trace = read["planner_trace_json"]["adaptive_decisions"][-1]
                    row = dict(question=q["question"], answer=answer, evaluation=events[0][1], decision=trace,
                        observations=read["interview_plan_json"]["adaptive_knowledge"])
                    result["rounds"].append(row)
                    count = batch.logical
                    assert events == await core.answer(sid, uid, q["question_id"], key, answer)
                    assert count == batch.logical
                    assert read["context_snapshot"] == detail["context_snapshot"]
                    batch.save(status="RUNNING", routes=routes)
                    if read["status"] == "COMPLETED": break
                    q = read["current_question"]
                result["status"] = read["status"]
                result["statistics"] = read["completion_json"]
                async with factory() as db:
                    answers = list((await db.scalars(select(InterviewAnswer).where(InterviewAnswer.session_id == sid))).all())
                    evaluations = list((await db.scalars(select(InterviewEvaluation).where(InterviewEvaluation.session_id == sid))).all())
                    assert len(answers) == len(evaluations) == 3
                    assert all(a.status == "COMPLETED" for a in answers)
                    admissions = list((await db.scalars(select(ModelAdmission).where(ModelAdmission.owner_key == f"user:{uid}"))).all())
                    result["quota"] = dict(rows=len(admissions), charged_tokens=sum(a.charged_tokens for a in admissions), statuses=[a.status for a in admissions])
                assert read["status"] == "COMPLETED"
                batch.save(status="RUNNING", routes=routes)
            batch.save(status="COMPLETED", routes=routes, reports="NOT_RUN_BUDGET_PRIORITIZED_DECISIONS")
    finally:
        await engine.dispose()


async def bounded_run(batch, database):
    try:
        async with asyncio.timeout(720): await run(batch, database)
    except BaseException as exc:
        batch.finalize_unknown()
        batch.stop_reason = batch.stop_reason or getattr(exc, "code", type(exc).__name__)
        batch.save(status="STOPPED", error_type=type(exc).__name__)
        print("BATCH_STOPPED " + batch.stop_reason, flush=True)
    else:
        print("BATCH_COMPLETED", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    output = ROOT / ".runtime/acceptance/deepseek_v21_live_20261022_attempt1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists(): raise SystemExit("Attempt exists; never overwrite or retry a LIVE batch")
    with (output.parent / "deepseek_v21_live.lock").open("a+b") as lock:
        if lock.tell() == 0: lock.write(b"0"); lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try: asyncio.run(bounded_run(V21Batch(output), args.database))
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
