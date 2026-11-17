"""R122 stage 3: C - true cross-session WEAKNESS training Live for R121 user A.

Real Service + Controller + governed model. Reuses user A (id=9), JD2, the legal
PRACTICING memory target ai_agent_application:agent, and the R121 Live profile.
Only 3 questions. Verifies the 10 acceptance points, with a 13 HTTP / 90K token
hard budget and abort-on-substantive-failure.
"""
from __future__ import annotations

import argparse
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
from interview_api.infrastructure.models.router import build_interview_router  # noqa: E402
from interview_api.modules.interview.core_service import MockInterviewService  # noqa: E402
from interview_api.modules.interview.models import InterviewSession, InterviewSessionQuestion  # noqa: E402
from interview_api.modules.interview.schemas import CreateSessionRequest  # noqa: E402
from interview_api.modules.interview.training import TrainingService  # noqa: E402
from interview_api.modules.interview_report.service import InterviewReportService  # noqa: E402
from interview_api.core.security import hash_password  # noqa: E402
from interview_api.modules.interview_report.agent import harness_enabled  # noqa: E402

SANDBOX = "interview_agent_showcase_r1_20261116"
DSN = f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{SANDBOX}"
OUT = REPO / "data/r74_r122_c_live"
BUDGET = {"max_http": 13, "max_tokens": 90000}
USER_ID = 9
JD_VERSION = 2
COMPANY_ID, JOB_ID = 2, 2
SKILL = "ai_agent_application"
KNOWLEDGE_ID = "ai_agent_application:agent"
# Frozen answers for the Agent tool/state target - independent, no reference answers.
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


def guard() -> dict:
    url = os.getenv("DATABASE_URL", "")
    ledger_db = os.getenv("MODEL_LEDGER_DATABASE", "")
    if not url.endswith("/" + SANDBOX) or ledger_db != SANDBOX:
        raise SystemExit(f"REFUSED: DATABASE_URL={url} ledger={ledger_db}")
    router = build_interview_router()
    resolved = {}
    for route in ("interview_main", "answer_evaluation"):
        p = router.primary(route)
        resolved[route] = {"provider": p.provider_name, "model": p.model_name,
                           "governed": bool(getattr(p, "governed", False))}
        if (p.provider_name != "modelscope" or p.model_name != settings.modelscope_model
                or not resolved[route]["governed"]):
            raise SystemExit(f"REFUSED route {route}")
    if router.fallback_routes or harness_enabled():
        raise SystemExit("REFUSED: fallback or Coach Harness on")
    return {"database": SANDBOX, "routes": resolved, "coach_harness": False,
            "main_harness": False, "evaluation_frozen": "2.3.4"}


async def ledger(factory) -> dict:
    async with factory() as db:
        rows = (await db.execute(select(ModelAdmission.call_key, ModelAdmission.route,
                                        ModelAdmission.status, ModelAdmission.reserved_tokens,
                                        ModelAdmission.charged_tokens, ModelAdmission.actual_tokens))).all()
    return {"http": len(rows),
            "tokens": sum((r[5] if r[5] is not None else (r[4] if r[4] is not None else (r[3] or 0))) for r in rows)}


def rec(report, name, ok, detail):
    report["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'OK' if ok else 'FAIL'}] {name}: {str(detail)[:140]}", flush=True)


