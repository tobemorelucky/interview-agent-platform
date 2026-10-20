"""Local-only real API + isolated PostgreSQL + explicit Replay model acceptance.

Never points at the formal database. No authentication bypass or HTTP fixtures.
"""
import argparse
import asyncio
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
parser = argparse.ArgumentParser()
parser.add_argument("--database", required=True)
parser.add_argument("--prepare", action="store_true")
args = parser.parse_args()
if not args.database.startswith("interview_agent_v12_acceptance_") or not args.database.replace("_", "").isalnum():
    raise SystemExit("Dedicated V1.2 acceptance database required")
from sqlalchemy.engine import make_url
from interview_api.core.config import settings
test_url = make_url(settings.database_url).set(database=args.database)

async def prepare():
    import asyncpg
    conn = await asyncpg.connect(make_url(settings.database_url).set(drivername="postgresql", database="postgres").render_as_string(hide_password=False), timeout=5)
    try:
        if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", args.database):
            raise RuntimeError("Database exists; never clear or overwrite an earlier attempt")
        await conn.execute(f'CREATE DATABASE "{args.database}"')
    finally: await conn.close()
    env = {**os.environ, "APP_ENV": "test", "DATABASE_URL": test_url.render_as_string(hide_password=False)}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True, timeout=90)
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from test_m4_interview import seed
    from interview_api.modules.users.models import User
    from interview_api.modules.public_career.models import Company, Job
    from interview_api.core.security import hash_password
    engine = create_async_engine(test_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, _, _, uid, detail = await seed(factory)
        async with factory() as db:
            user = await db.get(User, uid)
            user.email, user.username = "v12.demo@example.com", "DEMO_V12_ACCEPTANCE"
            user.password_hash = hash_password("DEMO-v12-local-only")
            company = await db.get(Company, detail["context_snapshot"]["company_id"])
            company.canonical_name = "DEMO V1.2 验收公司"
            job = await db.get(Job, detail["context_snapshot"]["job_id"])
            job.canonical_title = "Java后端工程师"
            await db.commit()
        print("Prepared isolated DEMO database: " + args.database)
    finally: await engine.dispose()

if args.prepare:
    asyncio.run(prepare())
else:
    # Process-only configuration; the repository .env and model settings are untouched.
    os.environ.update(APP_ENV="test", DATABASE_URL=test_url.render_as_string(hide_password=False))
    settings.database_url = test_url.render_as_string(hide_password=False)
    from interview_api.main import app
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.infrastructure.models.provider import CompatibleModelProvider
    from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE
    from interview_api.modules.interview import router as interview_routes
    from interview_api.modules.interview_report import router as report_routes
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview_report.service import InterviewReportService
    from v12_support import DesignReplayProvider
    async def forbidden(*a, **k): raise RuntimeError("Live model disabled in DEMO acceptance runtime")
    CompatibleModelProvider.generate = forbidden
    provider = DesignReplayProvider()
    router = ModelRouter({r: provider for r in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE)})
    interview_routes.build_core = lambda: MockInterviewService(async_session_factory, router)
    report_routes.build_core = lambda: InterviewReportService(async_session_factory, router)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8005, log_level="warning")
