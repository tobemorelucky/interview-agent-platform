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

# Round 10: the full regression suite and a browser E2E run used to share one
# database, so regression fixtures (DEMO companies/jobs/JDs) appeared in the UI.
# A test run may only write to a database that is explicitly named as a test or
# migration target. Production, E2E/UI and archived Live databases are refused.
PROTECTED_DATABASES = frozenset({
    "interview_agent", "postgres", "template0", "template1",
    "interview_agent_m6_f3_ux_demo",
})
PROTECTED_MARKERS = (
    "_e2e", "_ui_", "_live_", "_accept", "_browser", "_release", "_demo",
    "_coach_live", "_budget_live", "_full_live",
)
TEST_DATABASE_MARKERS = ("_test", "_regression", "_mig")


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
    test_db = _database_name(test_url)
    app_db = _database_name(os.getenv("DATABASE_URL") or "")
    problems: list[str] = []
    if not test_db.startswith("interview_agent_"):
        problems.append(f"M1_TEST_DATABASE_URL 不是隔离测试库（当前：{test_db or '未设置'}）")
    if test_db in PROTECTED_DATABASES or any(m in test_db for m in PROTECTED_MARKERS):
        problems.append(f"M1_TEST_DATABASE_URL 指向受保护的 E2E / 正式 / 历史归档库：{test_db}")
    # test_09_v22_migration.py already demands this exact prefix for an empty DB.
    migration_only = test_db.startswith("interview_agent_v21_")
    if not migration_only and not any(m in test_db for m in TEST_DATABASE_MARKERS):
        problems.append("测试库名必须含 _test / _regression / _mig 标记")
    if app_db != test_db:
        problems.append(
            f"DATABASE_URL 必须与 M1_TEST_DATABASE_URL 指向同一测试库（{app_db or '未设置'} != {test_db}）")
    return problems


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

