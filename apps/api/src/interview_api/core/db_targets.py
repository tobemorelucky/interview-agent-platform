"""One classification of "what kind of database is this name", for every writer.

Round 9 wrote 219 DEMO companies and 347 PUBLISHED job descriptions into the
browser acceptance database because the regression fixtures were pointed at it.
Round 10 added a pytest guard, but the same `.env` is read by the delivery
helper, the start scripts and the browser-fixture script, so the decision was
still being re-made in four places with four different rules.

This module is the single answer. It is pure string logic on purpose: no
settings, no connection, no I/O, so a test can exercise every branch offline and
a script can decide *before* it opens a connection.

Kinds, and what may be done with each:

| kind | meaning | pytest | ops migrate | browser fixtures |
|---|---|---|---|---|
| `PRODUCTION` | the real database | never | only with an explicit authorization naming it | never |
| `ARCHIVED_LIVE` | a sealed acceptance batch | never | never | never |
| `E2E` / `UI` | browser or fixture database | never | explicit authorization | yes, when empty |
| `DEMO` | the replay demo database | never | yes (demo mode only) | seeded only by demo mode |
| `MIGRATION` | empty database for upgrade/downgrade cases | yes (alone) | yes | no |
| `TEST` | data-bearing regression database | yes | yes | no |
| `SYSTEM` | postgres / templates | never | never | never |
| `OTHER` | anything unrecognised | never | only with authorization | never |
"""

from __future__ import annotations

PRODUCTION = "PRODUCTION"
ARCHIVED_LIVE = "ARCHIVED_LIVE"
E2E = "E2E"
UI = "UI"
DEMO = "DEMO"
MIGRATION = "MIGRATION"
TEST = "TEST"
SYSTEM = "SYSTEM"
OTHER = "OTHER"

PRODUCTION_DATABASE = "interview_agent"
SYSTEM_DATABASES = frozenset({"postgres", "template0", "template1"})
DEMO_DATABASES = frozenset({"interview_agent_m6_f3_ux_demo"})

# Order matters: an archived batch is never "just an e2e database", and a
# browser fixture database is never "just a v21 database".
# Sealed acceptance/real-model batches. Most names say `live`/`accept`, but
# `interview_agent_thinking_long_20261110` (docs/80) carries neither word and is
# still a frozen现场: it must never be migrated, with or without authorisation.
ARCHIVED_MARKERS = ("_live_", "_live", "_accept", "_archived", "_thinking_long", "_smoke")
UI_MARKERS = ("_e2e", "_ui_", "_browser", "_release")
DEMO_MARKERS = ("_demo", "_f3_ux_demo")
TEST_MARKERS = ("_test", "_regression")
MIGRATION_MARKERS = ("_mig",)
MIGRATION_PREFIX = "interview_agent_v21_"
WORKBUDDY_PREFIX = "interview_agent_"


def classify(name: str | None) -> str:
    """Classify a database *name* (never a URL, never a DSN)."""
    if not name:
        return OTHER
    if name in SYSTEM_DATABASES:
        return SYSTEM
    if name == PRODUCTION_DATABASE:
        return PRODUCTION
    lowered = name.lower()
    if any(marker in lowered for marker in ARCHIVED_MARKERS):
        return ARCHIVED_LIVE
    if any(marker in lowered for marker in UI_MARKERS):
        return UI if "_ui_" in lowered else E2E
    if name in DEMO_DATABASES or any(marker in lowered for marker in DEMO_MARKERS):
        return DEMO
    if any(marker in lowered for marker in MIGRATION_MARKERS):
        return MIGRATION
    if any(marker in lowered for marker in TEST_MARKERS):
        return TEST
    if name.startswith(MIGRATION_PREFIX):
        # The V2.1/V2.2 migration cases demand this exact prefix. They still
        # have to be empty, which the runner checks against the server.
        return MIGRATION
    return OTHER


