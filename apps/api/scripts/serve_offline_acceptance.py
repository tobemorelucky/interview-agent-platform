"""Offline acceptance API/worker for the browser E2E: real services, replay models.

Round 9 drove the browser against the real provider and spent 12 real model
calls. Round 11 allows none, so this launcher substitutes the model boundary and
nothing else: the same FastAPI app, the same services, transactions, ownership
checks and UI, with every agent backed by a deterministic replay provider and all
outbound HTTP blocked.

Round 16 incident and this round's fix
--------------------------------------
`--database` used to create a *disposable* engine that only `seed`/`history`
used. `api` then started `uvicorn.run("interview_api.main:app")`, whose
module-level engine is built from `settings.database_url` — i.e. `.env`. So the
guard validated a name the server never used, and the API served production.

Every action now goes through `interview_api.core.runtime_target`:

    python apps/api/scripts/serve_offline_acceptance.py verify --database interview_agent_wb_r17_e2e_20261113
    python apps/api/scripts/serve_offline_acceptance.py api    --database interview_agent_wb_r17_e2e_20261113
    python apps/api/scripts/serve_offline_acceptance.py worker --database interview_agent_wb_r17_e2e_20261113

The target must be named explicitly, is validated with the project-wide
`db_targets` classification, is written into settings *before* any engine or
Redis client is imported, and is re-verified against the built engine before the
server starts. Nothing here falls back to `.env`, and no password is printed.
"""

import argparse
import asyncio
import atexit
import dataclasses
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
sys.path[:0] = [str(API / "src"), str(API / "tests")]

from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from interview_api.core import runtime_target  # noqa: E402
from interview_api.core.config import settings  # noqa: E402

PASSWORD = "r11-acceptance-only"
EMAILS = {"owner": "r11.owner@example.com", "intruder": "r11.intruder@example.com"}
GOOD = "Kafka 事务和唯一键，提交后 ACK，失败恢复"
WEAK = "Kafka ACK，失败重试"


def database(target):
    engine = create_async_engine(make_url(target.url))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def announce(target):
    """One line per process: the whole isolated group, never a password."""
    group = target.describe()
    for notice in target.notices:
        print(f"NOTICE: {notice}")
    print("ACCEPTANCE_TARGET " + json.dumps(group, ensure_ascii=False))


# ── replay providers: the only substitution in this harness ──────────────────
from interview_api.infrastructure.models.provider import (  # noqa: E402
    ModelProvider, ModelResponse)


class ReplayExtract(ModelProvider):
    """Echoes the pasted JD as requirements; every evidence is verbatim.

    Company/title are copied from a labelled line when the pasted text has one,
    so the UI shows what the user wrote instead of an invented value.
    """

    provider_name, model_name = "replay", "R11_EXTRACT"
    ROLES = ("工程师", "开发", "专员", "经理", "专家", "架构师")

    async def generate(self, messages, *, schema):
        raw = json.loads(messages[-1]["content"])["input"]["input"]["raw_text"]
        lines = [line.strip(" -•\t") for line in raw.splitlines() if len(line.strip()) > 4]

        def labelled(prefixes):
            for line in lines:
                if any(line.startswith(prefix) for prefix in prefixes):
                    return line.split("：", 1)[-1].strip()[:40] or None
            return None

        requirements = [dict(statement=line[:40], importance="REQUIRED", evidence=line,
                             topic="Kafka" if "Kafka" in line else "Python" if "Python" in line else None)
                        for line in lines if line.endswith(("。", "；", ";", ".")) or "熟悉" in line or "掌握" in line]
        value = dict(company_name=labelled(("公司名称：", "公司：")),
                     job_title=labelled(("岗位名称：", "职位：")) or next(
                         (line[:40] for line in lines if line.endswith(self.ROLES)), None),
                     responsibilities=[line for line in lines if line.endswith("。")][:3],
                     requirements=requirements[:5])
        return ModelResponse(json.dumps(value, ensure_ascii=False), dict(total_tokens=0))


