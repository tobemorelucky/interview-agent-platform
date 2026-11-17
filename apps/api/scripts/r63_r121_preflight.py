"""R121 stage 1: read-only preflight for the UI showcase and the first Live.

Proves, rather than assumes, which database the running application code points
at, that both sandboxes really contain the nine published JDs, and that the R120
Replay sessions are still intact and must not be overwritten.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg
from sqlalchemy import text

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

import interview_api.modules.models  # noqa: F401,E402
from interview_api.core.config import settings  # noqa: E402
from interview_api.infrastructure.db.session import async_session_factory  # noqa: E402
from interview_api.modules.interview_report.agent import harness_enabled  # noqa: E402

JD_DB = "interview_agent_wb_jd_r31_20261116"
SHOW_DB = "interview_agent_showcase_r1_20261116"
OUT = REPO / "data/r63_r121_preflight"
DSN = "postgresql://interview_agent:interview_agent@localhost:5432/"


async def snapshot(db: str) -> dict:
    conn = await asyncpg.connect(DSN + db)
    try:
        result = {
            "database": db,
            "alembic": await conn.fetchval("select version_num from alembic_version"),
            "published": [dict(r) for r in await conn.fetch(
                """select v.id, c.canonical_name company, j.canonical_title title, v.version_number
                     from job_description_versions v join jobs j on j.id=v.job_id
                     join companies c on c.id=j.company_id
                    where v.publication_status='PUBLISHED' order by v.id""")],
            "waiting": await conn.fetchval(
                "select count(*) from job_description_versions where publication_status='WAITING_REVIEW'"),
            "sessions": await conn.fetchval("select count(*) from interview_sessions"),
            "evaluations": await conn.fetchval("select count(*) from interview_evaluations"),
            "reports": await conn.fetchval("select count(*) from final_interview_reports"),
            "admissions": await conn.fetchval("select count(*) from model_admissions"),
            "tokens": await conn.fetchval(
                "select coalesce(sum(coalesce(actual_tokens,reserved_tokens,0)),0) from model_admissions"),
        }
        if db == SHOW_DB:
            result["r120_marker_users"] = [dict(r) for r in await conn.fetch(
                "select id, email from users where email like 'showcase-r1-synthetic-%' order by id")]
            result["r120_final_sessions"] = [dict(r) for r in await conn.fetch(
                """select s.id, s.status, s.user_id, (s.completion_json->>'evaluated_count') evaluated
                     from interview_sessions s where s.id between 6 and 8 order by s.id""")]
    finally:
        await conn.close()
    return result


async def main() -> int:
    jd, show = await snapshot(JD_DB), await snapshot(SHOW_DB)
    env_url = os.getenv("DATABASE_URL", "")
    env_ledger = os.getenv("MODEL_LEDGER_DATABASE", "")
    if SHOW_DB not in env_url:
        raise SystemExit(f"REFUSED: application DATABASE_URL is not the showcase sandbox: {env_url}")
    if env_ledger != SHOW_DB:
        raise SystemExit(f"REFUSED: MODEL_LEDGER_DATABASE={env_ledger!r} != {SHOW_DB}")

    # Query through the application's own session factory: this is the decisive
    # proof of where the API will read and write, independent of psql/psql-style DSNs.
    async with async_session_factory() as db:
        live = {
            "settings_database_url": settings.database_url.replace(
                os.getenv("DB_PASSWORD", "interview_agent"), "***"),
            "application_sees_marker_users": (await db.execute(text(
                "select count(*) from users where email like 'showcase-r1-synthetic-%'"))).scalar(),
            "application_sees_sessions": (await db.execute(text(
                "select count(*) from interview_sessions"))).scalar(),
            "application_sees_admissions": (await db.execute(text(
                "select count(*) from model_admissions"))).scalar(),
        }
    model_routes = {}
    if "modelscope" in settings.llm_provider.lower():
        model_routes["provider"] = "modelscope"
        model_routes["model"] = settings.modelscope_model
    report = {
        "stage": "R121-1", "http_calls_to_model": 0,
        "git_head": "1d87a0b (preflight does not modify the worktree)",
        "jd_sandbox": jd, "showcase_sandbox": show,
        "environment": {"DATABASE_URL": env_url.replace("interview_agent:interview_agent", "***:***"),
                        "MODEL_LEDGER_DATABASE": env_ledger, "llm_provider": settings.llm_provider,
                        "harness_enabled": harness_enabled()},
        "application_connection": live,
        "same_published_set": [r["id"] for r in jd["published"]] == [r["id"] for r in show["published"]],
        "r120_sessions_preserved": (show["sessions"] >= 8 and show["evaluations"] >= 20
                                    and show["reports"] >= 6),
        "decision": ("PASS" if live["application_sees_marker_users"] >= 4 and live["application_sees_admissions"] == 0
                     and show["alembic"] == "0029" else "BLOCKED"),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "preflight.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("decision", report["decision"])
    print("same published set", report["same_published_set"])
    print("r120 sessions preserved", report["r120_sessions_preserved"])
    print("application sees", live)
    print("written", OUT / "preflight.json")
    return 0 if report["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
