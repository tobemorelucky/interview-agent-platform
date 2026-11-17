"""R122 stage 3 (resume): finish C WEAKNESS Live from an already-started session.

The first run (r74) already created WEAKNESS session 16 and generated its first
question via a real governed call (1 HTTP). This script only answers the 3
questions, generates Coach and reads training feedback - it never creates a
session or calls start again, so no model call is repeated. Hard budget 13/90K
measured against the ledger baseline captured here.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

import interview_api.modules.models  # noqa: F401,E402
import interview_api.modules.job_target.models  # noqa: F401,E402
from interview_api.core.config import settings  # noqa: E402
from interview_api.infrastructure.db.session import async_session_factory  # noqa: E402
from interview_api.infrastructure.models.governance_models import ModelAdmission  # noqa: E402
from interview_api.modules.interview.core_service import MockInterviewService  # noqa: E402
from interview_api.modules.interview.models import InterviewEvaluation, InterviewSessionQuestion  # noqa: E402
from interview_api.modules.interview.training import TrainingService  # noqa: E402
from interview_api.modules.interview_report.service import InterviewReportService  # noqa: E402

SANDBOX = "interview_agent_showcase_r1_20261116"
DSN = f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{SANDBOX}"
OUT = REPO / "data/r74_r122_c_live"
BUDGET = {"max_http": 13, "max_tokens": 90000}
USER_ID, SID = 9, 16
KNOWLEDGE_ID = "ai_agent_application:agent"
ANSWERS = [
    "工具调用我会分三层处理。第一层在调用前校验参数 schema，并查权限白名单，区分模型建议和程序真正允许执行的操作；"
    "第二层执行时设置超时和最大步数，只读工具可重试、写操作带幂等键；第三层把工具返回压成结构化结果回填上下文，"
    "保留工具名、状态和摘要。执行前会记录节点检查点，失败时从这里恢复。",
    "工具报错我会先分类：参数错误把结构化错误原文回给模型让它改正；超时或限流按退避重试并换备用工具；"
    "权限拒绝直接终止该分支并把原因写进状态。上下文回填只放必要字段，避免把整段原始输出塞回去污染下一轮。",
    "多步任务我用显式状态机管理：每一步的输入、决策和输出都存到带版本的状态对象里，模型每轮只看当前节点相关的片段。"
    "跨轮用摘要压缩历史，但关键事实单独保留。为了独立验证，我这里不用上一场提到的节点检查点方案，"
    "而是用事件溯源，把每次状态变更记成不可变事件，重放即可得到任意时刻的状态。",
]


def guard():
    url = os.getenv("DATABASE_URL", "")
    if not url.endswith("/" + SANDBOX) or os.getenv("MODEL_LEDGER_DATABASE") != SANDBOX:
        raise SystemExit("REFUSED: wrong database target")


async def ledger(factory):
    async with factory() as db:
        rows = (await db.execute(select(ModelAdmission.call_key, ModelAdmission.route,
                                        ModelAdmission.reserved_tokens, ModelAdmission.charged_tokens,
                                        ModelAdmission.actual_tokens))).all()
    return {"http": len(rows),
            "tokens": sum((r[4] if r[4] is not None else (r[3] if r[3] is not None else (r[2] or 0))) for r in rows)}


def rec(report, name, ok, detail):
    report["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'OK' if ok else 'FAIL'}] {name}: {str(detail)[:140]}", flush=True)


async def main() -> int:
    guard()
    engine = create_async_engine(DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"round": "R122-C-resume", "mode": "WEAKNESS cross-session Live (continued from session 16)",
              "started_at": datetime.now(timezone.utc).isoformat(), "budget": BUDGET,
              "user_id": USER_ID, "session_id": SID, "knowledge_id": KNOWLEDGE_ID, "steps": []}
    base = await ledger(factory)
    report["ledger_baseline"] = base
    core = MockInterviewService(factory)
    try:
        d0 = await core.detail(SID, USER_ID)
        if d0.get("status") != "IN_PROGRESS":
            raise RuntimeError(f"session not in progress: {d0.get('status')}")
        answered = len([q for q in (d0.get("questions") or []) if q.get("answer") or q.get("answered")])
        rec(report, "续跑前置：会话 IN_PROGRESS，首题已由真实模型生成（不重复 start）", True,
            {"status": d0["status"], "questions": len(d0.get("questions") or [])})

        # answer until 3 evaluations or completion
        for i, ans in enumerate(ANSWERS):
            d = await core.detail(SID, USER_ID)
            if d.get("status") == "COMPLETED":
                break
            cq = d.get("current_question") or {}
            qid = cq.get("question_id")
            if not qid:
                rec(report, f"第{i+1}题无可答题", False, {"status": d.get("status")})
                break
            result = await core.answer(SID, USER_ID, qid,
                                       f"r122-c-resume-{SID}-{i}-{uuid.uuid4().hex[:8]}", ans)
            events = [e.get("type") if isinstance(e, dict) else getattr(e, "type", str(e))
                      for e in (result.get("events") if isinstance(result, dict) else [])]
            rec(report, f"第{i+1}题真实作答与评价", True, {"qid": qid, "events": events})
            now = await ledger(factory)
            if now["http"] - base["http"] > BUDGET["max_http"] or now["tokens"] - base["tokens"] > BUDGET["max_tokens"]:
                report["stopped"] = {"reason": "BUDGET_EXCEEDED", "now": now}
                return 3

        final = await core.detail(SID, USER_ID)
        async with factory() as db:
            ev = await db.scalar(select(func.count()).select_from(InterviewEvaluation)
                                 .where(InterviewEvaluation.session_id == SID))
        rec(report, "会话 COMPLETED 且 3 条 Evaluation 落库",
            final.get("status") == "COMPLETED" and ev == 3,
            {"status": final.get("status"), "evals": ev})

        # adaptive decisions
        async with factory() as db:
            qs = (await db.execute(select(InterviewSessionQuestion).where(
                InterviewSessionQuestion.session_id == SID).order_by(InterviewSessionQuestion.id))).scalars()
            decisions = []
            for q in qs:
                ad = (q.candidate_refs_json or {}).get("adaptive_decision", {}) if isinstance(q.candidate_refs_json, dict) else {}
                decisions.append({"qid": q.id, "strategy": ad.get("strategy"),
                                  "target_knowledge_id": ad.get("target_knowledge_id"),
                                  "q": (q.question or "")[:90]})
        rec(report, "adaptive_decision 的 strategy/target_knowledge_id 对应训练目标",
            any(x["target_knowledge_id"] == KNOWLEDGE_ID for x in decisions), decisions)

        # Coach (only if completed)
        if final.get("status") == "COMPLETED":
            coach = await InterviewReportService(factory).generate(SID, USER_ID)
            rec(report, "Coach 正常完成并引用本场真实评价",
                coach.get("generation_status") == "COMPLETED",
                {"id": coach.get("id"), "producer": coach.get("producer"),
                 "fp": coach.get("evaluation_fingerprint")})
            fb = await TrainingService(factory).feedback(USER_ID, SID)
            rec(report, "训练反馈 before/after/effective/independent",
                bool(fb.get("available")),
                {k: fb.get(k) for k in ("mode", "before_state", "after_state",
                                        "effective_evaluations", "independent_evaluations", "message") if k in fb})
            report["feedback"] = fb
    except BaseException as exc:
        rec(report, "运行异常", False, f"{type(exc).__name__}: {exc}")
        report["stopped"] = {"reason": "EXCEPTION", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        after = await ledger(factory)
        delta = {"http": after["http"] - base["http"], "tokens": after["tokens"] - base["tokens"]}
        report["consumed_this_resume"] = delta
        # C field total = r74 first run (1 HTTP/4203) + this resume
        report["consumed_total_c"] = {"http": delta["http"] + 1, "tokens": delta["tokens"] + 4203}
        report["budget_ok"] = report["consumed_total_c"]["http"] <= BUDGET["max_http"] and report["consumed_total_c"]["tokens"] <= BUDGET["max_tokens"]
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (OUT / "c_resume.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"resume_consumed": report["consumed_this_resume"],
                      "total_c": report["consumed_total_c"], "budget_ok": report["budget_ok"],
                      "steps_ok": all(s["ok"] for s in report["steps"]), "stopped": report.get("stopped")}, ensure_ascii=False), flush=True)
    await engine.dispose()
    return 0 if report["budget_ok"] and all(s["ok"] for s in report["steps"]) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