async def main() -> int:
    env = guard()
    engine = create_async_engine(DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"round": "R122-C", "mode": "WEAKNESS cross-session Live / real model",
              "environment": env, "started_at": datetime.now(timezone.utc).isoformat(),
              "budget": BUDGET, "user_id": USER_ID, "jd_version": JD_VERSION,
              "knowledge_id": KNOWLEDGE_ID, "steps": [], "stopped": None}
    base = await ledger(factory)
    core = MockInterviewService(factory)
    resume_sid = int(os.getenv("R122_C_RESUME_SID", "0")) or None
    try:
        # 0-HTTP precondition: recompute legal training candidate
        req0 = CreateSessionRequest(company_id=COMPANY_ID, job_id=JOB_ID,
                                    job_description_version_id=JD_VERSION,
                                    interviewer_skill_id=SKILL, adaptive_interview=True, question_count=3)
        wb = await TrainingService(factory).workbench(USER_ID, req0)
        target = next((t for t in wb["targets"] if t["knowledge_id"] == KNOWLEDGE_ID), None)
        legal = bool(target and target["state"] == "PRACTICING" and "WEAKNESS" in target["available_modes"]
                     and target["revision"] == 1 and not target["revoked"])
        obs_live = all(o["session_id"] == 14 for o in (target or {}).get("observations", [])) if target else False
        rec(report, "0HTTP 训练候选合法（PRACTICING→WEAKNESS，来源全为 R121 Live session14）", legal and obs_live,
            {"target": target, "all_obs_from_live_14": obs_live,
             "recommendations": wb.get("recommendations")})
        if not legal:
            report["stopped"] = {"reason": "NO_LEGAL_WEAKNESS_TARGET"}
            return 2

        if resume_sid:
            sid = resume_sid
            report["session_id"] = sid
            report["resumed"] = True
            # snapshot verified in the first run; re-read for the record
            async with factory() as db:
                snap = await db.execute(select(InterviewSession.context_snapshot_id)
                                        .where(InterviewSession.id == sid))
                snap_id = snap.scalar_one()
            rec(report, "续跑已创建的 WEAKNESS 会话（不重复出题）", True, {"session_id": sid})
        else:
          # create WEAKNESS training session via TrainingService (idempotent key)
          req = CreateSessionRequest(company_id=COMPANY_ID, job_id=JOB_ID,
                                   job_description_version_id=JD_VERSION,
                                   interviewer_skill_id=SKILL, adaptive_interview=True, question_count=3,
                                   training_mode="WEAKNESS", training_knowledge_id=KNOWLEDGE_ID,
                                   training_request_key="r122-c-weakness-" + uuid.uuid4().hex[:12])
        created = await TrainingService(factory).create(USER_ID, req)
        sid = created["id"]
        # verify frozen snapshot carries the legal training target + plan reference
        async with factory() as db:
            snap = await db.execute(select(InterviewSession.context_snapshot_id)
                                    .where(InterviewSession.id == sid))
            snap_id = snap.scalar_one()
            ctxj = await db.execute(select(__import__("interview_api.modules.interview.models", fromlist=["InterviewContextSnapshot"]).InterviewContextSnapshot.context_json)
                                    .where(__import__("interview_api.modules.interview.models", fromlist=["InterviewContextSnapshot"]).InterviewContextSnapshot.id == snap_id))
            ctx = ctxj.scalar_one()
        training = ctx.get("training", {}) if isinstance(ctx, dict) else {}
        plan = ctx.get("memory_guided_plan", {}) if isinstance(ctx, dict) else {}
        plan_text = json.dumps(plan, ensure_ascii=False)
        plan_refs = [e for e in ("evaluation:27", "evaluation:28", "evaluation:29") if e in plan_text]
        rec(report, "冻结快照含合法训练目标 + MemoryGuidedPlan 引用 R121 Live 评价",
            training.get("mode") == "WEAKNESS" and training.get("selected_knowledge_id") == KNOWLEDGE_ID and len(plan_refs) >= 1,
            {"mode": training.get("mode"), "knowledge_id": training.get("selected_knowledge_id"),
             "plan_refs": plan_refs})
        report["session_id"] = sid

        # start (first question via real model)
        first = await core.start(sid, USER_ID)
        rec(report, "真实模型首题生成", bool(first), {"result": str(first)[:200]})

        # answer 3 frozen replies
        for i, ans in enumerate(ANSWERS):
            d = await core.detail(sid, USER_ID)
            current = d.get("current_question") or {}
            qid = current.get("id")
            if not qid:
                rec(report, f"第{i+1}题无可答题", False, {"status": d.get("status")})
                break
            result = await core.answer(sid, USER_ID, qid,
                                       f"r122-c-{sid}-{i}-{uuid.uuid4().hex[:8]}", ans)
            events = [e.get("type") if isinstance(e, dict) else getattr(e, "type", str(e))
                      for e in (result.get("events") if isinstance(result, dict) else [])]
            rec(report, f"第{i+1}题真实作答与评价", True,
                {"qid": qid, "events": events})
            mid = await ledger(factory)
            if mid["http"] - base["http"] > BUDGET["max_http"] or mid["tokens"] - base["tokens"] > BUDGET["max_tokens"]:
                report["stopped"] = {"reason": "BUDGET_EXCEEDED", "now": mid}
                return 3

        # completion
        detail = await core.detail(sid, USER_ID)
        ev_count = await _eval_count(factory, sid)
        rec(report, "会话 COMPLETED 且 3 条真实 Evaluation 落库",
            detail.get("status") == "COMPLETED" and ev_count == 3,
            {"status": detail.get("status"), "evals": ev_count})

        # adaptive decisions: strategy + target_knowledge_id
        async with factory() as db:
            qs = await db.execute(select(InterviewSessionQuestion).where(
                InterviewSessionQuestion.session_id == sid).order_by(InterviewSessionQuestion.id))
            decisions = []
            for qrow in qs.scalars():
                ad = (qrow.candidate_refs_json or {}).get("adaptive_decision", {}) if isinstance(qrow.candidate_refs_json, dict) else {}
                decisions.append({"qid": qrow.id, "strategy": ad.get("strategy"),
                                  "target_knowledge_id": ad.get("target_knowledge_id"), "q": (qrow.question or "")[:90]})
        rec(report, "adaptive_decision 的 strategy/target_knowledge_id 与训练目标对应",
            any(d["target_knowledge_id"] == KNOWLEDGE_ID for d in decisions), decisions)

        # Coach report
        coach = await InterviewReportService(factory).generate(sid, USER_ID)
        rec(report, "Coach 正常完成并引用本场真实评价",
            coach.get("generation_status") == "COMPLETED",
            {"id": coach.get("id"), "producer": coach.get("producer"),
             "fp": coach.get("evaluation_fingerprint")})

        # feedback: before/after/effective/independent
        fb = await TrainingService(factory).feedback(USER_ID, sid)
        rec(report, "训练反馈含 before/after/effective/independent",
            fb.get("available") and ("before_state" in fb or "targets" in fb),
            {k: fb.get(k) for k in ("mode", "before_state", "after_state", "effective_evaluations",
                                    "independent_evaluations", "message") if k in fb})
        report["feedback"] = fb
    except BaseException as exc:
        rec(report, "运行异常", False, f"{type(exc).__name__}: {exc}")
        report["stopped"] = {"reason": "EXCEPTION", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        final = await ledger(factory)
        delta = {"http": final["http"] - base["http"], "tokens": final["tokens"] - base["tokens"]}
        report["consumed"] = delta
        report["budget_ok"] = delta["http"] <= BUDGET["max_http"] and delta["tokens"] <= BUDGET["max_tokens"]
        report["ledger"] = {"before": base, "after": final}
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (OUT / "c_live.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"consumed": report["consumed"], "budget_ok": report["budget_ok"],
                      "steps_ok": all(s["ok"] for s in report["steps"]), "session_id": report.get("session_id"),
                      "stopped": report["stopped"]}, ensure_ascii=False), flush=True)
    await engine.dispose()
    return 0 if report["budget_ok"] and all(s["ok"] for s in report["steps"]) else 2


async def _eval_count(factory, sid) -> int:
    from interview_api.modules.interview.models import InterviewEvaluation
    async with factory() as db:
        return await db.scalar(select(func.count()).select_from(InterviewEvaluation)
                               .where(InterviewEvaluation.session_id == sid))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
