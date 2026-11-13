"""Round 17: the acceptance launcher can no longer serve the wrong database.

Round 16 incident: `--database` only fed a throwaway engine; the API used the
module-level engine built from `settings.database_url` (.env), so a browser run
whose flag said `*_e2e_*` actually served `interview_agent`.

These cases pin the three properties that were missing:

* the target must be named explicitly (no fallback to `.env`),
* the CLI name wins over an exported `DATABASE_URL`, and the override is said out
  loud instead of happening silently,
* refusal happens before a connection exists — proven by making every connection
  primitive explode and by running the real CLI in a subprocess.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from interview_api.core import runtime_target
from interview_api.core.config import Settings

E2E = "interview_agent_wb_r17_e2e_20261113"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "serve_offline_acceptance.py"
SECRET = "sup3r-s3cret-pw"

# Captured at import, before any test lets `apply()` rewrite the environment.
# Re-reading `Settings()` afterwards would only restore the mutated value.
ENV_KEYS = ("DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL",
            "CELERY_RESULT_BACKEND", "MINIO_BUCKET")
ORIGINAL_ENV = {key: os.environ.get(key) for key in ENV_KEYS}


def restore_env():
    for key, value in ORIGINAL_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def base_url(password=SECRET):
    return f"postgresql+asyncpg://interview_agent:{password}@localhost:5432/interview_agent"


def refused(name, **kwargs):
    with pytest.raises(runtime_target.RuntimeTargetError) as captured:
        runtime_target.resolve(name, base_url=base_url(), **kwargs)
    return captured.value


def test_missing_database_is_refused_instead_of_falling_back_to_env():
    error = refused(None, env_url=base_url())
    assert "必须显式指定" in error.reason
    assert "interview_agent" in error.actual          # what .env would have used
    assert E2E in error.expected or "_e2e_" in error.expected
    assert "不会回退" in str(error)
    assert refused("   ", env_url=base_url()).reason == error.reason


@pytest.mark.parametrize("name,needle", [
    ("interview_agent", "生产库"),
    ("interview_agent_thinking_long_20261110", "历史 Live 归档"),
    ("interview_agent_live_20261020", "历史 Live 归档"),
    ("postgres", "系统库"),
    ("interview_agent_wb_r17_test_20261113", "不是浏览器验收库"),
    ("interview_agent_v21_wb_r17_20261113", "不是浏览器验收库"),
    ("interview_agent_m6_f3_ux_demo", "不是浏览器验收库"),
    ("random_scratch", "不是浏览器验收库"),
])
def test_only_e2e_databases_are_accepted(name, needle):
    error = refused(name)
    assert needle in error.reason
    assert name in error.actual
    assert "E2E" in error.expected


def test_cli_name_wins_over_an_exported_url_and_says_so():
    # .env / an inherited environment still points at production.
    target = runtime_target.resolve(E2E, base_url=base_url(), env_url=base_url())
    assert target.database == E2E and target.kind == "E2E"
    assert target.env_conflict and SECRET not in target.env_conflict
    assert any("命令行 --database 优先" in line for line in target.notices)
    # No conflict → no notice, and nothing else changes.
    quiet = runtime_target.resolve(E2E, base_url=base_url(), env_url=f"postgresql+asyncpg://u:{SECRET}@h:5432/{E2E}")
    assert quiet.env_conflict is None and quiet.notices == []


def test_resolved_group_isolates_database_redis_and_bucket():
    target = runtime_target.resolve(E2E, base_url=base_url())
    assert target.display.endswith(f"/{E2E}") and SECRET not in target.display
    assert target.url.endswith(f"/{E2E}")           # real DSN still usable internally
    indexes = [make_url(url).database for url in target.redis]
    assert indexes == ["9", "10", "11"]
    assert target.minio_bucket == runtime_target.ACCEPTANCE_MINIO_BUCKET
    assert target.minio_bucket != Settings().minio_bucket  # never the production bucket


def test_apply_writes_every_member_before_any_client_is_built():
    applied = Settings()
    target = runtime_target.resolve(E2E, base_url=base_url(), env_url=base_url())
    runtime_target.apply(target, settings=applied)
    try:
        runtime_target.verify_settings(target, settings=applied)
        runtime_target.verify_optional_clients(target, settings=applied)
        assert make_url(applied.database_url).database == E2E
        assert applied.redis_url == target.redis[0]
        assert applied.minio_bucket == target.minio_bucket
        assert os.environ["DATABASE_URL"] == target.url
    finally:
        # Never leak the acceptance target into the rest of the session.
        restore_env()


def test_refusal_message_reports_expected_and_actual_without_the_password():
    error = refused("interview_agent")
    text = str(error)
    assert "预期" in text and "实际" in text
    assert E2E in text.split("预期")[1].split("实际")[0] or "_e2e_" in text
    assert "interview_agent（判定：PRODUCTION）" in text
    assert SECRET not in text
    assert "未建立任何数据库连接" in text


def test_engine_mismatch_is_detected_from_url_only(monkeypatch):
    target = runtime_target.resolve(E2E, base_url=base_url())
    wrong = create_async_engine(base_url().replace("interview_agent", "interview_agent_live_20261020"))
    try:
        with pytest.raises(runtime_target.RuntimeTargetError) as captured:
            runtime_target.verify_engine(wrong, target)
        assert "已构建的数据库引擎与预期目标不一致" in captured.value.reason
        assert E2E in captured.value.expected
        assert "interview_agent_live_20261020" in captured.value.actual
    finally:
        import asyncio
        asyncio.run(wrong.dispose())


def test_acceptance_path_never_opens_a_connection(monkeypatch):
    """The whole resolve → apply → verify path with every connect() booby-trapped."""
    import asyncpg
    import sqlalchemy.ext.asyncio

    def explode(*args, **kwargs):
        raise AssertionError("a connection was attempted before the target was verified")

    monkeypatch.setattr(sqlalchemy.ext.asyncio, "create_async_engine", explode)
    monkeypatch.setattr(asyncpg, "connect", explode)
    target = runtime_target.resolve(E2E, base_url=base_url(), env_url=base_url())
    applied = Settings()
    runtime_target.apply(target, settings=applied)
    try:
        runtime_target.verify_settings(target, settings=applied)
        runtime_target.verify_optional_clients(target, settings=applied)
    finally:
        restore_env()
    # And a refusal reaches the same point without any I/O at all.
    with pytest.raises(runtime_target.RuntimeTargetError):
        runtime_target.resolve("interview_agent", base_url=base_url())


def run_launcher(*arguments):
    return subprocess.run([sys.executable, str(SCRIPT), *arguments],
                          capture_output=True, text=True, timeout=120,
                          cwd=str(SCRIPT.parents[1]))


@pytest.mark.parametrize("arguments", [
    ["verify"],
    ["verify", "--database", "interview_agent"],
    ["verify", "--database", "interview_agent_thinking_long_20261110"],
    ["api", "--database", "postgres"],          # a real action, refused just as early
])
def test_launcher_refuses_before_startup(arguments):
    finished = run_launcher(*arguments)
    assert finished.returncode == 3, finished.stderr
    assert "验收目标拒绝" in finished.stderr
    assert "ACCEPTANCE_TARGET" not in finished.stdout
    assert "uvicorn" not in finished.stdout.lower()


def test_launcher_verify_accepts_an_e2e_name_without_touching_a_database():
    finished = run_launcher("verify", "--database", E2E)
    assert finished.returncode == 0, finished.stderr
    assert "ACCEPTANCE_TARGET" in finished.stdout
    assert E2E in finished.stdout and "interview-agent-r17-e2e" in finished.stdout
    assert "命令行 --database 优先" in finished.stdout    # env had another database
    assert SECRET not in finished.stdout + finished.stderr
