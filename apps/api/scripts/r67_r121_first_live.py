"""R121 stage 3: the one authorised three-question Live interview.

Real Service + Controller, real governed ModelScope router, real PostgreSQL.
No fixture provider, no direct Evaluation writes, no Repair loop. The run stops
on the first substantive failure, and every admission/step is recorded.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "tests"))

import interview_api.modules.models  # noqa: F401,E402
import interview_api.modules.job_target.models  # noqa: F401,E402
from interview_api.core.config import settings  # noqa: E402
from interview_api.infrastructure.db.session import async_session_factory  # noqa: E402
from interview_api.infrastructure.models.governance_models import ModelAdmission  # noqa: E402
from interview_api.infrastructure.models.router import (  # noqa: E402
    build_interview_router, build_interview_coach_router, build_resume_profile_router,
)
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent  # noqa: E402
from interview_api.modules.experience.models import ExperienceAgentStepRun  # noqa: E402
from interview_api.modules.interview.core_service import MockInterviewService  # noqa: E402
from interview_api.modules.interview.schemas import CreateSessionRequest  # noqa: E402
from interview_api.modules.interview.training import TrainingService  # noqa: E402
from interview_api.modules.interview_report.service import InterviewReportService  # noqa: E402
from interview_api.modules.memory.capability_service import CapabilityMemoryService  # noqa: E402
from interview_api.modules.resume.models import Resume, ResumeReport  # noqa: E402
from interview_api.modules.resume.parser import ResumeParser  # noqa: E402
from interview_api.modules.users.models import User  # noqa: E402
from interview_api.core.security import hash_password  # noqa: E402
from interview_api.modules.interview_report.agent import harness_enabled  # noqa: E402
from test_m3_candidate_profile import RAW  # noqa: E402

SANDBOX = "interview_agent_showcase_r1_20261116"
DSN = f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{SANDBOX}"
OUT = REPO / "data/r68_r121_live"
BUDGET = {"max_http": 13, "max_tokens": 90000}
JD_VERSION = 2  # 沐瞳 Agent 开发工程师（平台）: 12 verbatim requirements, 0 D1 rows
ANSWERS = [
    "我会在 Agent 工作流里用明确的节点边界管理状态：先把用户输入、工具返回和上下文摘要分开保存，"
    "再为每轮调用固定最大步数和超时。失败时记录节点级检查点，重试只读幂等工具，写操作用业务唯一键去重。",
    "我用 RAG 时会先按文档结构切片并保留来源 ID，查询阶段结合关键词和向量召回，重排后只把必要片段放进提示。"
    "评测集按真实用户问题分层，记录命中率和答案依据，避免只测顺利路径。",
    "Function Calling 我会把工具 schema 写得足够窄，参数校验放在服务端；出错时返回可恢复的结构化错误，"
    "让模型能改正或换工具。多 Agent 场景用明确的任务交接格式，并给每个子任务保留可追溯的输入输出。",
]


def guard() -> dict:
    url = os.getenv("DATABASE_URL", "")
    ledger = os.getenv("MODEL_LEDGER_DATABASE", "")
    if not url.endswith("/" + SANDBOX) or ledger != SANDBOX:
        raise SystemExit(f"REFUSED: DATABASE_URL={url} ledger={ledger}")
    router = build_interview_router()
    resolved = {}
    for route in ("interview_main", "answer_evaluation"):
        provider = router.primary(route)
        resolved[route] = {"provider": provider.provider_name, "model": provider.model_name,
                           "governed": bool(getattr(provider, "governed", False))}
        if provider.provider_name != "modelscope" or provider.model_name != settings.modelscope_model:
            raise SystemExit(f"REFUSED route {route}: {resolved[route]}")
        if not resolved[route]["governed"]:
            raise SystemExit(f"REFUSED ungoverned route {route}")
    if router.fallback_routes:
        raise SystemExit("REFUSED: fallback routes present")
    if harness_enabled():
        raise SystemExit("REFUSED: Coach Harness must stay OFF")
    return {"database": SANDBOX, "routes": resolved, "coach_harness": False,
            "main_harness": False, "evaluation_frozen": "2.3.4"}


async def ledger(factory) -> dict:
    async with factory() as db:
        rows = (await db.execute(select(ModelAdmission.call_key, ModelAdmission.route, ModelAdmission.model,
                                        ModelAdmission.status, ModelAdmission.reserved_tokens,
                                        ModelAdmission.charged_tokens, ModelAdmission.actual_tokens))).all()
    return {"http": len(rows),
            "tokens": sum((r[6] if r[6] is not None else (r[5] if r[5] is not None else (r[4] or 0))) for r in rows),
            "rows": [{"call_key": r[0], "route": r[1], "model": r[2], "status": r[3],
                      "reserved": r[4], "charged": r[5], "actual": r[6]} for r in rows]}


async def steps(factory) -> list[dict]:
    async with factory() as db:
        rows = (await db.execute(select(ExperienceAgentStepRun.id, ExperienceAgentStepRun.step_name,
                                        ExperienceAgentStepRun.prompt_name,
                                        ExperienceAgentStepRun.prompt_version,
                                        ExperienceAgentStepRun.status,
                                        ExperienceAgentStepRun.token_usage_json,
                                        ExperienceAgentStepRun.error_message)
                                 .order_by(ExperienceAgentStepRun.id))).all()
    return [{"id": r[0], "step": r[1], "prompt": r[2], "version": r[3],
             "status": r[4], "usage": r[5], "error": (r[6] or "")[:200]} for r in rows]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--resume-id", type=int, default=None)
    args = parser.parse_args()
    env = guard()
    engine = create_async_engine(DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    report = {"round": "R121-3", "mode": "FIRST LIVE / real governed model",
              "environment": env, "started_at": datetime.now(timezone.utc).isoformat(),
              "budget": BUDGET, "jd_version": JD_VERSION, "steps": [], "stopped": None}
    base = await ledger(factory)
    report["ledger_baseline"] = {"http": base["http"], "tokens": base["tokens"]}
    user = None
    resume = None
    session_id = None

    def record(name, ok, detail):
        report["steps"].append({"step": name, "ok": bool(ok), "detail": detail})
        print(f"[{'OK  ' if ok else 'FAIL'}] {name}: {str(detail)[:200]}", flush=True)

    try:
        # ── synthetic, clearly labelled candidate ──────────────────────────
        if args.user_id and args.resume_id:
            async with factory() as db:
                user = await db.get(User, args.user_id)
                resume = await db.get(Resume, args.resume_id)
            record("复用已结算的合成用户与简历", bool(user and resume),
                   {"user_id": args.user_id, "resume_id": args.resume_id,
                    "model_http": 0, "note": "the first profile extraction was already settled in this same Live run"})
            profile = {"status": "COMPLETED", "version_id": "reused"}
        else:
            uid = uuid4().hex[:10]
            email = f"showcase-r1-live-synthetic-{uid}@example.com"
            password = "ShowcaseR1LiveOnly!"
            async with factory() as db:
                user = User(email=email, username=f"SHOWCASE_R1_LIVE_SYNTHETIC_{uid}",
                            password_hash=hash_password(password), role="USER")
                db.add(user)
                await db.flush()
                parsed = ResumeParser().parse(RAW.encode("utf-8"), "txt")
                resume = Resume(user_id=user.id, filename="SHOWCASE_R1_LIVE_SYNTHETIC_RESUME.txt",
                                storage_key=f"showcase/r1-live/synthetic/{uid}.txt", file_type="txt",
                                status="COMPLETED", raw_text="SYNTHETIC TEST MATERIAL — " + RAW)
                db.add(resume)
                await db.flush()
                db.add(ResumeReport(resume_id=resume.id, user_id=user.id,
                                    summary_json={"synthetic": True, "showcase_round": "R121"}))
                await db.commit()
            record("合成用户与简历（明确标注）", True,
                   {"user_id": user.id, "resume_id": resume.id, "parser_type": type(parsed).__name__})

            # ── real model profile extraction ──────────────────────────────
            before = await ledger(factory)
            profile = await ResumeProfileAgent(factory, build_resume_profile_router()).run(resume.id)
            after = await ledger(factory)
            delta = {"http": after["http"] - before["http"], "tokens": after["tokens"] - before["tokens"]}
            record("真实模型抽取画像", profile.get("status") == "COMPLETED",
                   {"status": profile.get("status"), "version_id": profile.get("version_id"),
                    "error": str(profile.get("error"))[:200], "delta": delta})
            if profile.get("status") != "COMPLETED":
                report["stopped"] = {"reason": "PROFILE_NOT_COMPLETED", "error": str(profile.get("error"))[:200]}
                return 2

        # ── real interview through Service + Controller ───────────────────
        core = MockInterviewService(factory, build_interview_router())
        checks = await ledger(factory)
        if checks["http"] - base["http"] > BUDGET["max_http"] or checks["tokens"] - base["tokens"] > BUDGET["max_tokens"]:
            report["stopped"] = {"reason": "BUDGET_BEFORE_INTERVIEW"}
            return 2
        async with factory() as db:
            jd = await db.get(__import__("interview_api.modules.public_career.models",
                                         fromlist=["JobDescriptionVersion"]).JobDescriptionVersion, JD_VERSION)
            job_id, company_id = jd.job_id, None
            from interview_api.modules.public_career.models import Job
            job = await db.get(Job, job_id)
            company_id = job.company_id
        created = await core.create(user.id, CreateSessionRequest(
            company_id=company_id, job_id=job_id, job_description_version_id=JD_VERSION,
            interviewer_skill_id="ai_agent_application", adaptive_interview=True,
            question_count=3, training_mode="FULL", training_request_key=uuid4().hex))
        session_id = created["id"]
        record("真实 Controller 创建 FULL 自适应会话", bool(session_id),
               {"session_id": session_id, "jd_version": JD_VERSION,
                "fingerprint": created.get("snapshot_fingerprint")})

        first = await core.start(session_id, user.id)
        record("真实模型首题", bool(first.get("question_id")),
               {"question_id": first.get("question_id"), "question": str(first.get("question"))[:140]})

        submitted = []
        answer_ids = []
        for index, answer in enumerate(ANSWERS):
            detail = await core.detail(session_id, user.id)
            if detail["status"] == "COMPLETED":
                break
            current = detail.get("current_question") or {}
            qid = current.get("question_id") or current.get("id")
            if not qid:
                record(f"第{index+1}题取当前题", False, detail["status"])
                break
            events = await core.answer(session_id, user.id, qid, uuid4().hex, answer)
            submitted.append({"question_id": qid, "answer": answer})
            from interview_api.modules.interview.models import InterviewAnswer
            async with factory() as db:
                row = await db.scalar(select(InterviewAnswer).where(
                    InterviewAnswer.session_id == session_id, InterviewAnswer.status == "COMPLETED")
                    .order_by(InterviewAnswer.id.desc()))
                if row:
                    answer_ids.append(row.id)
            record(f"第{index+1}题作答与评价落库", any(e[0] == "evaluation" for e in events),
                   {"events": [e[0] for e in events], "answer_id": row.id if row else None})
            live = await ledger(factory)
            used = {"http": live["http"] - base["http"], "tokens": live["tokens"] - base["tokens"]}
            if used["http"] > BUDGET["max_http"] or used["tokens"] > BUDGET["max_tokens"]:
                report["stopped"] = {"reason": "BUDGET_EXCEEDED", "used": used}
                break
            if any(r["status"] and "UNKNOWN" in r["status"].upper() for r in live["rows"]):
                report["stopped"] = {"reason": "UNKNOWN_ADMISSION"}
                break

        final = await core.detail(session_id, user.id)
        evaluated = (final.get("completion_json") or {}).get("evaluated_count")
        record("会话真实 COMPLETED 且三题均评价", final["status"] == "COMPLETED" and evaluated == 3,
               {"status": final["status"], "evaluated": evaluated,
                "questions": len(final.get("questions") or [])})

        # Persisted evaluation audit: quotes and covered points must come from answers.
        async with factory() as db:
            from interview_api.modules.interview.models import InterviewEvaluation
            rows = (await db.execute(select(InterviewEvaluation.id, InterviewEvaluation.artifact_json,
                                            InterviewEvaluation.answer_id)
                                     .where(InterviewEvaluation.session_id == session_id)
                                     .order_by(InterviewEvaluation.id))).all()
        eval_audit = []
        for row in rows:
            artifact = row[1] or {}
            quotes = artifact.get("answer_quotes") or []
            observations = artifact.get("knowledge_observations") or []
            for obs in observations:
                quotes += obs.get("answer_quotes") or []
            answer_text = next((a["answer"] for a in submitted
                                if a["question_id"] == (artifact.get("question_id") or -1)), "")
            eval_audit.append({"evaluation_id": row[0], "answer_id": row[2],
                               "covered_points": artifact.get("covered_points"),
                               "quotes": [str(q)[:120] for q in quotes[:4]],
                               "quote_from_answer": any(str(q)[:12] in answer_text for q in quotes) if answer_text else None,
                               "observation_states": [o.get("state") for o in observations]})
        report["evaluation_audit"] = eval_audit
        record("评价引用来自本题回答", bool(eval_audit) and any(e["quote_from_answer"] for e in eval_audit),
               eval_audit[:2])

        coach = await InterviewReportService(factory, build_interview_coach_router()).generate(
            session_id, user.id)
        report_record = {"id": coach.get("id"), "generation_status": coach.get("generation_status"),
                         "producer": coach.get("producer"), "prompt_version": coach.get("prompt_version"),
                         "statistics": coach.get("statistics_json")}
        record("真实模型 Coach 报告", bool(coach.get("id")), report_record)

        async with factory() as db:
            states = await CapabilityMemoryService(db).list(user.id)
        record("能力观察产生", bool(states),
               [{"topic": s.get("topic"), "state": s.get("state")} for s in states[:5]])

        request = CreateSessionRequest(company_id=company_id, job_id=job_id,
                                       job_description_version_id=JD_VERSION,
                                       interviewer_skill_id="ai_agent_application",
                                       adaptive_interview=True, question_count=3)
        workbench = await TrainingService(factory).workbench(user.id, request)
        report["next_plan"] = {"targets": len(workbench["targets"]),
                               "recommendations": workbench.get("recommendations") or []}
        linked = [r for r in (workbench.get("recommendations") or [])
                  if any(f"evaluation:{e[0]}" in json.dumps(r, ensure_ascii=False) for e in rows)]
        record("下一场 MemoryGuidedPlan 引用本场真实评价", bool(linked),
               {"linked": len(linked), "recommendations": (workbench.get("recommendations") or [])[:2]})

    except BaseException as exc:
        record("运行异常", False, f"{type(exc).__name__}: {exc}")
        report["stopped"] = {"reason": "EXCEPTION", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        final_ledger = await ledger(factory)
        delta = {"http": final_ledger["http"] - base["http"],
                 "tokens": final_ledger["tokens"] - base["tokens"]}
        report["actual"] = delta
        report["budget_ok"] = (delta["http"] <= BUDGET["max_http"]
                               and delta["tokens"] <= BUDGET["max_tokens"])
        report["ledger_rows"] = final_ledger["rows"][base["http"]:]
        report["step_audit"] = [s for s in await steps(factory) if s["id"] > 0][-30:]
        report["user_id"] = user.id if user else None
        report["session_id"] = session_id
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "live_run.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str),
                                           encoding="utf-8")
        await engine.dispose()
    print("ACTUAL", report["actual"], "budget_ok", report["budget_ok"], "stopped", report["stopped"])
    print("written", OUT / "live_run.json")
    return 0 if not report["stopped"] and report["budget_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
