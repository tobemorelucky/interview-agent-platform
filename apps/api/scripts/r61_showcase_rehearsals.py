"""SHOWCASE-R1 / part 4: three complete Replay-driven flows on an isolated DB.

Uses the real business services (auth, resume parser, profile agent, interview
controller, evaluation, coach report, capability memory, training) with explicit
Replay/DEMO model providers. No outgoing HTTP: every model call is a fixture.
The accounts and resumes are clearly labelled synthetic.

Flow A  AI Agent/RAG published JD + adaptive FULL, >= 3 questions
Flow B  Java-backend published JD + adaptive FULL, >= 3 questions
Flow C  same user as A, after persisted evaluations/capability state, a legal
        WEAKNESS (or RECHECK) training session with MemoryGuidedPlan

Failures are recorded per step; the script never claims Live success.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "tests"))

import interview_api.modules.models  # noqa: F401,E402
from interview_api.core.config import settings  # noqa: E402
from interview_api.infrastructure.models.router import (  # noqa: E402
    ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE, INTERVIEW_MAIN_ROUTE,
    RESUME_PROFILE_ROUTE, ModelRouter,
)
from interview_api.infrastructure.models.provider import ReplayModelProvider  # noqa: E402
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent  # noqa: E402
from interview_api.modules.experience.models import ExperienceAgentStepRun  # noqa: E402
from interview_api.infrastructure.models.governance_models import ModelAdmission  # noqa: E402
from interview_api.modules.interview.core_service import MockInterviewService  # noqa: E402
from interview_api.modules.interview.schemas import CreateSessionRequest  # noqa: E402
from interview_api.modules.interview.training import TrainingService  # noqa: E402
from interview_api.modules.interview_report.service import InterviewReportService  # noqa: E402
from interview_api.modules.memory.capability_service import CapabilityMemoryService  # noqa: E402
from interview_api.modules.resume.models import Resume, ResumeReport  # noqa: E402
from interview_api.modules.resume.parser import ResumeParser  # noqa: E402
from interview_api.modules.interview_report.statistics import interview_records  # noqa: E402
from test_coach_harness import response_for  # noqa: E402
from test_m3_candidate_profile import RAW, response as profile_response, model_router  # noqa: E402
from v21_support import AdaptiveReplayProvider  # noqa: E402
from v22_support import MemoryReplayProvider  # noqa: E402

DATABASE = "interview_agent_showcase_r1_20261116"
DSN = f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{DATABASE}"
OUT = REPO / "data/r61_showcase_rehearsals"
PASSWORD = "ShowcaseR1ReplayOnly!"
RUN_ID = uuid4().hex[:8]

# The two showcase roles, chosen from the PUBLISHED rows copied from the JD sandbox.
FLOW_A = {"company": "沐瞳科技", "title_contains": "Agent开发工程师", "skill": "ai_agent_application"}
FLOW_B = {"company": "沐瞳科技", "title_contains": "后端开发工程师", "skill": "java_backend"}


def guard() -> dict:
    if DATABASE not in settings.database_url:
        raise SystemExit(f"REFUSED: DATABASE_URL is not the showcase sandbox ({settings.database_url})")
    from interview_api.modules.interview_report.agent import harness_enabled
    if harness_enabled():
        raise SystemExit("REFUSED: Coach Harness must stay OFF")
    return {"database": DATABASE, "coach_harness": False, "main_harness": False,
            "provider": "replay", "live_http": 0}


class Recorder:
    def __init__(self, name: str):
        self.name = name
        self.steps: list[dict] = []
        self.failure: str | None = None

    def step(self, name: str, ok: bool, detail=None):
        self.steps.append({"step": name, "ok": bool(ok), "detail": detail})
        if not ok:
            self.failure = name
        print(f"  [{self.name}] {'OK  ' if ok else 'FAIL'} {name}: {str(detail)[:120]}")
        return ok

    def as_dict(self):
        return {"flow": self.name, "steps": self.steps, "failure_step": self.failure,
                "completed": all(s["ok"] for s in self.steps)}


async def register_login(client, email: str, username: str) -> dict:
    registered = await client.post("/api/v1/auth/register",
                                   json={"email": email, "username": username, "password": PASSWORD})
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    token = ((login.json().get("data") or {}).get("access_token"))
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}) if token else None
    return {"register_status": registered.status_code,
            "user_id": (registered.json().get("data") or {}).get("user_id"),
            "login_status": login.status_code, "token_present": bool(token),
            "me_status": me.status_code if me else None,
            "me_email": (me.json().get("data") or {}).get("email") if me else None}


async def synthetic_resume(factory, user_id: int) -> dict:
    """Synthetic, labelled fixture. Parser runs for real; no file storage needed."""
    parsed = ResumeParser().parse(RAW.encode("utf-8"), "txt")
    async with factory() as db:
        resume = Resume(user_id=user_id, filename="SHOWCASE_R1_SYNTHETIC_RESUME.txt",
                        storage_key=f"showcase/r1/synthetic/{uuid4().hex}.txt", file_type="txt",
                        status="COMPLETED", raw_text=RAW)
        db.add(resume)
        await db.flush()
        db.add(ResumeReport(resume_id=resume.id, user_id=user_id,
                            summary_json={"synthetic": True, "showcase_round": "R1",
                                          "skills": ["Python", "Redis"]}))
        await db.commit()
        return {"resume_id": resume.id, "parser_sections": list(parsed) if isinstance(parsed, dict) else None,
                "raw_chars": len(RAW), "label": "SYNTHETIC_TEST_MATERIAL"}


async def build_profile(factory, resume_id: int) -> dict:
    router, provider = model_router([profile_response(resume_id)])
    result = await ResumeProfileAgent(factory, router).run(resume_id)
    return {"status": result.get("status"), "version_id": result.get("version_id"),
            "replay_calls": provider.calls, "producer": "REPLAY_FIXTURE"}


async def pick_job(conn, pick: str) -> dict:
    row = await conn.fetchrow(
        """select c.id company_id, c.canonical_name, j.id job_id, j.canonical_title, v.id jd_version_id
             from job_description_versions v
             join jobs j on j.id=v.job_id join companies c on c.id=j.company_id
            where v.publication_status='PUBLISHED' and c.canonical_name=$1
              and j.canonical_title like $2 and j.status='ACTIVE' and c.status='ACTIVE'
            order by v.id limit 1""", FLOW_A["company"] if pick == "A" else FLOW_B["company"],
        f"%{FLOW_A['title_contains'] if pick == 'A' else FLOW_B['title_contains']}%")
    return dict(row) if row else {}


async def create_and_run(factory, user_id: int, job: dict, *, flow: str,
                         training_mode: str | None = None,
                         knowledge_id: str | None = None,
                         provider_cls=AdaptiveReplayProvider,
                         rec: Recorder, min_questions: int = 3) -> dict:
    provider = provider_cls()
    core = MockInterviewService(factory, ModelRouter(
        {INTERVIEW_MAIN_ROUTE: provider, ANSWER_EVALUATION_ROUTE: provider}))
    skill = FLOW_A["skill"] if flow == "A" else (FLOW_B["skill"] if flow == "B" else FLOW_A["skill"])
    request = CreateSessionRequest(
        company_id=job["company_id"], job_id=job["job_id"],
        job_description_version_id=job["jd_version_id"],
        interviewer_skill_id=skill, adaptive_interview=True, question_count=3,
        training_mode=training_mode, training_knowledge_id=knowledge_id,
        training_request_key=uuid4().hex if training_mode else None)
    created = await core.create(user_id, request)
    snapshot = created["context_snapshot"]
    rec.step("冻结面试快照", bool(created.get("context_snapshot")) and bool(created.get("snapshot_fingerprint")),
             {"session_id": created["id"], "jd_version_id": snapshot.get("job_description_version_id"),
              "training": (snapshot.get("training") or {}).get("mode"),
              "memory_guided_plan_items": len(((snapshot.get("memory_guided_plan") or {}).get("items") or []))})
    rec.step("选择已发布 JD", snapshot.get("publication_status") != "WAITING_REVIEW"
             and snapshot.get("job_id") == job["job_id"],
             {"company": snapshot.get("company_name"), "job": snapshot.get("job_title")})

    first = await core.start(created["id"], user_id)
    rec.step("Main 出题（首题）", bool(first.get("question_id")), {"question": str(first.get("question"))[:80]})

    answers = ["DEMO 合成回答：使用事务和唯一键处理，包含失败恢复",
               "DEMO 合成回答：只了解整体思路，缺少具体实现",
               "DEMO 合成回答：事务提交后 ACK，异常时用工具回滚并验证"]
    turns = 0
    for index, answer in enumerate(answers):
        detail = await core.detail(created["id"], user_id)
        if detail["status"] == "COMPLETED":
            break
        current = detail.get("current_question") or {}
        question_id = current.get("question_id") or current.get("id")
        if not question_id:
            rec.step(f"第{index + 1}轮取当前题", False, detail["status"])
            break
        events = await core.answer(created["id"], user_id, question_id, uuid4().hex, answer)
        turns += 1
        rec.step(f"作答与追问 第{turns}轮", any(e[0] == "evaluation" for e in events),
                 {"events": [e[0] for e in events], "answer_chars": len(answer)})

    final = await core.detail(created["id"], user_id)
    evaluated = (final.get("completion_json") or {}).get("evaluated_count")
    rec.step(f"会话 COMPLETED 且 >={min_questions} 题",
             final["status"] == "COMPLETED" and (evaluated or 0) >= min_questions,
             {"status": final["status"], "evaluated_count": evaluated,
              "questions": len(final.get("questions") or [])})
    rec.step("快照未被改写", final.get("snapshot_fingerprint") == created.get("snapshot_fingerprint")
             and final.get("context_snapshot") == created.get("context_snapshot"), None)

    # Coach report over the persisted evaluations, with a legal fixture artifact.
    async with factory() as db:
        _, evaluations = await interview_records(db, created["id"])
    eval_ids = [(row.get("id") if isinstance(row, dict) else row.id) for row in evaluations]
    coach = ReplayModelProvider([response_for(eval_ids)]) if eval_ids else ReplayModelProvider([])
    try:
        report = await InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: coach})).generate(
            created["id"], user_id)
        rec.step("Coach 报告生成", report.get("generation_status") == "COMPLETED"
                 or report.get("status") == "COMPLETED" or bool(report.get("id")),
                 {"report_id": report.get("id"), "statistics": report.get("statistics_json", {}),
                  "supporting_evaluation_ids": eval_ids[:4]})
        report_payload = {"id": report.get("id"), "status": report.get("generation_status"),
                          "statistics": report.get("statistics_json"),
                          "referenced_evaluations": eval_ids}
    except BaseException as exc:
        rec.step("Coach 报告生成", False, f"{type(exc).__name__}: {exc}")
        report_payload = {"error": f"{type(exc).__name__}: {exc}"}

    async with factory() as db:
        states = await CapabilityMemoryService(db).list(user_id)
    rec.step("能力状态由持久化评价产生", bool(states),
             {"states": [{"topic": s.get("topic"), "state": s.get("state"),
                          "revision": s.get("revision")} for s in states[:6]]})
    return {"session_id": created["id"], "final_status": final["status"],
            "evaluated_count": evaluated, "evaluation_ids": eval_ids,
            "report": report_payload, "capability_states": [
                {"topic": s.get("topic"), "state": s.get("state")} for s in states],
            "replay_model_calls": len(getattr(provider, "calls", []) or []),
            "context_snapshot_keys": sorted(snapshot.keys())}


async def flow(factory, client, conn, label: str) -> dict:
    rec = Recorder(label)
    email = f"showcase-r1-synthetic-{label.lower()}-{RUN_ID}@example.com"
    auth = await register_login(client, email, f"SHOWCASE_R1_SYNTHETIC_{label}_{RUN_ID}")
    auth["email"] = email
    rec.step("注册 / 登录 / me", auth["register_status"] in (200, 201) and auth["login_status"] == 200
             and auth["token_present"] and auth["me_status"] == 200, auth)
    user_id = auth["user_id"]
    if not user_id:
        return rec.as_dict()
    resume = await synthetic_resume(factory, user_id)
    rec.step("合成简历解析与落库", bool(resume["resume_id"]) and resume["raw_chars"] > 0, resume)
    profile = await build_profile(factory, resume["resume_id"])
    rec.step("候选人画像（Replay）", profile["status"] == "COMPLETED", profile)

    job = await pick_job(conn, label)
    if not job:
        rec.step("选择已发布 JD", False, "no published JD matched")
        return rec.as_dict()
    targets = await client.get("/api/v1/interview/targets",
                               headers={"Authorization": f"Bearer {(await client.post('/api/v1/auth/login', json={'email': email, 'password': PASSWORD})).json()['data']['access_token']}"})
    listed = [row for row in (targets.json().get("data") or []) if row.get("job_id") == job["job_id"]]
    rec.step("公开岗位列表可选中", targets.status_code == 200 and bool(listed),
             {"target_status": targets.status_code, "matched": len(listed), "job": job})

    result = await create_and_run(factory, user_id, job, flow=label, training_mode="FULL",
                                  provider_cls=AdaptiveReplayProvider, rec=rec)
    payload = rec.as_dict()
    payload.update({"user_id": user_id, "job": job, "auth": auth, "resume": resume,
                    "profile": profile, "session": result})
    return payload


async def flow_c(factory, client, conn, flow_a: dict) -> dict:
    rec = Recorder("C")
    user_id = flow_a["user_id"]
    job = flow_a["job"]
    email = flow_a["auth"]["email"]
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    rec.step("同一测试用户再次登录", login.status_code == 200, {"login_status": login.status_code})

    request = CreateSessionRequest(company_id=job["company_id"], job_id=job["job_id"],
                                   job_description_version_id=job["jd_version_id"],
                                   interviewer_skill_id=FLOW_A["skill"],
                                   adaptive_interview=True, question_count=3)
    try:
        board = await TrainingService(factory).workbench(user_id, request)
        candidates = [t for t in board["targets"]
                      if any(mode in t["available_modes"] for mode in ("WEAKNESS", "RECHECK"))]
        rec.step("训练工作台出现合法 WEAKNESS/RECHECK 目标", bool(candidates),
                 {"target_count": len(board["targets"]),
                  "legal": [{"id": t["knowledge_id"], "state": t["state"],
                             "modes": t["available_modes"]} for t in candidates[:4]],
                  "recommendations": len(board.get("recommendations") or [])})
        if not candidates:
            return rec.as_dict()
        target = candidates[0]
        mode = "RECHECK" if "RECHECK" in target["available_modes"] else "WEAKNESS"
    except BaseException as exc:
        rec.step("训练工作台出现合法 WEAKNESS/RECHECK 目标", False, f"{type(exc).__name__}: {exc}")
        return rec.as_dict()

    result = await create_and_run(factory, user_id, job, flow="C", training_mode=mode,
                                  knowledge_id=target["knowledge_id"],
                                  provider_cls=MemoryReplayProvider, rec=rec, min_questions=1)
    created_snapshot = result.get("context_snapshot_keys") or []
    rec.step("跨场次 MemoryGuidedPlan 已冻结", "memory_guided_plan" in created_snapshot,
             {"training_mode": mode, "knowledge_id": target["knowledge_id"],
              "snapshot_keys_count": len(created_snapshot)})
    payload = rec.as_dict()
    payload.update({"user_id": user_id, "job": job, "training_mode": mode,
                    "knowledge_id": target["knowledge_id"],
                    "prior_session_id": flow_a["session"]["session_id"],
                    "session": result})
    return payload


async def main() -> int:
    env = guard()
    engine = create_async_engine(DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    import asyncpg
    conn = await asyncpg.connect(f"postgresql://interview_agent:interview_agent@localhost:5432/{DATABASE}")
    from interview_api.main import app
    transport = httpx.ASGITransport(app=app)
    report = {"round": "SHOWCASE-R1 part4", "mode": "REPLAY / 0 real HTTP",
              "environment": env, "database": DATABASE,
              "started_at": datetime.now(timezone.utc).isoformat(), "flows": {}}

    async with httpx.AsyncClient(transport=transport, base_url="http://showcase") as client:
        async with factory() as db:
            before = await db.scalar(select(func.count()).select_from(ExperienceAgentStepRun))
        for label in ("A", "B"):
            try:
                report["flows"][label] = await flow(factory, client, conn, label)
            except BaseException as exc:
                report["flows"][label] = {"flow": label, "exception": f"{type(exc).__name__}: {exc}",
                                          "completed": False}
                print(f"[{label}] EXCEPTION {type(exc).__name__}: {exc}")
        if report["flows"].get("A", {}).get("completed"):
            try:
                report["flows"]["C"] = await flow_c(factory, client, conn, report["flows"]["A"])
            except BaseException as exc:
                report["flows"]["C"] = {"flow": "C", "exception": f"{type(exc).__name__}: {exc}",
                                        "completed": False}
        else:
            report["flows"]["C"] = {"flow": "C", "skipped": "A did not complete; no persisted evaluations",
                                    "completed": False}
        async with factory() as db:
            after = await db.scalar(select(func.count()).select_from(ExperienceAgentStepRun))
            admissions = await db.scalar(select(func.count()).select_from(ModelAdmission))
    report["step_rows_delta"] = after - before
    report["model_admissions_total"] = admissions
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    for label, payload in report["flows"].items():
        print(f"FLOW {label}: completed={payload.get('completed')} "
              f"failure={payload.get('failure_step')} "
              f"status={(payload.get('session') or {}).get('final_status')}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rehearsals.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    await engine.dispose()
    await conn.close()
    print("written", OUT / "rehearsals.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
