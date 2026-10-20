"""Isolated real API/database/browser runtime; Web + models explicitly REPLAY.

Never uses formal user data or network models. Refuses to recreate existing DBs.
"""
import argparse
import asyncio
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
parser = argparse.ArgumentParser()
parser.add_argument('--database', required=True)
parser.add_argument('--prepare', action='store_true')
parser.add_argument('--test', action='store_true')
parser.add_argument('--review-live', action='store_true', help='Review existing LIVE artifacts only; all model calls disabled')
args = parser.parse_args()
if not args.database.startswith('interview_agent_v14_acceptance_') or not args.database.replace('_', '').isalnum():
    raise SystemExit('Dedicated V1.4 acceptance database required')
from sqlalchemy.engine import make_url
from interview_api.core.config import settings
test_url = make_url(settings.database_url).set(database=args.database).render_as_string(hide_password=False)


async def prepare():
    import asyncpg
    conn = await asyncpg.connect(make_url(settings.database_url).set(drivername='postgresql', database='postgres').render_as_string(hide_password=False), timeout=5)
    try:
        if await conn.fetchval('SELECT 1 FROM pg_database WHERE datname=$1', args.database):
            raise RuntimeError('Database exists; never clear an earlier attempt')
        await conn.execute(f'CREATE DATABASE "{args.database}"')
    finally: await conn.close()
    env = {**os.environ, 'APP_ENV': 'test', 'DATABASE_URL': test_url}
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ROOT, env=env, check=True, timeout=90)
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from interview_api.modules.auth.service import AuthService
    import interview_api.modules.models
    engine = create_async_engine(test_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            for role, email, name in [('ADMIN', 'v14.admin@example.com', 'DEMO_V14_ADMIN'),
                    ('USER', 'v14.a@example.com', 'DEMO_V14_USER_A'), ('USER', 'v14.b@example.com', 'DEMO_V14_USER_B')]:
                await AuthService(db).register(email, name, 'DEMO-v14-local-only', role)
            await db.commit()
        print('Prepared isolated REPLAY database: ' + args.database)
    finally: await engine.dispose()


if args.prepare:
    asyncio.run(prepare())
elif args.test:
    env = {**os.environ, 'APP_ENV': 'test', 'DATABASE_URL': test_url, 'M1_TEST_DATABASE_URL': test_url}
    raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q', 'tests/test_v14_web_provider.py',
        'tests/test_v14_web_collection.py', '-p', 'no:cacheprovider'], cwd=ROOT, env=env))
else:
    os.environ.update(APP_ENV='test', DATABASE_URL=test_url)
    settings.database_url = test_url
    # Process-local restrictions; actual .env/model routing remains untouched.
    if args.review_live:
        if not args.database.startswith('interview_agent_v14_acceptance_live_'):
            raise SystemExit('LIVE review requires a dedicated live acceptance database')
        settings.deepseek_web_daily_search_limit = 2
        settings.deepseek_web_daily_fetch_limit = 2
        settings.deepseek_web_retries = 0
    else:
        settings.deepseek_web_api_key = 'DEMO-REPLAY-no-live-key'
    from interview_api.main import app
    from interview_api.infrastructure.models.provider import CompatibleModelProvider
    from interview_api.modules.public_career import web_router
    from interview_api.modules.public_career.web_collection import WebCollectionService
    from interview_api.infrastructure.db.session import async_session_factory
    if not args.review_live:
        from v14_support import FixtureWeb, FixtureResearchAgent
        web = FixtureWeb('browser-demo')
        app.dependency_overrides[web_router.get_web_service] = lambda: WebCollectionService(async_session_factory, web.gateway)
        web_router.PublicDataResearchAgent = FixtureResearchAgent
    async def forbidden(*args, **kwargs): raise RuntimeError('Additional LIVE models disabled in acceptance review runtime')
    CompatibleModelProvider.generate = forbidden
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8005, log_level='warning')
