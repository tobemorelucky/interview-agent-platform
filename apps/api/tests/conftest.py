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