class ReplayCoach(ModelProvider):
    provider_name, model_name = "replay", "R11_COACH"

    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        refs = [item["evidence_ref"] for item in payload["evaluations"]]
        finding = lambda text: dict(statement=text, supporting_evaluation_ids=refs[:1])
        value = dict(overall_summary="离线回放：围绕你提供的私有 JD 做了一轮训练，机制说明与边界条件仍有缺口。",
            strengths=[finding("回答能说明消息可靠消费的基本取舍。")],
            weaknesses=[finding("失败恢复的边界仍需更具体的说明。")],
            recurring_issues=[finding("多轮回答中失败恢复细节仍不充分。")] if len(refs) > 1 else [],
            job_alignment=[finding("回答与私有 JD 中的消息可靠性要求存在证据交集。")],
            recommended_actions=["复盘消费端失败恢复与重复投递。"],
            practice_plan=["按原理、实现、边界三段式练习。"], memory_proposals=[])
        return ModelResponse(json.dumps(value, ensure_ascii=False), dict(total_tokens=0))


SECTION_TITLES = {
    "projects": ("项目经历", "项目经验", "项目"),
    "work": ("工作经历", "工作经验"),
    "internship": ("实习经历", "实习经验"),
    "education": ("教育经历", "教育背景", "教育"),
    "skills": ("专业技能", "技术技能", "技能"),
}


def _resume_text(prompt):
    """The launcher echoes the *uploaded* text; it never invents resume content."""
    marker, end = "## 简历文本", "## 输出 JSON 格式"
    if marker in prompt:
        text = prompt.split(marker, 1)[1]
        return (text.split(end, 1)[0] if end in text else text).strip()
    return ""


def _sections(text):
    found, current = {}, "general"
    for raw in text.splitlines():
        line = raw.strip(" -•\t")
        if not line:
            continue
        heading = next((name for name, titles in SECTION_TITLES.items()
                        if line in titles or line.rstrip("：:") in titles), None)
        if heading:
            current = heading
            continue
        found.setdefault(current, []).append(line)
    return found


class ReplayResumeExtract:
    """Deterministic stand-in for `resume_parse_v1`; extracts only what is written.

    Replaces `OpenAICompatibleLLMProvider` inside the worker, so it must accept
    the same constructor arguments and expose `chat`, not `generate`.
    """

    provider_name, model_name, thinking_type = "replay", "R17_RESUME_EXTRACT", "disabled"

    def __init__(self, *args, **kwargs):
        pass

    async def chat(self, messages, **kwargs):
        text = _resume_text(messages[-1]["content"])
        sections = _sections(text)
        first = (sections.get("general") or [""])[0]
        skills = [token.strip() for line in sections.get("skills", [])
                  for token in line.replace("，", "、").replace(",", "、").split("、")]
        value = dict(
            basic_info=dict(name=first, email=None, phone=None, location=None),
            skills={"languages": [s for s in skills if s], "tools": []},
            projects=[dict(name=line, tech_stack=[], key_contributions=[])
                      for line in sections.get("projects", [])],
            education=[dict(school=line) for line in sections.get("education", [])],
            work_experience=[dict(company=line) for line in sections.get("work", [])],
            internships=[dict(company=line) for line in sections.get("internship", [])],
            risk_points=[], synthetic_fixture=True,
        )
        return json.dumps(value, ensure_ascii=False)


class ReplayProfile(ModelProvider):
    """Selects facts from the *call-local evidence catalogue* the app builds.

    Every label is a substring of a real evidence excerpt and every citation is
    an alias the server minted for this call, so the normal validator still runs;
    nothing is written behind the service's back.
    """

    provider_name, model_name, thinking_type = "replay", "R17_RESUME_PROFILE", "disabled"

    def __init__(self, *args, **kwargs):
        pass

    CATEGORY = {"projects": "projects", "education": "education", "work": "work_experience",
                "internship": "internship_experience", "skills": "skills"}

    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        body = {category: [] for category in self.CATEGORY.values()}
        body["unknowns"] = []
        for item in payload.get("resume_evidence", []):
            category, excerpt, alias = self.CATEGORY.get(item.get("section")), item.get("excerpt", ""), item["evidence_id"]
            if not category or not excerpt.strip():
                continue
            if category == "skills":
                token = next((part.strip() for part in
                              excerpt.replace("，", "、").replace(",", "、").split("、")
                              if len(part.strip()) >= 2), excerpt[:40])
                body["skills"].append(dict(label=token, evidence_ids=[alias],
                                           proposed_evidence_state="MENTIONED"))
            else:
                body[category].append(dict(label=excerpt[:40].strip(), evidence_ids=[alias]))
        # Deterministic and bounded: one fact per evidence row, ordered by alias.
        for category in self.CATEGORY.values():
            body[category] = sorted(body[category], key=lambda row: row["evidence_ids"][0])[:20]
        return ModelResponse(json.dumps(body, ensure_ascii=False), dict(total_tokens=0))


