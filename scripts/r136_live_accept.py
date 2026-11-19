"""R136 acceptance driver. This round's budget is a DELTA of 23 HTTP / 182,824 tokens,
carried over from R135's 27/400k; no Search/Fetch, no automatic retry.

Baseline is the max step/run id at process start so only R136-new calls count.
The wire tap also records the R136 candidate binding: whether a retrieved reviewed
question surfaced its wording + candidate_id on a selectable candidate.

Usage:
  R136_DATABASE_URL=... python scripts/r136_live_accept.py precheck
  R136_DATABASE_URL=... python scripts/r136_live_accept.py review   # reviewed experience used as question source
  R136_DATABASE_URL=... python scripts/r136_live_accept.py noharness # no-experience job falls back to Skill
  R136_DATABASE_URL=... python scripts/r136_live_accept.py full      # multi-turn + report + provenance
  R136_DATABASE_URL=... python scripts/r136_live_accept.py ledger
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))
if not os.environ.get("R136_DATABASE_URL"):
    raise SystemExit("set R136_DATABASE_URL to the isolated acceptance database")
os.environ["DATABASE_URL"] = os.environ["R136_DATABASE_URL"]

import interview_api.modules.models  # noqa: F401
import interview_api.modules.job_target.models  # noqa: F401
import interview_api.modules.purge.models  # noqa: F401
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from interview_api.core.config import settings
from interview_api.infrastructure.models.governance_models import ModelAdmission
from interview_api.infrastructure.models.router import build_interview_router
from interview_api.modules.experience.models import (
    ExperienceAgentRun, ExperienceAgentStepRun, InterviewExperience)
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview_report.service import InterviewReportService

HTTP_CAP, TOKEN_CAP = 23, 182_824  # this round's delta budget (R135 used 27/400k)
OWNER, UID = "user:21", 21
OUT = Path(__file__).resolve().parents[1] / "data/r136_audit"
OUT.mkdir(exist_ok=True)

ANSWERS = {
    "agent": "Harness 这边我把执行拆成规划、工具调用、结果校验和状态持久化四段。每个工具调用都有白名单和参数校验，"
             "执行结果统一转成 evidence，再由 verifier 检查答案是否引用了真实证据。多 Agent 并行改同一个文件时，"
             "我用文件级租约加版本号做冲突检测，冲突就回滚重跑。评估上看任务成功率、修复正确率和平均重试次数。",
    "rag": "我们的 RAG 是 BGE-M3 向量召回加 BM25 的关键词召回，两路各取 50 条，用 RRF 按排名融合，再截断到 30 条交给 reranker。"
           "生成前会把证据按文档和段落拼进 prompt，要求答案带引用；引用不存在的 evidence id 会被后端校验拒绝。"
           "评估上离线看 Recall@K 和引用准确率，线上看采纳率和无证据回答率。",
    "java": "这个秒杀场景里，库存扣减先走 Redis 的 Lua 脚本做原子校验，判断库存和一人一单的 Set，扣减成功后再异步落 MySQL 订单。"
            "为了防止超卖，库存 key 用 Hash 存总量和已售，Lua 里先判断再 decrement；数据库侧再用唯一索引和乐观锁兜底。"
            "失败会回滚 Redis 并记录补偿任务。",
    "vague": "这个我大概了解过，具体细节记不太清了，之前项目里应该用过一段时间。",
}
CONFIG = {
    "agent": dict(company_id=6, job_id=31, skill="ai_agent_application", answer="agent"),
    "rag": dict(company_id=8, job_id=34, skill="rag_retrieval", answer="rag"),
    "java": dict(company_id=18, job_id=46, skill="java_backend", answer="java"),
}


def _wire_summary(messages, schema):
    """Extract the decision-relevant projection from the real model input."""
    try:
        packet = json.loads(messages[-1]["content"])["input"]
    except Exception:
        return None
    adaptive = packet.get("adaptive") or {}
    snapshot = packet.get("snapshot") or {}
    feedback = packet.get("tool_feedback") or []
    fb = []
    for f in feedback:
        data = (f.get("data") or {})
        items = data.get("items") or []
        fb.append(dict(tool=f.get("tool"), status=f.get("status"), code=f.get("code"),
            question_refs=[q.get("ref") for it in items for q in it.get("questions", [])],
            candidate_ids=[q.get("candidate_id") for it in items for q in it.get("questions", []) if q.get("candidate_id")]))
    ca = snapshot.get("context_availability") or {}
    cands = adaptive.get("candidates") or []
    pq = [dict(id=c.get("candidate_id"), ref=(c.get("source") or {}).get("ref"),
               strategies=c.get("strategies"),
               reported_question=c.get("reported_question"),
               reported_source_ref=c.get("reported_source_ref"),
               review_evidence=c.get("review_evidence")) for c in cands
          if (c.get("source") or {}).get("ref", "").startswith("public_question:")]
    return dict(schema=schema.get("title"),
        active_gaps=ca.get("active_gaps"), reviewed=ca.get("reviewed_experiences"),
        tool_feedback=fb,
        candidate_count=len(cands),
        pq_candidates=pq,
        bound_answerable=sum(1 for c in pq if c.get("reported_question")))


def wire_tap(router, evidence):
    for route in ("interview_main", "answer_evaluation"):
        provider = router.primary(route)
        original = provider.generate

        async def logged(messages, *, schema, _original=original, _route=route):
            evidence["wire"].append(_wire_summary(messages, schema))
            return await _original(messages, schema=schema)

        provider.generate = logged
    return router


async def baseline(factory):
    async with factory() as db:
        step_id = await db.scalar(select(func.max(ExperienceAgentStepRun.id))) or 0
        run_id = await db.scalar(select(func.max(ExperienceAgentRun.id))) or 0
    return dict(step_id=step_id, run_id=run_id)


async def usage(factory, base):
    async with factory() as db:
        steps = (await db.scalars(select(ExperienceAgentStepRun).where(
            ExperienceAgentStepRun.id > base["step_id"],
            ExperienceAgentStepRun.step_name != "main_harness_tool").order_by(ExperienceAgentStepRun.id))).all()
        tools = (await db.scalars(select(ExperienceAgentStepRun).where(
            ExperienceAgentStepRun.id > base["step_id"],
            ExperienceAgentStepRun.step_name == "main_harness_tool").order_by(ExperienceAgentStepRun.id))).all()
        runs = (await db.scalars(select(ExperienceAgentRun).where(
            ExperienceAgentRun.id > base["run_id"]).order_by(ExperienceAgentRun.id))).all()
    http = tokens = 0
    model_steps = []
    for step in steps:
        inv = (step.output_json or {}).get("model_invocations") or []
        http += len(inv) or 1
        tokens += (step.token_usage_json or {}).get("total_tokens", 0) or 0
        model_steps.append(dict(id=step.id, step=step.step_name, inv=len(inv) or 1,
                                tokens=(step.token_usage_json or {}).get("total_tokens"),
                                main_action=(step.output_json or {}).get("main_action")))
    tool_rows = [dict(id=s.id, tool=(s.output_json or {}).get("tool"),
                      arguments=(s.output_json or {}).get("arguments"),
                      result=(s.output_json or {}).get("result")) for s in tools]
    run_rows = [dict(id=r.id, graph=r.graph_name, status=r.status, error=r.error_message,
                     audit=(r.output_json or {}).get("tool_audit"),
                     terminal=(r.output_json or {}).get("terminal")) for r in runs]
    return dict(http=http, tokens=tokens, steps=model_steps, tools=tool_rows, runs=run_rows)


async def make_session(svc, cfg, *, count=2, harness=True, opening="MODEL"):
    req = CreateSessionRequest(company_id=cfg["company_id"], job_id=cfg["job_id"],
        question_count=count, interviewer_skill_id=cfg["skill"], adaptive_interview=True,
        main_harness=harness, interviewer_selection_source="USER_SELECTED")
    created = await svc.create(UID, req)
    sid = created["id"] if isinstance(created, dict) else created.id
    started = await svc.start(sid, UID, opening=opening)
    return sid, started


async def answer_once(svc, sid, answer_key):
    detail = await svc.detail(sid, UID)
    qid = detail.get("active_question_id")
    if not qid:
        return None
    events = dict(await svc.answer(sid, UID, qid, str(uuid4()), ANSWERS[answer_key]))
    q = events.get("question") or {}
    ev = events.get("evaluation") or {}
    return dict(question_id=qid, question=q.get("question"), source_ref=q.get("source_ref"),
                scores={k: ev.get(k) for k in ("technical_correctness", "coverage", "depth", "relevance", "communication")})


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _usage_summary(u):
    """Compact, report-ready summary of usage + reviewed-source classification."""
    reviewed = {}
    for r in u.get("runs", []):
        a = r.get("audit") or {}
        if a:
            reviewed[str(r["id"])] = {k: a.get(k) for k in (
                "reviewed_search_called", "reviewed_retrieved_count", "used_tool_result",
                "reviewed_usage", "selection_source_ref")}
    return dict(http=u["http"], tokens=u["tokens"],
                tools=len(u.get("tools", [])), reviewed_runs=reviewed)


async def run_scenario(factory, base, name, scenarios, report_sid=None):
    ev = dict(scenario=name, started=datetime.now(timezone.utc).isoformat(), wire=[], sessions=[])
    router = wire_tap(build_interview_router(), ev)
    svc = MockInterviewService(factory, router)
    before = await usage(factory, base)
    for cfg, turns, key in scenarios:
        cur = await usage(factory, base)
        if cur["http"] + 3 > HTTP_CAP or cur["tokens"] + 40_000 > TOKEN_CAP:
            ev["stopped"] = "BUDGET"; break
        try:
            sid, started = await make_session(svc, cfg, count=turns)
            entry = dict(session=sid, job=cfg["job_id"], skill=cfg["skill"],
                         opening=(started or {}).get("question") if isinstance(started, dict) else None,
                         turns=[])
            ev["sessions"].append(entry)
            print("SESSION", name, sid, flush=True)
            for t in range(turns):
                r = await answer_once(svc, sid, key)
                if r:
                    entry["turns"].append(r)
                cur = await usage(factory, base)
                print("LEDGER", name, cur["http"], "/", HTTP_CAP, cur["tokens"], "/", TOKEN_CAP, flush=True)
                if cur["http"] >= HTTP_CAP or cur["tokens"] >= TOKEN_CAP:
                    ev["stopped"] = "BUDGET"; break
            det = await svc.detail(sid, UID)
            entry["final_status"] = det.get("status")
            if report_sid and det.get("status") in ("COMPLETED", "ENDED"):
                try:
                    await InterviewReportService(factory).generate(sid, UID)
                    entry["report"] = "GENERATED"
                except Exception as exc:
                    entry["report"] = type(exc).__name__ + ":" + str(exc)[:150]
        except Exception as exc:
            ev.setdefault("errors", []).append(dict(type=type(exc).__name__, message=str(exc)[:400]))
            print("ERROR", name, type(exc).__name__, str(exc)[:300], flush=True)
            break  # never auto-retry a possibly billed request
    ev["usage"] = await usage(factory, base)
    ev["delta"] = dict(http=ev["usage"]["http"] - before["http"],
                       tokens=ev["usage"]["tokens"] - before["tokens"])
    ev["summary"] = _usage_summary(ev["usage"])
    ev["finished"] = datetime.now(timezone.utc).isoformat()
    save(f"r136_{name}.json", ev)
    print("DONE", name, json.dumps(ev["delta"], ensure_ascii=False), "wire", len(ev["wire"]), flush=True)
    return ev


async def precheck(factory):
    async with factory() as db:
        exp = (await db.scalars(select(InterviewExperience))).all()
        for e in exp:
            print("EXPERIENCE", e.id, e.company, e.position, e.review_status, e.reliability_score,
                  "company", e.company_id, "job", e.job_id)
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        used = (await db.execute(select(func.count(), func.coalesce(func.sum(ModelAdmission.charged_tokens), 0))
            .where(ModelAdmission.owner_key == OWNER, ModelAdmission.created_at >= today))).one()
    router = build_interview_router()
    p = router.primary("interview_main")
    print("ROUTE", settings.llm_provider, p.provider_name, p.model_name,
          "key_set", bool(str(getattr(p, "api_key", "") or "").strip()))
    print("QUOTA_TODAY calls", used[0], "charged", used[1],
          "remaining", settings.model_user_daily_calls - used[0], settings.model_user_daily_tokens - used[1])
    print("CAP", HTTP_CAP, TOKEN_CAP)


async def main():
    cmd = sys.argv[1]
    factory = async_sessionmaker(create_async_engine(os.environ["R136_DATABASE_URL"]), expire_on_commit=False)
    if cmd == "precheck":
        await precheck(factory)
    elif cmd == "ledger":
        print(json.dumps(await usage(factory, await baseline(factory)), ensure_ascii=False, default=str)[:2000])
    else:
        base = await baseline(factory)
        if cmd == "review":
            # Agent Harness job with a reviewed experience; the answer shares the
            # reported topics (Agent eval / multi-agent) so a bound, answerable
            # reviewed candidate is a genuinely good choice, not a forced one.
            await run_scenario(factory, base, "review", [(CONFIG["agent"], 2, "agent")])
        elif cmd == "noharness":
            # No reviewed experience for this exact job; must fall back to Skill/JD.
            await run_scenario(factory, base, "noharness", [(CONFIG["rag"], 2, "rag")])
        elif cmd == "full":
            await run_scenario(factory, base, "full", [(CONFIG["agent"], 4, "agent")], report_sid=True)
        else:
            raise SystemExit("unknown command")


asyncio.run(main())
