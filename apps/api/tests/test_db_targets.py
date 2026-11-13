"""Round 11: one database-target decision, shared by pytest, the runner and delivery.

These cases are the safety mat: they must fail loudly if someone widens a
marker, reorders the classification or lets a script authorize itself.
"""

import asyncio

from interview_api.core import db_targets as targets


def test_production_and_system_databases_are_never_writable():
    for name in ("interview_agent", "postgres", "template0", "template1"):
        assert targets.classify(name) in (targets.PRODUCTION, targets.SYSTEM)
        assert targets.test_run_problems(name, name)
        assert targets.operational_migrate_problem(name) is not None


def test_a_sealed_live_batch_cannot_be_migrated_even_with_authorization():
    name = "interview_agent_thinking_long_20261110"
    assert targets.classify(name) == targets.ARCHIVED_LIVE
    assert targets.operational_migrate_problem(name, authorized=name) is not None
    assert targets.test_run_problems(name, name)


def test_the_browser_database_is_never_a_pytest_target():
    name = "interview_agent_wb_r11_e2e_20261112"
    assert targets.classify(name) == targets.E2E
    assert any("受保护" in problem for problem in targets.test_run_problems(name, name))
    # It is a legitimate migration target only when the operator names it.
    assert targets.operational_migrate_problem(name) is not None
    assert targets.operational_migrate_problem(name, authorized=name) is None
    assert targets.e2e_problem(name) is None


def test_round_nine_database_without_a_marker_is_still_refused():
    name = "interview_agent_wb_r9_20261112"
    assert targets.classify(name) == targets.OTHER
    assert any("标记" in problem for problem in targets.test_run_problems(name, name))


def test_test_and_migration_databases_are_allowed_and_distinguishable():
    assert targets.classify("interview_agent_wb_r11_test_20261112") == targets.TEST
    assert targets.classify("interview_agent_wb_r11_mig_20261112") == targets.MIGRATION
    assert targets.classify("interview_agent_v21_wb_r11_20261112") == targets.MIGRATION
    for name in ("interview_agent_wb_r11_test_20261112", "interview_agent_wb_r11_mig_20261112"):
        assert targets.test_run_problems(name, name) == []
        assert targets.operational_migrate_problem(name) is None


def test_a_data_bearing_regression_database_is_not_an_empty_migration_target():
    problem = targets.migration_problem("interview_agent_wb_r11_test_20261112")
    assert problem and "承载业务数据" in problem
    assert targets.migration_problem("interview_agent_wb_r11_mig_20261112") is None
    assert targets.migration_problem("interview_agent_wb_r11_e2e_20261112") is not None
    assert targets.migration_problem("interview_agent") is not None


def test_browser_fixtures_only_accept_the_browser_database():
    assert targets.e2e_problem("interview_agent_wb_r11_e2e_20261112") is None
    assert targets.e2e_problem("interview_agent_wb_r11_test_20261112") is not None
    assert targets.e2e_problem("interview_agent") is not None
    # The V2.3 fixture database keeps its historical name, which is a UI one.
    assert targets.classify("interview_agent_v21_v23_browser_20261022") == targets.E2E


def test_app_and_test_urls_must_agree():
    problems = targets.test_run_problems("interview_agent_wb_r11_test_20261112",
                                       "interview_agent_wb_r11_e2e_20261112")
    assert any("同一测试库" in problem for problem in problems)


def test_demo_database_is_seeded_by_demo_mode_only():
    assert targets.classify("interview_agent_m6_f3_ux_demo") == targets.DEMO
    assert targets.test_run_problems("interview_agent_m6_f3_ux_demo", "interview_agent_m6_f3_ux_demo")
    assert targets.operational_migrate_problem("interview_agent_m6_f3_ux_demo") is None


def test_emptiness_is_checked_against_the_server(database_url):
    """The regression database has tables, so it cannot be a migration target."""
    name = database_url.rsplit("/", 1)[-1]
    problem = asyncio.run(targets.emptiness_problem(database_url, name))
    assert problem and "不是空库" in problem
