"""Run the backend suite against a WorkBuddy-owned isolated database.

Round 9 showed how easy it is to point a regression run at the wrong database:
the E2E database received 219 ACTIVE companies and 347 PUBLISHED job rows from
test fixtures. `apps/api/tests/conftest.py` now refuses to write into a database
whose name is not clearly a test database, but that guard only helps once you
are already inside pytest. This wrapper closes the earlier half of the hole: it
refuses to *aim* a run at the production database, the E2E database or an
archived Live database in the first place, creates a fresh database when asked,
migrates that one database only, and then runs the suite.

Examples:
    python scripts/run-workbuddy-tests.py --database interview_agent_wb_r10_test_20261112
    python scripts/run-workbuddy-tests.py --database interview_agent_v21_wb_r10_20261112 --migration
    python scripts/run-workbuddy-tests.py --database interview_agent_wb_r10_test_20261112 --reuse -- tests/test_private_training.py

The database name is the whole safety mechanism, so it is checked twice: here
before anything runs, and again by the pytest session guard.
"""

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
PYTHON = API / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():  # non-Windows checkout
    PYTHON = API / ".venv" / "bin" / "python"
sys.path.insert(0, str(API / "src"))

# A pytest database must announce itself. `_e2e` is deliberately absent: the
# browser acceptance database is driven through the API, never through fixtures.
PYTEST_MARKERS = ("_test", "_regression", "_mig")
MIGRATION_PREFIX = "interview_agent_v21_"
GENERAL_PREFIX = "interview_agent_wb_"
# Anything else is a production / archived-Live / Demo database: read-only.
NEVER = ("e2e", "live", "accept", "demo", "browser", "prod")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--database", required=True, help="isolated database name")
parser.add_argument("--reuse", action="store_true", help="use the existing database instead of creating it")
parser.add_argument("--migration", action="store_true", help="run the empty-database migration suite")
parser.add_argument("--create-only", action="store_true", help="create and migrate, then stop")
parser.add_argument("tests", nargs="*", help="pytest arguments after --")
args = parser.parse_args()

name = args.database
if not name.replace("_", "").isalnum() or name.lower() != name:
    raise SystemExit("database names are lowercase word characters only")
if not (name.startswith(GENERAL_PREFIX) or name.startswith(MIGRATION_PREFIX)):
    raise SystemExit(f"only {GENERAL_PREFIX}* or {MIGRATION_PREFIX}* databases may be used")
if not any(marker in name for marker in PYTEST_MARKERS):
    raise SystemExit(f"the name must contain one of {PYTEST_MARKERS} so the pytest guard accepts it")
if any(word in name for word in NEVER):
    raise SystemExit(f"'{name}' looks like an acceptance, Demo or archived Live database; refusing")

from sqlalchemy.engine import make_url  # noqa: E402

from interview_api.core.config import settings  # noqa: E402

base = make_url(settings.database_url)
url = base.set(database=name).render_as_string(hide_password=False)


async def prepare():
    import asyncpg

    admin = base.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    connection = await asyncpg.connect(admin)
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name)
        if exists and not args.reuse:
            raise SystemExit(f"'{name}' already exists; pass --reuse or pick a new name "
                             "(this command never drops or clears a database)")
        if not exists:
            await connection.execute(f'CREATE DATABASE "{name}"')
            print(f"created {name}")
    finally:
        await connection.close()


asyncio.run(prepare())

env = os.environ.copy()
env["DATABASE_URL"] = url
env["M1_TEST_DATABASE_URL"] = url
subprocess.run([str(PYTHON), "-m", "alembic", "upgrade", "head"], cwd=API, env=env, check=True)
print(f"migrated {name} to head")
if args.create_only:
    raise SystemExit(0)

# The same URL in both variables is what the pytest guard requires; a mismatch
# means the fixtures would write somewhere other than the database just prepared.
#
# The migration cases upgrade an empty database from nothing and then downgrade
# it, so they cannot share a database with data-bearing tests. Two passes:
# `--migration` runs only those, the default run ignores them.
MIGRATION_TESTS = [
    "tests/test_00_migration.py", "tests/test_01_m2a_migration.py",
    "tests/test_02_m2b_migration.py", "tests/test_03_m2b_migration_data.py",
    "tests/test_04_m3_migration.py", "tests/test_05_m4_migration.py",
    "tests/test_06_m5_migration.py", "tests/test_07_m6_migration.py",
    "tests/test_08_v13_migration.py", "tests/test_09_v22_migration.py",
]
command = [str(PYTHON), "-m", "pytest", "-q", "-p", "no:cacheprovider"]
command += args.tests or (MIGRATION_TESTS if args.migration
                          else ["--ignore=" + path for path in MIGRATION_TESTS])
print("running:", " ".join(command))
raise SystemExit(subprocess.run(command, cwd=API, env=env).returncode)
