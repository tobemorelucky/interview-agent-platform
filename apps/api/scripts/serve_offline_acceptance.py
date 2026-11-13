"""Offline acceptance API for the browser E2E: real services, replay providers.

Round 9 drove the browser against the real provider and spent 12 real model
calls. Round 11 allows none, so this launcher substitutes the model boundary and
nothing else: the same FastAPI app, the same services, transactions, ownership
checks and UI, with every agent backed by a deterministic replay provider and all
outbound HTTP blocked.

It refuses to run against anything but an `*_e2e_*` database (see
`interview_api.core.db_targets`), so it can never seed or serve the production or
an archived Live database.

    python apps/api/scripts/serve_offline_acceptance.py seed  --database interview_agent_wb_r11_e2e_20261112
    python apps/api/scripts/serve_offline_acceptance.py api   --database interview_agent_wb_r11_e2e_20261112
    python apps/api/scripts/serve_offline_acceptance.py history --database ... --email ... --target 3
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

API = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(API / "src"), str(API / "tests")]

from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from interview_api.core.config import settings  # noqa: E402
from interview_api.core.db_targets import classify, e2e_problem, E2E  # noqa: E402

PASSWORD = "r11-acceptance-only"
EMAILS = {"owner": "r11.owner@example.com", "intruder": "r11.intruder@example.com"}
GOOD = "Kafka 事务和唯一键，提交后 ACK，失败恢复"
WEAK = "Kafka ACK，失败重试"


def guard(name):
    if not name.replace("_", "").isalnum() or classify(name) != E2E:
        raise SystemExit("only an *_e2e_* database may be served or seeded")
    problem = e2e_problem(name)
    if problem:
        raise SystemExit(problem)


def database(name):
    engine = create_async_engine(make_url(settings.database_url).set(database=name))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


# ── replay providers: the only substitution in this harness ──────────────────
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse  # noqa: E402


class ReplayExtract(ModelProvider):
    """Echoes the pasted JD as requirements; every evidence is verbatim."""

    provider_name, model_name = "replay", "R11_EXTRACT"

    async def generate(self, messages, *, schema):
        raw = json.loads(messages[-1]["content"])["input"]["input"]["raw_text"]
        lines = [line.strip(" -•\t") for line in raw.splitlines() if len(line.strip()) > 4]
        requirements = [dict(statement=line[:40], importance="REQUIRED", evidence=line,
                             topic="Kafka" if "Kafka" in line else "Python" if "Python" in line else None)
                        for line in lines if line.endswith(("。", "；", ";", ".")) or "熟悉" in line or "掌握" in line]
        value = dict(company_name=None, job_title=None,
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


def build_core(factory):
    from interview_api.infrastructure.models.router import (
        ANSWER_EVALUATION_ROUTE, INTERVIEW_MAIN_ROUTE, ModelRouter)
    from interview_api.modules.interview.core_service import MockInterviewService
    from v22_support import MemoryReplayProvider

    provider = MemoryReplayProvider()
    return MockInterviewService(factory, ModelRouter(
        {route: provider for route in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))


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


# ── the API process ─────────────────────────────────────────────────────────
def serve():
    import httpx
    import uvicorn
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.infrastructure.models.router import INTERVIEW_COACH_ROUTE, ModelRouter
    from interview_api.modules.interview import router as interview_router
    from interview_api.modules.interview_report import router as report_router
    from interview_api.modules.interview_report.service import InterviewReportService
    from interview_api.modules.job_target import service as job_target_service

    interview_router.build_core = lambda: build_core(async_session_factory)
    report_router.build_core = lambda: InterviewReportService(
        async_session_factory, ModelRouter({INTERVIEW_COACH_ROUTE: ReplayCoach()}))
    # JD extraction normally builds a router from settings; point it at replay.
    job_target_service.build_model_router = lambda: ModelRouter({job_target_service.EXTRACT_ROUTE: ReplayExtract()})

    blocker = patch("httpx.AsyncClient.request",
                    side_effect=AssertionError("R11 acceptance: outbound HTTP is prohibited"))
    blocker.start()
    print("R11 acceptance API on 127.0.0.1:8000 (replay providers, outbound HTTP blocked)")
    uvicorn.run("interview_api.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "history", "api", "state"])
    parser.add_argument("--database", required=True)
    parser.add_argument("--email", default=EMAILS["owner"])
    parser.add_argument("--target", type=int)
    parser.add_argument("--answers", default="GOOD,GOOD,WEAK,GOOD,WEAK,WEAK")
    args = parser.parse_args()

    guard(args.database)
    engine, factory = database(args.database)
    if args.action == "api":
        asyncio.run(engine.dispose())
        serve()
    else:
        async def main():
            try:
                if args.action == "seed":
                    await seed(factory)
                elif args.action == "history":
                    mapping = {"GOOD": GOOD, "WEAK": WEAK}
                    await history(factory, args.email, args.target,
                                  [mapping[name] for name in args.answers.split(",")])
                elif args.action == "state":
                    from interview_api.modules.interview.training import TrainingService
                    from sqlalchemy import select
                    from interview_api.modules.users.models import User
                    async with factory() as db:
                        user_id = await db.scalar(select(User.id).where(User.email == args.email))
                    from interview_api.modules.interview.schemas import CreateSessionRequest
                    board = await TrainingService(factory).workbench(user_id, CreateSessionRequest(
                        job_target_id=args.target, interviewer_skill_id="java_backend",
                        adaptive_interview=True, question_count=8))
                    print(json.dumps([(t["knowledge_id"], t["state"], t["available_modes"])
                                      for t in board["targets"]], ensure_ascii=False))
            finally:
                await engine.dispose()
        asyncio.run(main())
