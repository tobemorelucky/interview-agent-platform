"""Round 10: a regression run must never write into an E2E or archived Live DB.

The guard is a pure environment check, so these tests need no database at all.
"""

import conftest


BASE = "postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/"


def _set(monkeypatch, name):
    monkeypatch.delenv("M1_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if name is not None:
        monkeypatch.setenv("M1_TEST_DATABASE_URL", BASE + name)
        monkeypatch.setenv("DATABASE_URL", BASE + name)


def test_pure_unit_tests_need_no_database(monkeypatch):
    _set(monkeypatch, None)
    assert conftest._isolation_problems() == []


def test_a_dedicated_test_database_is_allowed(monkeypatch):
    _set(monkeypatch, "interview_agent_wb_r10_test_20261112")
    assert conftest._isolation_problems() == []
    _set(monkeypatch, "interview_agent_wb_r10_regression_20261112")
    assert conftest._isolation_problems() == []
    _set(monkeypatch, "interview_agent_wb_r10_mig_20261112")
    assert conftest._isolation_problems() == []


def test_the_migration_only_v21_family_is_allowed(monkeypatch):
    _set(monkeypatch, "interview_agent_v21_wb_r10_20261112")
    assert conftest._isolation_problems() == []


def test_production_database_is_refused(monkeypatch):
    _set(monkeypatch, "interview_agent")
    assert conftest._isolation_problems()


def test_e2e_browser_database_is_refused(monkeypatch):
    _set(monkeypatch, "interview_agent_wb_r10_e2e_20261112")
    assert any("受保护" in p for p in conftest._isolation_problems())


def test_round_nine_e2e_database_without_a_test_marker_is_refused(monkeypatch):
    _set(monkeypatch, "interview_agent_wb_r9_20261112")
    assert any("标记" in p for p in conftest._isolation_problems())


def test_archived_live_and_demo_databases_are_refused(monkeypatch):
    for name in ("interview_agent_wb_coach_live2_20261111",
                 "interview_agent_v21_acceptance_20261022_a",
                 "interview_agent_m6_f3_ux_demo"):
        _set(monkeypatch, name)
        assert conftest._isolation_problems(), name


def test_app_and_test_urls_must_point_at_the_same_database(monkeypatch):
    monkeypatch.setenv("M1_TEST_DATABASE_URL", BASE + "interview_agent_wb_r10_test_20261112")
    monkeypatch.setenv("DATABASE_URL", BASE + "interview_agent_wb_r10_e2e_20261112")
    assert any("同一测试库" in p for p in conftest._isolation_problems())


def test_missing_app_url_is_refused(monkeypatch):
    monkeypatch.setenv("M1_TEST_DATABASE_URL", BASE + "interview_agent_wb_r10_test_20261112")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert any("同一测试库" in p for p in conftest._isolation_problems())