def test_run_problems(test_db: str, app_db: str) -> list[str]:
    """Empty list means a pytest run may write to `test_db`.

    Both pytest and the runner call this, so the two can never disagree.
    """
    problems: list[str] = []
    kind = classify(test_db)
    if kind in (PRODUCTION, SYSTEM, ARCHIVED_LIVE, E2E, UI, DEMO, OTHER) or not test_db.startswith(WORKBUDDY_PREFIX):
        problems.append(f"M1_TEST_DATABASE_URL 不是隔离测试库（当前：{test_db or '未设置'}）")
    if kind in (PRODUCTION, SYSTEM, ARCHIVED_LIVE, E2E, UI, DEMO) or test_db in SYSTEM_DATABASES:
        problems.append(f"M1_TEST_DATABASE_URL 指向受保护的 E2E / 正式 / 历史归档库：{test_db}")
    if kind == OTHER and test_db.startswith(WORKBUDDY_PREFIX):
        # A workbuddy-named database must still say what it is for.
        problems.append("测试库名必须含 _test / _regression / _mig 标记")
    if app_db != test_db:
        problems.append(
            f"DATABASE_URL 必须与 M1_TEST_DATABASE_URL 指向同一测试库（{app_db or '未设置'} != {test_db}）")
    return problems


def migration_problem(name: str) -> str | None:
    """A migration pass upgrades an empty database; nothing else qualifies."""
    kind = classify(name)
    if kind == MIGRATION:
        return None
    if kind == TEST:
        return f"{name} 是承载业务数据的回归库，不能当作空库跑迁移用例；请另建 *_mig_* 或 interview_agent_v21_* 空库"
    return f"{name} 不是迁移专用空库（判定：{kind}）；迁移用例只能跑在 * _mig _ * 或 interview_agent_v21_* 空库上"


def e2e_problem(name: str) -> str | None:
    """The browser database may be written by the API, never by pytest."""
    kind = classify(name)
    if kind == E2E:
        return None
    if kind == UI:
        return f"{name} 是 UI 夹具库，浏览器验收请用 *_e2e_* 库"
    return f"{name} 不是浏览器验收库（判定：{kind}）"


def operational_migrate_problem(name: str, *, authorized: str | None = None,
                                demo_database: str | None = None) -> str | None:
    """Guard for `delivery.py migrate`.

    Test, migration and demo databases are local by construction. Everything
    else — the production database, an unknown name, an E2E database — needs the
    authorization flag to spell out the same name, so a script that merely reads
    `.env` can never migrate production by accident. Archived Live batches are
    refused with no override at all.
    """
    if not name:
        return "无法从 DATABASE_URL 解析数据库名"
    kind = classify(name)
    if kind in (SYSTEM, ARCHIVED_LIVE):
        return f"{name} 是排除在外的数据库（判定：{kind}），不允许迁移"
    if kind in (TEST, MIGRATION, DEMO):
        return None
    if demo_database:
        return None
    if authorized != name:
        return (f"{name} 不是本地测试库（判定：{kind}）。生产/验收库迁移必须显式授权："
                f"python scripts/delivery.py migrate --authorize-database {name}")
    return None


async def emptiness_problem(database_url: str, name: str) -> str | None:
    """A migration pass upgrades an empty database from nothing.

    Reusing a database that already holds tables (even only `alembic_version`
    and test leftovers) would run `upgrade` on top of an unknown schema, so the
    runner refuses instead of clearing, dropping or rebuilding anything.
    """
    import asyncpg
    from sqlalchemy.engine import make_url

    url = make_url(database_url).set(drivername="postgresql", database=name)
    connection = await asyncpg.connect(url.render_as_string(hide_password=False), timeout=5)
    try:
        tables = await connection.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
    finally:
        await connection.close()
    if tables:
        return (f"{name} 已有 {tables} 张表，不是空库；迁移用例需要全新空库。"
                "本命令不会清空、DROP 或重建任何已存在的库。")
    return None
