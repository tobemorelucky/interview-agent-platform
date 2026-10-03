"""Explicit REPLAY/FIXTURE/DEMO only. Real API/auth/SSE/DB; no live model/network.

uv run python scripts/m4_demo_runtime.py seed|api
Requires dedicated migrated m4_ DATABASE_URL. Never a production fallback.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from interview_api.core.config import settings
from interview_api.core.security import hash_password
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.users.models import User
from test_m4_interview import seed, question, evaluation


class DemoMainProvider(ModelProvider):
    provider_name, model_name = "replay", "m4_fixture_demo_main_v1"

    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        if not payload["snapshot"]["company_name"].startswith("M4 Replay Fixture"):
            raise ValueError("REPLAY_FIXTURE_ONLY")
        result = question(payload["snapshot"], payload["preferred_source"], payload["requested_action"], payload["desired_difficulty"] or "MEDIUM")
        return ModelResponse(json.dumps(result, ensure_ascii=False), {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0})


class DemoEvaluationProvider(ModelProvider):
    provider_name, model_name = "replay", "m4_fixture_demo_evaluation_v1"

    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        if not payload["snapshot"]["company_name"].startswith("M4 Replay Fixture"):
            raise ValueError("REPLAY_FIXTURE_ONLY")
        index = payload["current_question"]["question_index"]
        action, score = (("FOLLOW_UP", 2), ("NEXT", 3), ("DEEP_DIVE", 4), ("ADJUST_DIFFICULTY", 5), ("NEXT", 4), ("COMPLETE", 1))[min(index, 5)]
        return ModelResponse(json.dumps(evaluation(action, score), ensure_ascii=False), {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0})


async def seed_demo():
    _, _, _, uid, detail = await seed(async_session_factory)
    email, password = f"m4-demo-{uid}@example.com", "M4ReplayFixtureDemo!"
    async with async_session_factory() as db:
        user = await db.get(User, uid)
        user.email, user.password_hash = email, hash_password(password)
        await db.commit()
    print(json.dumps(dict(mode="replay / fixture / demo", email=email, password=password,
                         session_id=detail["id"], job_id=detail["context_snapshot"]["job_id"],
                         company_name=detail["context_snapshot"]["company_name"]), indent=2))


if __name__ == "__main__":
    if "m4_" not in settings.database_url:
        raise SystemExit("Use dedicated m4_ smoke/test database")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "api"])
    args = parser.parse_args()
    if args.action == "seed":
        asyncio.run(seed_demo())
    else:
        import uvicorn
        from interview_api.modules.interview import router as interview_router
        replay_router = ModelRouter({INTERVIEW_MAIN_ROUTE: DemoMainProvider(), ANSWER_EVALUATION_ROUTE: DemoEvaluationProvider()})
        interview_router.build_core = lambda: MockInterviewService(async_session_factory, replay_router)
        print("REPLAY / FIXTURE / DEMO model runtime; NOT Live model acceptance")
        uvicorn.run("interview_api.main:app", host="127.0.0.1", port=8000)
