"""Resolve, apply and verify the runtime target of the acceptance stack.

Round 16 incident: `serve_offline_acceptance.py api --database <e2e>` created a
disposable engine for `--database`, then started the app with
`uvicorn.run("interview_api.main:app")`. That import built the *module-level*
engine from `settings.database_url` (i.e. `.env`), so the API served the
production database while the guard had only checked a name it never used.

The fix has three parts, all here so they cannot drift apart again:

1. `resolve()` decides the target from the CLI name, reusing
   `interview_api.core.db_targets.classify` — the same classification pytest,
   the test runner and `delivery.py` use. No second naming rule.
2. `apply()` writes the resolved target into `settings` *and* the environment
   before anything that creates an engine or a Redis client is imported.
3. `verify_*()` re-reads the objects that actually got built and compares them
   with the intended target. These read `engine.url` and `settings` only: they
   never open a connection, so a refusal is guaranteed to happen before I/O.

Nothing here reads `.env` directly; `settings` is passed in by the caller that
already loaded it. Nothing here prints a password: URLs are rendered with
`hide_password=True`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from sqlalchemy.engine import make_url

from interview_api.core import db_targets
from interview_api.core.config import settings as global_settings

# Redis logical databases used by the acceptance stack. Production keeps the
# `.env` defaults (0/1/2) and demo mode uses 13/14, so acceptance is disjoint.
ACCEPTANCE_REDIS_DATABASES = (9, 10, 11)  # cache/rate limit, broker, result backend
ACCEPTANCE_MINIO_BUCKET = "interview-agent-r17-e2e"
EXPECTED_KIND = db_targets.E2E


class RuntimeTargetError(RuntimeError):
    """A target was refused before any connection was opened."""

    def __init__(self, reason: str, *, expected: str, actual: str, detail: str = ""):
        self.reason = reason
        self.expected = expected
        self.actual = actual
        self.detail = detail
        super().__init__(self.render())

    def render(self) -> str:
        text = (f"验收目标拒绝：{self.reason}\n"
                f"  预期：{self.expected}\n"
                f"  实际：{self.actual}")
        if self.detail:
            text += f"\n  说明：{self.detail}"
        return text + "\n  未建立任何数据库连接，也不会回退到 .env。"


def masked(url: str) -> str:
    """URL safe for logs and error messages: password never included."""
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<unparsable-url>"


def database_name(url: str | None) -> str:
    if not url:
        return ""
    try:
        return make_url(url).database or ""
    except Exception:
        return ""


def redis_with_database(url: str, index: int) -> str:
    return make_url(url).set(database=str(index)).render_as_string(hide_password=False)


@dataclass(frozen=True)
class AcceptanceTarget:
    """One resolved, fully isolated resource group."""

    database: str
    kind: str
    host: str | None
    port: int
    url: str                       # real DSN with credentials; never printed
    display: str                   # masked DSN for humans
    env_conflict: str | None       # a different DATABASE_URL was exported; CLI won
    redis: tuple[str, str, str]
    minio_bucket: str
    api_port: int = 8000
    web_port: int = 5173

    def describe(self) -> dict:
        return dict(
            database=self.database, kind=self.kind, host=self.host, port=self.port,
            database_url=self.display, redis_databases=[make_url(u).database for u in self.redis],
            minio_bucket=self.minio_bucket, api_port=self.api_port, web_port=self.web_port,
            env_conflict=self.env_conflict, expected_kind=EXPECTED_KIND,
        )

    @property
    def notices(self) -> list[str]:
        if not self.env_conflict:
            return []
        return [f"忽略环境变量 DATABASE_URL={self.env_conflict}：命令行 --database 优先"]


def resolve(name: str | None, *, base_url: str | None = None, env_url: str | None = None,
            redis_url: str | None = None, broker_url: str | None = None,
            backend_url: str | None = None, minio_bucket: str | None = None,
            require_explicit: bool = True) -> AcceptanceTarget:
    """Decide the acceptance target. Pure: no I/O, no engine, no connection.

    `base_url` supplies host/port/credentials; `name` supplies the database.
    A CLI name always wins over an exported `DATABASE_URL`, and that override is
    reported instead of happening silently.
    """
    base_url = base_url or global_settings.database_url
    env_url = env_url if env_url is not None else os.getenv("DATABASE_URL")
    env_name = database_name(env_url)
    try:
        base = make_url(base_url)
    except Exception:
        raise RuntimeTargetError(
            "配置中的 DATABASE_URL 无法解析", expected="可解析的 PostgreSQL DSN",
            actual="<unparsable>", detail="请检查 .env 的 DATABASE_URL 格式")

    if require_explicit and (name is None or not str(name).strip()):
        raise RuntimeTargetError(
            "验收模式必须显式指定 --database；不会隐式沿用 .env 或环境变量",
            expected="*_e2e_* 隔离库名",
            actual=f"未指定（.env 指向 {env_name or '未设置'}，已拒绝）",
            detail="例如 --database interview_agent_wb_r17_e2e_20261113")

    name = str(name).strip()
    if not name.replace("_", "").isalnum():
        raise RuntimeTargetError(
            "数据库名必须是字母、数字与下划线", expected="*_e2e_* 隔离库名", actual=name)

    kind = db_targets.classify(name)
    if kind != EXPECTED_KIND:
        raise RuntimeTargetError(
            _reason_for(kind),
            expected=f"{EXPECTED_KIND}（名字含 _e2e_ 的浏览器验收库）",
            actual=f"{name}（判定：{kind}）",
            detail="生产库、历史 Live 归档、系统库与回归测试库都不得被验收服务连接，只读查询也不例外")
    problem = db_targets.e2e_problem(name)
    if problem:
        raise RuntimeTargetError(problem, expected="*_e2e_* 隔离库", actual=name)

    url = base.set(database=name).render_as_string(hide_password=False)
    conflict = None
    if env_name and env_name != name:
        conflict = masked(env_url)

    redis_base = redis_url or global_settings.redis_url
    broker = broker_url or global_settings.celery_broker_url
    backend = backend_url or global_settings.celery_result_backend
    cache_index, broker_index, backend_index = ACCEPTANCE_REDIS_DATABASES
    return AcceptanceTarget(
        database=name, kind=kind, host=base.host, port=base.port or 5432,
        url=url, display=masked(url), env_conflict=conflict,
        redis=(redis_with_database(redis_base, cache_index),
               redis_with_database(broker, broker_index),
               redis_with_database(backend, backend_index)),
        minio_bucket=minio_bucket or ACCEPTANCE_MINIO_BUCKET,
    )


def _reason_for(kind: str) -> str:
    if kind == db_targets.PRODUCTION:
        return "目标被判为生产库；验收服务不得连接生产库（包括只读查询）"
    if kind == db_targets.ARCHIVED_LIVE:
        return "目标被判为历史 Live 归档库；该库只读封存，验收服务不得连接"
    if kind == db_targets.SYSTEM:
        return "目标是系统库（postgres/template）；验收服务不得连接"
    if kind == db_targets.UI:
        return "目标是 UI 夹具库，不是浏览器验收库"
    return "目标不是浏览器验收库（E2E）"


def apply(target: AcceptanceTarget, *, settings=None) -> None:
    """Write the target everywhere the app reads it, before any client exists."""
    settings = settings or global_settings
    settings.database_url = target.url
    os.environ["DATABASE_URL"] = target.url
    settings.redis_url, settings.celery_broker_url, settings.celery_result_backend = target.redis
    os.environ["REDIS_URL"], os.environ["CELERY_BROKER_URL"], os.environ["CELERY_RESULT_BACKEND"] = target.redis
    settings.minio_bucket = target.minio_bucket
    os.environ["MINIO_BUCKET"] = target.minio_bucket


def verify_settings(target: AcceptanceTarget, *, settings=None) -> None:
    """The applied configuration must still name the intended target."""
    settings = settings or global_settings
    actual = database_name(settings.database_url)
    if actual != target.database:
        raise RuntimeTargetError(
            "生效配置与预期目标不一致（可能被 .env 或子进程环境覆盖）",
            expected=target.database, actual=f"{actual or '未设置'}（{masked(settings.database_url)}）")


def verify_engine(engine, target: AcceptanceTarget) -> str:
    """Compare the built engine with the target. Reads `url` only: no connection."""
    actual = database_name(str(engine.url))
    if actual != target.database:
        raise RuntimeTargetError(
            "已构建的数据库引擎与预期目标不一致",
            expected=f"{target.database}（{target.display}）",
            actual=f"{actual or '未设置'}（{masked(str(engine.url))}）",
            detail="app 在应用验收配置之前就被导入，或子进程继承了另一个 DATABASE_URL")
    return actual


def verify_optional_clients(target: AcceptanceTarget, *, settings=None) -> None:
    """Redis/MinIO targets are set, not connected; report a mismatch early."""
    settings = settings or global_settings
    for attribute, url in zip(("redis_url", "celery_broker_url", "celery_result_backend"), target.redis):
        if getattr(settings, attribute) != url:
            raise RuntimeTargetError(
                f"{attribute} 与验收隔离组不一致",
                expected=f"{masked(url)}", actual=masked(getattr(settings, attribute) or ""))
    if settings.minio_bucket != target.minio_bucket:
        raise RuntimeTargetError(
            "MINIO_BUCKET 与验收隔离组不一致",
            expected=target.minio_bucket, actual=settings.minio_bucket or "未设置")
