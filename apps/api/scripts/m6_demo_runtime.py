"""Explicit fixture-only API runtime for UI acceptance; never production fallback.

Requires a migrated dedicated m6_ database. Outgoing HTTP is blocked in seed/API.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch
from sqlalchemy.engine import make_url
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from interview_api.core.config import settings
from interview_api.core.security import hash_password
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
from interview_api.infrastructure.models.router import ModelRouter, JOB_MATCHING_ROUTE, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
from interview_api.modules.job_matching.service import JobMatchingService
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.users.models import User
from test_m6_job_matching import seed_matching, artifact
from test_m4_interview import question, evaluation


class DemoMatchProvider(ModelProvider):
    provider_name, model_name = "replay", "m6_fixture_demo_matching_v1"
    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        if payload["data_mode"] != "DEMO_REPLAY" or any(not c["demo"] for c in payload["candidates"]):
            raise ValueError("REPLAY_FIXTURE_ONLY")
        return ModelResponse(json.dumps(artifact({"snapshot": payload}, reverse=True), ensure_ascii=False), {"total_tokens": 0})


class DemoMainProvider(ModelProvider):
    provider_name, model_name = "replay", "m6_fixture_demo_interview_main_v1"
    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        if not payload["snapshot"]["company_name"].startswith("M6 DEMO REPLAY"): raise ValueError("REPLAY_FIXTURE_ONLY")
        result = question(payload["snapshot"], payload["preferred_source"], payload["requested_action"], payload["desired_difficulty"] or "MEDIUM")
        return ModelResponse(json.dumps(result, ensure_ascii=False), {"total_tokens": 0})


class DemoEvaluationProvider(ModelProvider):
    provider_name, model_name = "replay", "m6_fixture_demo_evaluation_v1"
    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        if not payload["snapshot"]["company_name"].startswith("M6 DEMO REPLAY"): raise ValueError("REPLAY_FIXTURE_ONLY")
        return ModelResponse(json.dumps(evaluation("NEXT", 4), ensure_ascii=False), {"total_tokens": 0})


async def seed_demo():
    core, provider, uid, jobs, _, _, run = await seed_matching(async_session_factory)
    provider.responses.append(artifact(run, reverse=True))
    final = await core.generate(uid, run["id"])
    assert final["status"] == "COMPLETED"
    email, password = f"m6-demo-{uid}@example.com", "M6ReplayFixtureDemo!"
    async with async_session_factory() as db:
        user = await db.get(User, uid)
        user.email, user.password_hash = email, hash_password(password)
        await db.commit()
    print(json.dumps(dict(mode="DEMO / REPLAY / fixture, not Live", user_id=uid, email=email, password=password,
        run_id=run["id"], company_id=jobs[0]["company_id"], job_ids=[j["job_id"] for j in jobs]), indent=2))


if __name__ == "__main__":
    if not (make_url(settings.database_url).database or "").startswith("interview_agent_m6_"):
        raise SystemExit("Use an explicitly isolated interview_agent_m6_ database")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "api"])
    args = parser.parse_args()
    with patch("httpx.AsyncClient.request", side_effect=AssertionError("Demo outgoing HTTP is forbidden")):
        if args.action == "seed": asyncio.run(seed_demo())
        else:
            import uvicorn
            from interview_api.modules.job_matching import router as match_router
            from interview_api.modules.interview import router as interview_router
            matches = ModelRouter({JOB_MATCHING_ROUTE: DemoMatchProvider()})
            interviews = ModelRouter({INTERVIEW_MAIN_ROUTE: DemoMainProvider(), ANSWER_EVALUATION_ROUTE: DemoEvaluationProvider()})
            match_router.build_core = lambda: JobMatchingService(async_session_factory, matches)
            interview_router.build_core = lambda: MockInterviewService(async_session_factory, interviews)
            print("DEMO / REPLAY / fixture API, outbound HTTP blocked; NOT Real Model acceptance")
            uvicorn.run("interview_api.main:app", host="127.0.0.1", port=8000)
