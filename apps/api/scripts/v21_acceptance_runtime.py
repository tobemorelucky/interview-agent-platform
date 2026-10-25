"""Isolated real API/Vue browser acceptance. Only model I/O is explicitly Replay."""
import argparse
import asyncio
import os
from pathlib import Path
import sys

API = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(API / "src"), str(API / "tests")]
from sqlalchemy.engine import make_url
from interview_api.core.config import settings

parser = argparse.ArgumentParser()
parser.add_argument("--database", required=True)
parser.add_argument("--prepare", action="store_true")
parser.add_argument("--memory-replay", action="store_true", help="V2.2 fixed memory Replay; no LIVE model")
args = parser.parse_args()
if not args.database.startswith("interview_agent_v21_") or not args.database.replace("_", "").isalnum():
    raise SystemExit("Dedicated V2.1 database required")
url = make_url(settings.database_url).set(database=args.database).render_as_string(hide_password=False)

async def prepare():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import select
    from test_m4_interview import seed
    from interview_api.modules.users.models import User
    from interview_api.core.security import hash_password
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            if await db.scalar(select(User.id).where(User.email == "v21.demo@example.com")):
                raise RuntimeError("DEMO account already exists; do not overwrite")
        if args.memory_replay:
            from v22_support import kafka_seed
            uid, _ = await kafka_seed(factory)
        else:
            _, _, _, uid, _ = await seed(factory, budget=4)
        async with factory() as db:
            user = await db.get(User, uid)
            user.email, user.username = "v21.demo@example.com", "DEMO_V21_ACCEPTANCE"
            user.password_hash = hash_password("DEMO-v21-local-only")
            await db.commit()
        print("Prepared DEMO account in isolated database " + args.database)
    finally:
        await engine.dispose()

if args.prepare:
    asyncio.run(prepare())
else:
    os.environ.update(APP_ENV="test", DATABASE_URL=url)
    settings.database_url = url
    from interview_api.main import app
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.infrastructure.models.provider import CompatibleModelProvider
    from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE
    from interview_api.modules.interview import router as interviews
    from interview_api.modules.interview_report import router as reports
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview_report.service import InterviewReportService
    from v21_support import AdaptiveReplayProvider
    async def forbidden(*a, **k): raise RuntimeError("LIVE model disabled in V2.1 Replay acceptance")
    CompatibleModelProvider.generate = forbidden
    if args.memory_replay:
        from v22_support import MemoryReplayProvider
        provider = MemoryReplayProvider()
    else:
        provider = AdaptiveReplayProvider()
    router = ModelRouter({route: provider for route in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE)})
    interviews.build_core = lambda: MockInterviewService(async_session_factory, router)
    reports.build_core = lambda: InterviewReportService(async_session_factory, router)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8007, log_level="warning")
