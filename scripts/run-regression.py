"""Run full PostgreSQL regression in a newly named EMPTY dedicated test database."""
import argparse
import asyncio
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api/src'))
from interview_api.core.config import settings
from sqlalchemy.engine import make_url

parser = argparse.ArgumentParser()
parser.add_argument('--database', required=True)
args = parser.parse_args()
if not args.database.startswith('interview_agent_f4_regression_') or not args.database.replace('_','').isalnum():
    raise SystemExit('Only a dedicated F4 regression database is allowed')

async def prepare():
    import asyncpg
    url = make_url(settings.database_url).set(drivername='postgresql', database='postgres')
    conn = await asyncpg.connect(url.render_as_string(hide_password=False))
    try:
        if await conn.fetchval('SELECT 1 FROM pg_database WHERE datname=$1', args.database):
            raise RuntimeError('Test database already exists; use a new name, never clear old attempts')
        await conn.execute(f'CREATE DATABASE "{args.database}"')
    finally:
        await conn.close()
asyncio.run(prepare())
env = os.environ.copy()
env['APP_ENV'] = 'test'
env['DATABASE_URL'] = make_url(settings.database_url).set(database=args.database).render_as_string(hide_password=False)
env['M1_TEST_DATABASE_URL'] = env['DATABASE_URL']
# Isolated keys are never printed or committed; no real models are used by regression.
raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q', 'tests', '-p', 'no:cacheprovider'],
                                cwd=ROOT / 'apps/api', env=env))
