"""Bounded offline tests against a dedicated V2.1 database; never the business DB."""
import argparse
import asyncio
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))
from interview_api.core.config import settings
from sqlalchemy.engine import make_url

parser = argparse.ArgumentParser()
parser.add_argument("--database", required=True)
parser.add_argument("--prepare", action="store_true")
parser.add_argument("--recorded-v142-database")
parser.add_argument("files", nargs="*", default=["tests/test_v21_adaptive.py"])
args = parser.parse_args()
if not args.database.startswith("interview_agent_v21_") or not args.database.replace("_", "").isalnum():
    raise SystemExit("Dedicated V2.1 database required")
if any(not f.startswith("tests/") or ".." in f for f in args.files):
    raise SystemExit("Only repository tests are allowed")

async def prepare():
    import asyncpg
    conn = await asyncpg.connect(make_url(settings.database_url).set(drivername="postgresql", database="postgres").render_as_string(hide_password=False), timeout=5)
    try:
        if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", args.database):
            raise RuntimeError("Database exists; do not overwrite earlier attempts")
        await conn.execute(f'CREATE DATABASE "{args.database}"')
    finally:
        await conn.close()

if args.prepare:
    asyncio.run(prepare())
url = make_url(settings.database_url).set(database=args.database).render_as_string(hide_password=False)
env = {**os.environ, "APP_ENV": "test", "PYTHONUTF8": "1", "DATABASE_URL": url, "M1_TEST_DATABASE_URL": url}
if args.recorded_v142_database:
    if not args.recorded_v142_database.startswith("interview_agent_v14_acceptance_live_") or not args.recorded_v142_database.replace("_", "").isalnum():
        raise SystemExit("Only a retained isolated LIVE acceptance database may be read")
    env["V142_RECORDED_DATABASE_URL"] = make_url(settings.database_url).set(database=args.recorded_v142_database).render_as_string(hide_password=False)
subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "apps/api", env=env, check=True, timeout=90)
raise SystemExit(subprocess.run([sys.executable, "-m", "pytest", "-q", *args.files, "-p", "no:cacheprovider"],
    cwd=ROOT / "apps/api", env=env, timeout=175).returncode)
