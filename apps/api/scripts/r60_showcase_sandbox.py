"""SHOWCASE-R1 / part 4: create an isolated showcase sandbox at alembic head.

Creates a NEW database (never touches the production DB, the JD sandbox, or any
historical Live/acceptance database), migrates it with the real alembic chain to
0029, and copies only the public JD entities from the JD sandbox with their exact
ids, text and offsets. The three WAITING_REVIEW versions are copied too: a
showcase should show that they cannot be selected.

No model calls. No changes to any source database.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

SOURCE = "interview_agent_wb_jd_r31_20261116"
TARGET = "interview_agent_showcase_r1_20261116"
ROOT_DSN = "postgresql://interview_agent:interview_agent@localhost:5432/postgres"
PG = "postgresql://interview_agent:interview_agent@localhost:5432/"
OUT = REPO / "data/r60_showcase_sandbox"

# FK-safe order. created_by_tool_invocation_id is deliberately dropped (set to
# NULL): tool_invocations is an ingestion audit table, not a product entity.
TABLES = {
    "sources": None,
    "source_snapshots": {"created_by_tool_invocation_id": None},
    "companies": None,
    "company_aliases": None,
    "jobs": None,
    "job_tags": None,
    "claims": None,
    "claim_evidence": None,
    "job_description_versions": None,
    "job_requirements": None,
}


async def main() -> int:
    root = await asyncpg.connect(ROOT_DSN)
    try:
        exists = await root.fetchval("select count(*) from pg_database where datname=$1", TARGET)
        if not exists:
            await root.execute(f'CREATE DATABASE "{TARGET}"')
            created = True
        else:
            created = False
    finally:
        await root.close()

    env = dict(os.environ)
    env["DATABASE_URL"] = f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{TARGET}"
    env["MODEL_LEDGER_DATABASE"] = TARGET
    env["PYTHONPATH"] = str(API_ROOT / "src")
    head = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                          cwd=str(API_ROOT), env=env, capture_output=True, text=True)
    if head.returncode != 0:
        print(head.stdout[-1500:], head.stderr[-1500:])
        raise SystemExit("alembic upgrade failed")

    src = await asyncpg.connect(PG + SOURCE)
    dst = await asyncpg.connect(PG + TARGET)
    copied = {}
    try:
        version = await dst.fetchval("select version_num from alembic_version")
        for table, overrides in TABLES.items():
            cols = [r["column_name"] for r in await src.fetch(
                "select column_name from information_schema.columns "
                "where table_name=$1 and table_schema='public' order by ordinal_position", table)]
            rows = await src.fetch(f"select * from {table}")
            if not rows:
                copied[table] = 0
                continue
            values = []
            for row in rows:
                record = dict(row)
                for key, value in (overrides or {}).items():
                    record[key] = value
                values.append(tuple(record[column] for column in cols))
            placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
            await dst.executemany(
                f"insert into {table} ({', '.join(cols)}) values ({placeholders})", values)
            copied[table] = len(values)
            await dst.execute(
                f"select setval(pg_get_serial_sequence('{table}', 'id'), "
                f"(select max(id) from {table}))")
        checks = {}
        for key, sql in {
            "published_versions": "select count(*) from job_description_versions where publication_status='PUBLISHED'",
            "waiting_versions": "select count(*) from job_description_versions where publication_status='WAITING_REVIEW'",
            "selectable_jobs": "select count(*) from jobs j join companies c on c.id=j.company_id "
                               "where c.status='ACTIVE' and j.status='ACTIVE' and j.id in "
                               "(select job_id from job_description_versions where publication_status='PUBLISHED')",
            "requirements": "select count(*) from job_requirements",
            "claim_evidence": "select count(*) from claim_evidence",
            "evidence_offset_ok": "select count(*) from claim_evidence ce join source_snapshots ss "
                                  "on ss.id=ce.source_snapshot_id where "
                                  "substr(ss.content_text, (ce.source_location_json->>'start_char')::int + 1, "
                                  "(ce.source_location_json->>'end_char')::int - "
                                  "(ce.source_location_json->>'start_char')::int) = ce.excerpt",
        }.items():
            checks[key] = await dst.fetchval(sql)
    finally:
        await src.close()
        await dst.close()

    report = {"created_new_database": created, "target_database": TARGET,
              "source_database": SOURCE, "alembic_version": version,
              "copied_rows": copied, "checks": checks,
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "model_http_calls": 0,
              "alembic_stdout_tail": head.stdout[-400:]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sandbox.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print("written", OUT / "sandbox.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