def build_core(factory):
    from interview_api.infrastructure.models.router import (
        ANSWER_EVALUATION_ROUTE, INTERVIEW_MAIN_ROUTE, ModelRouter)
    from interview_api.modules.interview.core_service import MockInterviewService
    from v22_support import MemoryReplayProvider

    provider = MemoryReplayProvider()
    return MockInterviewService(factory, ModelRouter(
        {route: provider for route in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))


# ── round 18: the one live route pair, inside a hard ceiling ─────────────────
CAPTURE_DIR = Path(os.getenv("LIVE_CAPTURE_DIR", str(ROOT / ".runtime" / "r18" / "capture")))
LIVE_HOSTS = {"api-inference.modelscope.cn"}
# Round 20 tightens the ceiling per run; the numbers come from the CLI only.
LIVE_LIMIT_OVERRIDES = {}


def live_gate(name):
    """Build the gate and register its summary so it survives a Ctrl-C stop."""
    from live_model_gate import LIMITS, LiveGate

    limits = dict(LIMITS)
    limits.update({key: value for key, value in LIVE_LIMIT_OVERRIDES.items() if value})
    # A later round may only tighten the ceiling, never raise it.
    for key, cap in LIMITS.items():
        if limits[key] > cap:
            raise SystemExit(f"--live {key}={limits[key]} exceeds the hard ceiling {cap}")
        if limits[key] <= 0:
            raise SystemExit(f"--live {key} must be positive")
    gate = LiveGate(CAPTURE_DIR / name, limits=limits)
    print("LIVE_LIMITS " + json.dumps(limits))
    atexit.register(lambda: print("LIVE_GATE " + json.dumps(gate.summary(), ensure_ascii=False)))
    return gate


def only_configured_hosts():
    """Transport-level allowlist: covers httpx *and* the OpenAI SDK underneath."""
    import httpx

    original = httpx.AsyncHTTPTransport.handle_async_request

    async def guarded(self, request):
        if request.url.host not in LIVE_HOSTS:
            raise AssertionError(
                f"acceptance: outbound HTTP to {request.url.host} is prohibited (live mode "
                f"allows only {sorted(LIVE_HOSTS)})")
        return await original(self, request)

    return patch.object(httpx.AsyncHTTPTransport, "handle_async_request", guarded)


# ── fixtures ────────────────────────────────────────────────────────────────
async def seed(factory):
    from interview_api.core.security import hash_password
    from interview_api.modules.users.models import User
    from test_job_target import seed_user
    created = {}
    for role, email in EMAILS.items():
        user_id = await seed_user(factory)  # resume + real profile agent on replay
        async with factory() as db:
            user = await db.get(User, user_id)
            user.email, user.username = email, f"R11_{role}"
            user.password_hash = hash_password(PASSWORD)
            await db.commit()
        created[role] = dict(user_id=user_id, email=email)
    print(json.dumps(dict(seeded=created, password_hint="see script constant"), ensure_ascii=False))


async def history(factory, email, target_id, answers):
    """Extra private FULL sessions, one answered question each."""
    from sqlalchemy import select
    from interview_api.modules.users.models import User
    from interview_api.modules.interview.schemas import CreateSessionRequest
    async with factory() as db:
        user_id = (await db.scalar(select(User.id).where(User.email == email)))
    core = build_core(factory)
    states = []
    for answer in answers:
        request = CreateSessionRequest(job_target_id=target_id, interviewer_skill_id="java_backend",
            adaptive_interview=True, question_count=1, training_mode="FULL",
            training_request_key=uuid4().hex)
        session = await core.create(user_id, request)
        question = await core.start(session["id"], user_id)
        await core.answer(session["id"], user_id, question["question_id"], uuid4().hex, answer)
        from interview_api.modules.interview.training import TrainingService
        board = await TrainingService(factory).workbench(user_id, request)
        states.append([(t["knowledge_id"].split(":")[-1], t["state"], t["available_modes"])
                       for t in board["targets"]])
    print(json.dumps(states, ensure_ascii=False))


async def state(factory, email, target_id):
    from sqlalchemy import select
    from interview_api.modules.users.models import User
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.modules.interview.training import TrainingService
    async with factory() as db:
        user_id = await db.scalar(select(User.id).where(User.email == email))
    board = await TrainingService(factory).workbench(user_id, CreateSessionRequest(
        job_target_id=target_id, interviewer_skill_id="java_backend",
        adaptive_interview=True, question_count=8))
    print(json.dumps([(t["knowledge_id"], t["state"], t["available_modes"])
                      for t in board["targets"]], ensure_ascii=False))


# ── the API process ─────────────────────────────────────────────────────────
def verify_stack(target):
    """Re-read what was actually built. `engine.url` needs no connection."""
    from interview_api.infrastructure.db.engine import engine

    runtime_target.verify_settings(target)
    runtime_target.verify_engine(engine, target)
    runtime_target.verify_optional_clients(target)


def serve(target, *, live=False):
    import httpx
    import uvicorn
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.infrastructure.models.router import INTERVIEW_COACH_ROUTE, ModelRouter
    from interview_api.modules.interview import router as interview_router
    from interview_api.modules.interview_report import router as report_router
    from interview_api.modules.interview_report.service import InterviewReportService
    from interview_api.modules.job_target import service as job_target_service

    verify_stack(target)
    interview_router.build_core = lambda: build_core(async_session_factory)
    report_router.build_core = lambda: InterviewReportService(
        async_session_factory, ModelRouter({INTERVIEW_COACH_ROUTE: ReplayCoach()}))

    if live:
        # Only the JD extraction route is real; interview/coach stay replayed so
        # nothing can spend budget on a path this round does not exercise.
        from interview_api.infrastructure.models.router import EXTRACT_ROUTE as _EXTRACT, build_model_router as _build
        from live_model_gate import GatedProvider

        gate = live_gate("api")

        def live_jd_router():
            router = _build()
            router.routes[_EXTRACT] = GatedProvider(router.routes[_EXTRACT], gate, _EXTRACT)
            return router

        job_target_service.build_model_router = live_jd_router
        guard = only_configured_hosts()
    else:
        # JD extraction normally builds a router from settings; point it at replay.
        job_target_service.build_model_router = lambda: ModelRouter(
            {job_target_service.EXTRACT_ROUTE: ReplayExtract()})
        guard = patch("httpx.AsyncClient.request",
                      side_effect=AssertionError("acceptance: outbound HTTP is prohibited"))

    guard.start()
    print(f"acceptance API on 127.0.0.1:{target.api_port} database={target.database} "
          f"({'LIVE jd extraction, gated' if live else 'replay providers, outbound HTTP blocked'})")
    uvicorn.run("interview_api.main:app", host="127.0.0.1", port=target.api_port, log_level="info")


# ── the worker process ──────────────────────────────────────────────────────
def work(target, *, live=False):
    """Same real Celery worker; only the model boundary is replayed — unless live."""
    worker_src = ROOT / "apps" / "worker" / "src"
    if str(worker_src) not in sys.path:
        sys.path.insert(0, str(worker_src))
    from interview_api.infrastructure.llm.provider import OpenAICompatibleLLMProvider
    from interview_api.infrastructure.models.router import ModelRouter, RESUME_PROFILE_ROUTE
    from interview_worker.celery_app import app

    verify_stack(target)
    hostname = "iap-acceptance@localhost"

    if live:
        from interview_api.infrastructure.models.router import build_resume_profile_router
        from live_model_gate import GatedProvider, gated_chat_provider

        gate = live_gate("worker")

        def live_profile_router():
            router = build_resume_profile_router()
            router.routes[RESUME_PROFILE_ROUTE] = GatedProvider(
                router.routes[RESUME_PROFILE_ROUTE], gate, RESUME_PROFILE_ROUTE)
            return router

        patches = [
            patch("interview_api.infrastructure.llm.provider.OpenAICompatibleLLMProvider",
                  gated_chat_provider(OpenAICompatibleLLMProvider, gate)),
            patch("interview_api.modules.candidate_profile.agent.build_resume_profile_router",
                  live_profile_router),
            only_configured_hosts(),
        ]
        mode = "LIVE resume extraction + profile selection, gated"
    else:
        replay_router = lambda: ModelRouter({RESUME_PROFILE_ROUTE: ReplayProfile()})
        patches = [
            patch("interview_api.infrastructure.llm.provider.OpenAICompatibleLLMProvider", ReplayResumeExtract),
            patch("interview_api.modules.candidate_profile.agent.build_resume_profile_router", replay_router),
            patch("httpx.AsyncClient.request",
                  side_effect=AssertionError("acceptance: outbound HTTP is prohibited")),
        ]
        mode = "replay models"

    with patches[0], patches[1], patches[2]:
        print(f"acceptance worker {hostname} database={target.database} "
              f"broker={target.describe()['redis_databases'][1]} ({mode})")
        app.worker_main(["worker", "--pool=solo", "--loglevel=INFO",
                         "--hostname", hostname])


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "history", "api", "worker", "state", "verify"])
    # Deliberately not `required=True`: the refusal has to be ours, explicit, and
    # identical whether the flag is missing or names the wrong database.
    parser.add_argument("--database", default=None)
    parser.add_argument("--email", default=EMAILS["owner"])
    parser.add_argument("--target", type=int)
    parser.add_argument("--answers", default="GOOD,GOOD,WEAK,GOOD,WEAK,WEAK")
    parser.add_argument("--api-port", type=int, default=8000)
    # Round 18: real models stay opt-in, and only for the four input-quality
    # calls. Default is unchanged replay behaviour.
    parser.add_argument("--live", action="store_true",
                        help="real model calls for JD extraction / resume profile, gated by live_model_gate")
    parser.add_argument("--bucket", default=None, help="MinIO bucket for this round's isolation")
    # Round 20: this batch is capped at 6 calls / 60k tokens / 15 minutes. These
    # flags can only tighten live_model_gate.LIMITS, never raise them.
    parser.add_argument("--live-max-calls", type=int, default=None)
    parser.add_argument("--live-max-tokens", type=int, default=None)
    parser.add_argument("--live-max-minutes", type=int, default=None)
    args = parser.parse_args(argv)

    global LIVE_LIMIT_OVERRIDES
    LIVE_LIMIT_OVERRIDES = dict(
        max_calls=args.live_max_calls,
        max_tokens=args.live_max_tokens,
        max_seconds=args.live_max_minutes * 60 if args.live_max_minutes else None)
    if any(LIVE_LIMIT_OVERRIDES.values()) and not args.live:
        print("refusing to accept live budget flags without --live", file=sys.stderr)
        return 3

    try:
        target = runtime_target.resolve(args.database, base_url=settings.database_url,
                                        minio_bucket=args.bucket)
    except runtime_target.RuntimeTargetError as refused:
        print(str(refused), file=sys.stderr)
        return 3
    target = dataclasses.replace(target, api_port=args.api_port)
    runtime_target.apply(target)
    announce(target)

    if args.action == "verify":
        # Proves the resolution/apply path without importing an engine at all.
        return 0
    if args.action == "api":
        try:
            serve(target, live=args.live)
        except runtime_target.RuntimeTargetError as refused:
            print(str(refused), file=sys.stderr)
            return 3
        return 0
    if args.action == "worker":
        try:
            work(target, live=args.live)
        except runtime_target.RuntimeTargetError as refused:
            print(str(refused), file=sys.stderr)
            return 3
        return 0

    engine, factory = database(target)

    async def run():
        try:
            if args.action == "seed":
                await seed(factory)
            elif args.action == "history":
                mapping = {"GOOD": GOOD, "WEAK": WEAK}
                await history(factory, args.email, args.target,
                              [mapping[name] for name in args.answers.split(",")])
            else:
                await state(factory, args.email, args.target)
        finally:
            await engine.dispose()

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
