"""Run the backend suite in two properly separated passes.

Round 10 left a confusing result: `757 passed / 10 failed`, where all ten
failures were the migration cases that need an *empty* database and were being
pointed at the data-bearing regression database. Deleting or skipping them
would hide real coverage, so this runner groups them instead:

    pass 1  business regression   -> a dedicated test database, migrated to head
    pass 2  migration cases       -> a dedicated *empty* database, never migrated here

Both targets are checked by `interview_api.core.db_targets` before a connection
is opened, and the migration database is additionally checked against the
server: if it already has tables, the run refuses instead of clearing, dropping
or rebuilding anything.

Examples:
    python scripts/run-workbuddy-tests.py \\
        --database interview_agent_wb_r11_test_20261112 \\
        --migration-database interview_agent_v21_wb_r11_20261112

    # only one file, no migration pass
    python scripts/run-workbuddy-tests.py --database interview_agent_wb_r11_test_20261112 \\
        --reuse --skip-migration -- tests/test_private_training.py
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
PYTHON = API / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():  # non-Windows checkout
    PYTHON = API / ".venv" / "bin" / "python"
sys.path.insert(0, str(API / "src"))

from interview_api.core import db_targets  # noqa: E402
from interview_api.core.config import settings  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

# The cases that upgrade an empty database from nothing and then downgrade it.
MIGRATION_TESTS = [
    "tests/test_00_migration.py", "tests/test_01_m2a_migration.py",
    "tests/test_02_m2b_migration.py", "tests/test_03_m2b_migration_data.py",
    "tests/test_04_m3_migration.py", "tests/test_05_m4_migration.py",
    "tests/test_06_m5_migration.py", "tests/test_07_m6_migration.py",
    "tests/test_08_v13_migration.py", "tests/test_09_v22_migration.py",
]

parser = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--database", required=True, help="data-bearing regression database")
parser.add_argument("--migration-database",
                    help="separate EMPTY database for the upgrade/downgrade cases")
parser.add_argument("--reuse", action="store_true", help="reuse existing databases instead of creating them")
parser.add_argument("--skip-migration", action="store_true", help="run only the business pass")
parser.add_argument("--create-only", action="store_true", help="prepare databases, then stop")
parser.add_argument("tests", nargs="*", help="explicit pytest arguments (disables the migration pass)")
args = parser.parse_args()

base_url = make_url(settings.database_url)


def url_for(name):
    return base_url.set(database=name).render_as_string(hide_password=False)


def refuse(message):
    raise SystemExit(f"REFUSED: {message}")


async def ensure_database(name):
    """Create the database when missing; never clear an existing one."""
    import asyncpg

    admin = base_url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    connection = await asyncpg.connect(admin, timeout=5)
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name)
        if not exists:
            await connection.execute(f'CREATE DATABASE "{name}"')
            print(f"created {name}")
        elif not args.reuse:
            refuse(f"{name} 已存在；不覆盖、不清空。换一个新名字，或加 --reuse 复用。")
    finally:
        await connection.close()


def migrate(name):
    env = {**os.environ, "DATABASE_URL": url_for(name)}
    subprocess.run([str(PYTHON), "-m", "alembic", "upgrade", "head"],
                   cwd=API, env=env, check=True)
    print(f"migrated {name} to head")


def count(report: Path):
    totals = dict(passed=0, failed=0, skipped=0, error=0)
    if not report.exists():
        return totals
    for case in ElementTree.parse(report).getroot().iter("testcase"):
        if case.find("failure") is not None:
            totals["failed"] += 1
        elif case.find("error") is not None:
            totals["error"] += 1
        elif case.find("skipped") is not None:
            totals["skipped"] += 1
        else:
            totals["passed"] += 1
    return totals


def run_pytest(paths, name):
    """One pytest pass; counts come from the JUnit XML, not from stdout text."""
    env = {**os.environ, "DATABASE_URL": url_for(name), "M1_TEST_DATABASE_URL": url_for(name)}
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "report.xml"
        command = [str(PYTHON), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   f"--junitxml={report}", *paths]
        print("running:", " ".join(command))
        code = subprocess.run(command, cwd=API, env=env).returncode
        return count(report), code


# ── the business pass ────────────────────────────────────────────────────────
problems = db_targets.test_run_problems(args.database, args.database)
if problems:
    refuse("；".join(problems))
if args.migration_database and args.database == args.migration_database:
    refuse("业务回归库与迁移空库必须是两个不同的数据库")

asyncio.run(ensure_database(args.database))
migrate(args.database)
if args.create_only:
    raise SystemExit(0)

results = {}
business_paths = args.tests or ["--ignore=" + path for path in MIGRATION_TESTS]
results["business"], business_code = run_pytest(business_paths, args.database)

# ── the migration pass ───────────────────────────────────────────────────────
migration_code = 0
if args.tests:
    print("explicit test paths given: migration pass skipped")
elif args.skip_migration:
    print("--skip-migration: migration pass skipped")
else:
    if not args.migration_database:
        refuse("缺少 --migration-database：迁移用例必须跑在独立的空库上（或加 --skip-migration）")
    problem = db_targets.migration_problem(args.migration_database)
    if problem:
        refuse(problem)
    asyncio.run(ensure_database(args.migration_database))
    empty_problem = asyncio.run(db_targets.emptiness_problem(
        base_url.render_as_string(hide_password=False), args.migration_database))
    if empty_problem:
        refuse(empty_problem)
    results["migration"], migration_code = run_pytest(MIGRATION_TESTS, args.migration_database)

totals = {key: sum(pass_totals[key] for pass_totals in results.values()) for key in
          ("passed", "failed", "skipped", "error")}
print(json.dumps(dict(passes=results, combined=totals,
                      databases=dict(business=args.database,
                                     migration=args.migration_database if "migration" in results else None)),
                 ensure_ascii=False, indent=2))
raise SystemExit(business_code or migration_code)
