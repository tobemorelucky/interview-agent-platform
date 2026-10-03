"""Explicit offline REPLAY/FIXTURE/DEMO API; never a production fallback.

Seed a completed M4 interview, then use the real Report/approval UI and DB.
Only an isolated m5_ database is accepted. All outgoing HTTP is blocked.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from interview_api.core.config import settings
from interview_api.core.security import hash_password
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_COACH_ROUTE, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview_report.service import InterviewReportService
from interview_api.modules.users.models import User
from test_m4_interview import seed, queue_start, queue_answer
from test_m5_two_session_e2e import coach_fixture
from m4_demo_runtime import DemoMainProvider, DemoEvaluationProvider


class DemoCoachProvider(ModelProvider):
    provider_name, model_name = "replay", "m5_fixture_demo_coach_v1"

    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        if not payload["snapshot"]["company_name"].startswith("M4 Replay Fixture") or len(payload["evaluations"]) < 6:
            raise ValueError("REPLAY_FIXTURE_ONLY")
        return ModelResponse(json.dumps(coach_fixture(payload["evaluations"]), ensure_ascii=False),
            {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0})


async def seed_demo():
    core, main, evaluator, uid, detail = await seed(async_session_factory)
    sid = detail["id"]
    await queue_start(core, main, uid, sid)
    for action, score in (("FOLLOW_UP", 2), ("NEXT", 3), ("DEEP_DIVE", 4), ("ADJUST_DIFFICULTY", 5), ("NEXT", 4), ("COMPLETE", 1)):
        request = await queue_answer(core, main, evaluator, uid, sid, action, score)
        await core.answer(sid, uid, request.question_id, request.idempotency_key, request.content)
    email, password = f"m5-demo-{uid}@example.com", "M5ReplayFixtureDemo!"
    async with async_session_factory() as db:
        user = await db.get(User, uid)
        user.email, user.password_hash = email, hash_password(password)
        await db.commit()
    print(json.dumps(dict(mode="replay / fixture / demo", email=email, password=password,
        completed_session_id=sid, company_id=detail["context_snapshot"]["company_id"],
        job_id=detail["context_snapshot"]["job_id"], company_name=detail["context_snapshot"]["company_name"]), indent=2))


if __name__ == "__main__":
    if "m5_" not in settings.database_url:
        raise SystemExit("Use an explicitly isolated m5_ smoke/test DATABASE_URL")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "api"])
    args = parser.parse_args()
    with patch("httpx.AsyncClient.request", side_effect=AssertionError("Demo outgoing HTTP is forbidden")):
        if args.action == "seed":
            asyncio.run(seed_demo())
        else:
            import uvicorn
            from interview_api.modules.interview import router as interview_router
            from interview_api.modules.interview_report import router as report_router
            replay_interview = ModelRouter({INTERVIEW_MAIN_ROUTE: DemoMainProvider(), ANSWER_EVALUATION_ROUTE: DemoEvaluationProvider()})
            replay_coach = ModelRouter({INTERVIEW_COACH_ROUTE: DemoCoachProvider()})
            interview_router.build_core = lambda: MockInterviewService(async_session_factory, replay_interview)
            report_router.build_core = lambda: InterviewReportService(async_session_factory, replay_coach)
            print("REPLAY / FIXTURE / DEMO only, outgoing HTTP blocked; NOT Live model acceptance")
            uvicorn.run("interview_api.main:app", host="127.0.0.1", port=8000)
