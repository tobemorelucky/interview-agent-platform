"""R134 real-model acceptance driver. Budget-hard, no retry, no Search/Fetch.

Usage:
  R134_DATABASE_URL=... python scripts/r134_live_accept.py precheck
  R134_DATABASE_URL=... python scripts/r134_live_accept.py run
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))
if not os.environ.get("R134_DATABASE_URL"):
    raise SystemExit("set R134_DATABASE_URL to the isolated acceptance database")
os.environ["DATABASE_URL"] = os.environ["R134_DATABASE_URL"]

import interview_api.modules.models  # noqa: F401
import interview_api.modules.job_target.models  # noqa: F401
import interview_api.modules.purge.models  # noqa: F401
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.config import settings
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.infrastructure.models.governance_models import ModelAdmission
from interview_api.infrastructure.models.router import build_interview_router
from interview_api.modules.experience.models import (
    ExperienceAgentRun, ExperienceAgentStepRun, InterviewExperience)
from interview_api.modules.public_career.models import JobDescriptionVersion  # noqa: F401
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview_report.service import InterviewReportService

HTTP_CAP, TOKEN_CAP = 15, 120_000
OWNER = "user:21"
UID = 21
OUT = Path(__file__).resolve().parents[1] / "data/r134_audit"
OUT.mkdir(exist_ok=True)

ANSWERS = {
    "agent": "Harness 这边我把执行拆成规划、工具调用、结果校验和状态持久化四段。每个工具调用都有白名单和参数校验，"
             "执行结果统一转成 evidence，再由 verifier 检查答案是否引用了真实证据。多 Agent 并行改同一个文件时，"
             "我用文件级租约加版本号做冲突检测，冲突就回滚重跑。评估上看任务成功率、修复正确率和平均重试次数。",
    "rag": "我们的 RAG 是 BGE-M3 向量召回加 BM25 的关键词召回，两路各取 50 条，用 RRF 按排名融合，再截断到 30 条交给 reranker。"
           "生成前会把证据按文档和段落拼进 prompt，要求答案带引用；引用不存在的 evidence id 会被后端校验拒绝。"
           "评估上离线看 Recall@K 和引用准确率，线上看采纳率和无证据回答率。",
}
CONFIG = {
    "A_with_experience": dict(company_id=6, job_id=31, skill="ai_agent_application", answer="agent", count=2),
    "B_no_experience": dict(company_id=8, job_id=34, skill="rag_retrieval", answer="rag", count=2),
}


def wire_tap(router, evidence, watched=()):
    """Observe the real model input without spending or altering a call."""
    for route in ("interview_main", "answer_evaluation"):
        provider = router.primary(route)
        original = provider.generate

        async def logged(messages, *, schema, _original=original, _route=route):
            blob = json.dumps(messages, ensure_ascii=False)
            evidence["wire"].append(dict(route=_route, schema=schema.get("title"),
                bytes=len(blob.encode("utf-8")),
                leaked_reviewed_text=[t[:24] for t in watched if t in blob]))
            return await _original(messages, schema=schema)

        provider.generate = logged
    return router


async def baseline(factory):
    async with factory() as db:
        step_id = await db.scalar(select(func.max(ExperienceAgentStepRun.id))) or 0
        run_id = await db.scalar(select(func.max(ExperienceAgentRun.id))) or 0
        admissions = await db.scalar(select(func.count()).select_from(ModelAdmission))
    return dict(step_id=step_id, run_id=run_id, admissions=admissions)


async def usage(factory, base):
    """Count real paid HTTP invocations and tokens booked after the baseline."""
    async with factory() as db:
        steps = (await db.scalars(select(ExperienceAgentStepRun).where(
            ExperienceAgentStepRun.id > base["step_id"],
            ExperienceAgentStepRun.step_name != "main_harness_tool").order_by(
            ExperienceAgentStepRun.id))).all()
        tools = (await db.scalars(select(ExperienceAgentStepRun).where(
            ExperienceAgentStepRun.id > base["step_id"],
            ExperienceAgentStepRun.step_name == "main_harness_tool").order_by(
            ExperienceAgentStepRun.id))).all()
        runs = (await db.scalars(select(ExperienceAgentRun).where(
            ExperienceAgentRun.id > base["run_id"],
            ExperienceAgentRun.graph_name == "interview_main_harness").order_by(
            ExperienceAgentRun.id))).all()
    http, tokens = 0, 0
    model_steps = []
    for step in steps:
        invocations = (step.output_json or {}).get("model_invocations") or []
        count = len(invocations) or 1
        http += count
        for key, value in (step.token_usage_json or {}).items():
            if key == "total_tokens" and isinstance(value, int):
                tokens += value
        model_steps.append(dict(id=step.id, step=step.step_name, invocations=count,
                                provider=step.provider_name, model=step.model_name,
                                usage=step.token_usage_json))
    tool_rows = [dict(id=s.id, tool=(s.output_json or {}).get("tool"),
                      arguments=(s.output_json or {}).get("arguments"),
                      result=(s.output_json or {}).get("result"))
                 for s in tools]
    run_rows = [dict(id=r.id, status=r.status, error=r.error_message,
                     audit=(r.output_json or {}).get("tool_audit"),
                     terminal=(r.output_json or {}).get("terminal"),
                     artifact=(r.output_json or {}).get("validated_artifact"))
                for r in runs]
    return dict(http=http, tokens=tokens, steps=model_steps, tools=tool_rows, runs=run_rows)


async def precheck(factory):
    async with factory() as db:
        experience = (await db.scalars(select(InterviewExperience))).all()
        for row in experience:
            print("EXPERIENCE", row.id, row.company, row.position, row.review_status,
                  row.reliability_score, "company", row.company_id, "job", row.job_id,
                  "source", row.source_url, "reviewed", bool(row.reviewed_at))
        for table, cond in (("interview_questions", "experience_id=1"), ("jobs", "id=31"), ("jobs", "id=34")):
            count = await db.scalar(text(f"select count(*) from {table} where {cond}"))
            print("COUNT", table, cond, count)
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        used = (await db.execute(select(func.count(), func.coalesce(func.sum(ModelAdmission.charged_tokens), 0))
            .where(ModelAdmission.owner_key == OWNER, ModelAdmission.created_at >= today))).one()
    engine = build_interview_router()
    provider = engine.primary("interview_main")
    print("ROUTE", settings.llm_provider, provider.provider_name, provider.model_name,
          "key_set", bool(str(getattr(provider, "api_key", "") or "").strip()))
    print("QUOTA_TODAY", OWNER, "calls", used[0], "charged_tokens", used[1],
          "remaining_calls", settings.model_user_daily_calls - used[0],
          "remaining_tokens", settings.model_user_daily_tokens - used[1])
    print("BUDGET", HTTP_CAP, TOKEN_CAP)


async def run(factory):
    evidence = dict(started=datetime.now(timezone.utc).isoformat(), wire=[],
                    scenarios={}, errors=[])
    base = await baseline(factory)
    router = wire_tap(build_interview_router(), evidence,
                      watched=("怎么理解 Harness", "agent 修复代码的效果是怎么评判的"))
    svc = MockInterviewService(factory, router)
    report_svc = InterviewReportService(factory)

    for name, cfg in CONFIG.items():
        current = await usage(factory, base)
        if current["http"] >= HTTP_CAP or current["tokens"] >= TOKEN_CAP:
            evidence["stopped"] = "BUDGET_REACHED_BEFORE_" + name
            break
        print("SCENARIO", name, "http", current["http"], "tokens", current["tokens"], flush=True)
        try:
            req = CreateSessionRequest(company_id=cfg["company_id"], job_id=cfg["job_id"],
                question_count=cfg["count"], interviewer_skill_id=cfg["skill"],
                adaptive_interview=True, main_harness=True,
                interviewer_selection_source="USER_SELECTED")
            created = await svc.create(UID, req)
            sid = created["id"] if isinstance(created, dict) else created.id
            evidence["scenarios"][name] = dict(session=sid, job=cfg["job_id"], skill=cfg["skill"],
                                               harness=True, events=[])
            print("SESSION", sid, flush=True)
            started = await svc.start(sid, UID, opening="MODEL")
            evidence["scenarios"][name]["events"].append(dict(kind="start", detail=str(started)[:300]))
            for turn in range(cfg["count"]):
                detail = await svc.detail(sid, UID)
                qid = detail.get("active_question_id")
                if not qid or detail.get("status") in ("COMPLETED", "ENDED"):
                    break
                events = dict(await svc.answer(sid, UID, qid, str(uuid4()), ANSWERS[cfg["answer"]]))
                summary = dict(turn=turn + 1, question_id=qid,
                    question=(events.get("question") or {}).get("question"),
                    source_ref=(events.get("question") or {}).get("source_ref"),
                    evaluation={k: (events.get("evaluation") or {}).get(k) for k in
                                ("technical_correctness", "coverage", "depth", "relevance", "communication")},
                    done=bool(events.get("done")))
                evidence["scenarios"][name]["events"].append(summary)
                print("TURN", name, turn + 1, json.dumps(summary, ensure_ascii=False)[:400], flush=True)
                current = await usage(factory, base)
                print("LEDGER", current["http"], "/", HTTP_CAP, current["tokens"], "/", TOKEN_CAP, flush=True)
                if current["http"] >= HTTP_CAP or current["tokens"] >= TOKEN_CAP:
                    evidence["stopped"] = "BUDGET_REACHED_DURING_" + name
                    break
            detail = await svc.detail(sid, UID)
            evidence["scenarios"][name]["final"] = dict(status=detail.get("status"),
                questions=len(detail.get("questions") or []))
            if name == "A_with_experience" and detail.get("status") in ("COMPLETED", "ENDED"):
                try:
                    await report_svc.generate(sid, UID)
                    evidence["scenarios"][name]["report"] = "GENERATED"
                except Exception as exc:
                    evidence["scenarios"][name]["report"] = type(exc).__name__ + ":" + str(exc)[:200]
        except Exception as exc:
            evidence["errors"].append(dict(scenario=name, type=type(exc).__name__,
                                           message=str(exc)[:500]))
            print("ERROR", name, type(exc).__name__, str(exc)[:300], flush=True)
            break  # no automatic retry of a possibly billed request
    evidence["usage"] = await usage(factory, base)
    evidence["finished"] = datetime.now(timezone.utc).isoformat()
    (OUT / "live_evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2,
                                                       default=str), encoding="utf-8")
    print("FINAL", json.dumps({k: evidence.get(k) for k in ("usage", "stopped", "errors")},
                              ensure_ascii=False, default=str)[:1500], flush=True)


async def retest(factory):
    """Focused follow-up after the topic-alias fix; at most three paid calls."""
    evidence = dict(started=datetime.now(timezone.utc).isoformat(), wire=[],
                    purpose="R134 P1 retest: reviewed-experience tool scope after topic normalization")
    base = await baseline(factory)
    before = await usage(factory, base)
    if before["http"] + 3 > HTTP_CAP or before["tokens"] + 45_000 > TOKEN_CAP:
        print("RETEST_SKIPPED_BUDGET", before["http"], before["tokens"], flush=True)
        return
    router = wire_tap(build_interview_router(), evidence, watched=("怎么理解 Harness",))
    svc = MockInterviewService(factory, router)
    cfg = CONFIG["A_with_experience"]
    req = CreateSessionRequest(company_id=cfg["company_id"], job_id=cfg["job_id"],
        question_count=2, interviewer_skill_id=cfg["skill"], adaptive_interview=True,
        main_harness=True, interviewer_selection_source="USER_SELECTED")
    created = await svc.create(UID, req)
    sid = created["id"] if isinstance(created, dict) else created.id
    evidence["session"] = sid
    print("RETEST_SESSION", sid, flush=True)
    await svc.start(sid, UID, opening="MODEL")
    detail = await svc.detail(sid, UID)
    qid = detail.get("active_question_id")
    events = dict(await svc.answer(sid, UID, qid, str(uuid4()), ANSWERS["agent"]))
    evidence["events"] = dict(question=(events.get("question") or {}).get("question"),
                              question_id=qid,
                              evaluation=(events.get("evaluation") or {}).get("technical_correctness"))
    evidence["usage"] = await usage(factory, base)
    evidence["finished"] = datetime.now(timezone.utc).isoformat()
    (OUT / "live_retest.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2,
                                                     default=str), encoding="utf-8")
    print("RETEST_DONE", json.dumps(dict(session=sid, delta_http=evidence["usage"]["http"] - before["http"],
        delta_tokens=evidence["usage"]["tokens"] - before["tokens"],
        tools=evidence["usage"]["tools"], runs=[dict(id=r["id"], status=r["status"],
        error=r["error"], audit=r["audit"]) for r in evidence["usage"]["runs"]],
        event=evidence["events"]), ensure_ascii=False, default=str)[:2400], flush=True)


async def main():
    cmd = sys.argv[1]
    factory = async_sessionmaker(create_async_engine(os.environ["R134_DATABASE_URL"]),
                                 expire_on_commit=False)
    if cmd == "precheck":
        await precheck(factory)
    elif cmd == "run":
        await run(factory)
    elif cmd == "retest":
        await retest(factory)
    else:
        raise SystemExit("unknown command")


asyncio.run(main())
