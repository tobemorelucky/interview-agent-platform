from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# The classification lives in `interview_api.core.db_targets` so the pytest
# guard, the test runner and the delivery scripts cannot disagree about what
# "a test database" is. This module only reads the environment and asks it.
from interview_api.core.db_targets import test_run_problems

# SQLAlchemy resolves a ForeignKey lazily, by table name, the first time a mapper
# is configured. `interview_context_snapshots.job_target_id` points at
# `user_job_targets`, so a subset run that imports the interview models but not
# the private-JD models failed with NoReferencedTableError — a test-ordering
# artefact, not a product defect. Importing the model module here makes every
# subset run see the same registry the full run does.
from interview_api.modules.job_target import models as _job_target_models  # noqa: F401,E402


def _database_name(url: str) -> str:
    try:
        return make_url(url).database or ""
    except Exception:
        return ""


def _isolation_problems() -> list[str]:
    """Empty means safe to run. Pure unit tests (no M1 URL) are unaffected."""
    test_url = os.getenv("M1_TEST_DATABASE_URL")
    if not test_url:
        return []
    return test_run_problems(_database_name(test_url), _database_name(os.getenv("DATABASE_URL") or ""))


@pytest.fixture(scope="session", autouse=True)
def isolated_database_guard():
    """Refuse the whole session before any fixture can write to a shared DB."""
    problems = _isolation_problems()
    if problems:
        pytest.exit(
            "拒绝执行写入型测试：数据库隔离校验未通过\n  - " + "\n  - ".join(problems) +
            "\n专用测试库示例：interview_agent_wb_r10_test_20261112；"
            "E2E 库请用 interview_agent_wb_r10_e2e_20261112 并只由浏览器服务连接。",
            returncode=2,
        )


@pytest.fixture(scope="session")
def database_url() -> str:
    value = os.getenv("M1_TEST_DATABASE_URL")
    if not value:
        pytest.skip("M1_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    return value


@pytest.fixture(scope="session", autouse=True)
def declared_ledger_target():
    """The test process declares its own ledger database.

    `governed_call` refuses to book an admission anywhere but the database the
    process declares, so a test run must say "the test database" explicitly. Without
    this, a run whose DATABASE_URL happened to point at production would silently
    write admissions there instead of failing.
    """
    test_url = os.getenv("M1_TEST_DATABASE_URL")
    if not test_url:
        yield None
        return
    from interview_api.core.config import settings

    previous = settings.model_ledger_database
    settings.model_ledger_database = make_url(test_url).database or ""
    yield settings.model_ledger_database
    settings.model_ledger_database = previous


@pytest.fixture
def app_engine():
    """Give the process-wide app engine a pool that belongs to this event loop.

    `interview_api.infrastructure.db.engine` is built once per process, while an
    asyncpg connection belongs to the loop that opened it. Each test here runs
    its own `asyncio.run`, so a pool left over from an earlier test fails with
    `AttributeError: 'NoneType' object has no attribute 'send'` (round 16).

    Disposing the pool at the start of the *new* loop is the isolation the
    architecture actually supports: the app keeps its real engine, dependency
    wiring and authentication, instead of tests replacing `get_db` with a fixture
    factory. No production code is changed for this.
    """
    try:
        from interview_api.infrastructure.db.engine import engine
    except Exception:  # pragma: no cover - only when the app package is absent
        pytest.skip("application engine is unavailable")

    class Handle:
        @staticmethod
        async def reset() -> None:
            await engine.dispose()

    # No teardown disposal on purpose: closing a connection from a *new* loop
    # makes asyncpg write to a closed proactor. Callers reset inside their own
    # loop, which is where the pool was created.
    yield Handle

