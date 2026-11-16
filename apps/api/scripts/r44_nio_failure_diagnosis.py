"""Round 35, task 1: locate the NIO metadata failure without any model call.

Read-only. Connects to the R31 sandbox database, dumps every artefact the failed
revision left behind (refresh task, agent run, per-step audit, ledger rows,
snapshots) and then answers one question: is the offending `attribute` recoverable
from what was persisted?

It does not open a browser, does not call a provider, and writes only a JSON file
under data/r44_nio_diagnosis/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "data/r44_nio_diagnosis"
SANDBOX = "interview_agent_wb_jd_r31_20261116"
DSN = f"postgresql://interview_agent:interview_agent@localhost:5432/{SANDBOX}"

# Every column that could plausibly carry the rejected attribute, checked
# explicitly so "not found" is a measurement rather than an assumption.
PROBE_TABLES = [
    "public_data_refresh_tasks", "experience_agent_runs", "experience_agent_step_runs",
    "model_admissions", "source_snapshots", "sources", "tool_invocations",
    "claims", "claim_evidence", "job_requirements", "job_description_versions",
    "jobs", "companies", "interview_experiences", "audit_logs",
]


async def dump(conn) -> dict:
    tasks = await conn.fetch(
        "select id, target_type, target_ref, reason, status, error_message, idempotency_key, "
        "requested_at, started_at, completed_at, created_at, updated_at "
        "from public_data_refresh_tasks where id in (2,4) order by id")
    runs = await conn.fetch(
        "select id, refresh_task_id, agent_role, graph_name, graph_version, status, input_hash, "
        "output_json, error_message, started_at, finished_at "
        "from experience_agent_runs where refresh_task_id in (2,4) order by id")
    steps = await conn.fetch(
        "select id, agent_run_id, step_name, status, provider_name, model_name, prompt_name, "
        "prompt_version, structured_retry, token_usage_json, input_json, output_json, "
        "error_message, latency_ms from experience_agent_step_runs "
        "where agent_run_id = any($1::bigint[]) order by id",
        [r["id"] for r in runs] or [0])
    ledger = await conn.fetch(
        "select call_key, owner_key, route, model, status, reserved_tokens, charged_tokens, "
        "actual_tokens, created_at, finished_at from model_admissions order by created_at")
    snap_ids = []
    for task in tasks:
        ref = task["target_ref"] or ""
        if ref.startswith("manual:"):
            snap_ids.append(int(ref.split(":", 1)[1]))
    snaps = await conn.fetch(
        "select id, source_id, content_hash, title, length(content_text) as chars, fetched_at, "
        "created_by_tool_invocation_id, metadata_json from source_snapshots "
        "where id = any($1::bigint[]) order by id",
        snap_ids or [0])
    nio_entities = {}
    for table, sql in {
        "companies": "select count(*) from companies where canonical_name like '%蔚来%'",
        "jobs": "select count(*) from jobs j join companies c on c.id=j.company_id where c.canonical_name like '%蔚来%'",
        "jd_versions": "select count(*) from job_description_versions v "
                       "join jobs j on j.id=v.job_id join companies c on c.id=j.company_id "
                       "where c.canonical_name like '%蔚来%'",
    }.items():
        nio_entities[table] = await conn.fetchval(sql)
    column_hits = {}
    for table in PROBE_TABLES:
        rows = await conn.fetch(
            "select column_name from information_schema.columns "
            "where table_schema='public' and table_name=$1 "
            "and column_name in ('attributes','attribute_json','metadata','metadata_json',"
            "'extracted_json','extraction_json','raw_output','response_json','payload_json')",
            table)
        if rows:
            column_hits[table] = [r["column_name"] for r in rows]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": SANDBOX,
        "tasks": [dict(r) for r in tasks],
        "runs": [dict(r) for r in runs],
        "steps": [dict(r) for r in steps],
        "ledger": [dict(r) for r in ledger],
        "snapshots_for_task_4": [dict(r) for r in snaps],
        "nio_entity_counts": nio_entities,
        "plausible_attribute_columns": column_hits,
    }


async def has_col(conn, table, col) -> bool:
    return await conn.fetchval(
        "select count(*) from information_schema.columns where table_schema='public' "
        "and table_name=$1 and column_name=$2", table, col) > 0


def _load(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _keys(value):
    value = _load(value)
    return list(value.keys()) if isinstance(value, dict) else []


def _short(value, limit=600):
    value = _load(value)
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text[:limit]


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "capture_audit.json"))
    args = ap.parse_args()
    conn = await asyncpg.connect(DSN)
    try:
        dbname = await conn.fetchval("select current_database()")
        assert dbname == SANDBOX, f"refused: connected to {dbname}"
        data = await dump(conn)
    finally:
        await conn.close()
    data = _jsonable(data)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"database={data['database']}")
    for task in data["tasks"]:
        print(f"task#{task['id']} {task['status']} ref={task['target_ref']} err={task['error_message']}")
    for run in data["runs"]:
        print(f"run#{run['id']} task={run['refresh_task_id']} {run['status']} err={run['error_message']}")
    for step in data["steps"]:
        print(f"step#{step['id']} run={step['agent_run_id']} {step['step_name']} {step['status']} "
              f"prompt={step['prompt_name']}@{step['prompt_version']} tokens={step['token_usage_json']}")
        print(f"   input_keys={sorted(_keys(step['input_json']))}")
        print(f"   output_keys={sorted(_keys(step['output_json']))}")
        print(f"   output={_short(step['output_json'])}")
    print("ledger rows:", len(data["ledger"]),
          "tokens:", sum((r["actual_tokens"] or r["reserved_tokens"] or 0) for r in data["ledger"]))
    print("snapshots(task4):", data["snapshots_for_task_4"])
    print("nio entities:", data["nio_entity_counts"])
    print("columns that could hold attributes:", data["plausible_attribute_columns"])
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
