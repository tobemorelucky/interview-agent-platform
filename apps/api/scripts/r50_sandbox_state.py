"""Round 35: read-only snapshot of the sandbox's real-JD assets.

Zero HTTP, zero writes. Counts, publication states, audit rows and the public
`targets` projection are captured so the round's report can quote them exactly.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

REPO = Path(__file__).resolve().parents[3]
SANDBOX = "interview_agent_wb_jd_r31_20261116"
OUT = REPO / "data/r50_state"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "sandbox_state.json"))
    args = ap.parse_args()
    conn = await asyncpg.connect(
        f"postgresql://interview_agent:interview_agent@localhost:5432/{SANDBOX}")
    try:
        assert await conn.fetchval("select current_database()") == SANDBOX
        counts = {}
        for table in ("companies", "jobs", "job_description_versions", "job_requirements",
                      "claims", "claim_evidence", "sources", "source_snapshots",
                      "public_data_refresh_tasks", "experience_agent_runs",
                      "experience_agent_step_runs", "model_admissions", "audit_logs", "users"):
            counts[table] = await conn.fetchval(f"select count(*) from {table}")
        status = await conn.fetch(
            "select publication_status, count(*) n from job_description_versions group by 1 order by 1")
        jobs = await conn.fetch(
            "select v.id, c.canonical_name, j.canonical_title, v.version_number, "
            "v.publication_status, v.content_fingerprint, "
            "(select count(*) from job_requirements r where r.job_description_version_id = v.id) reqs "
            "from job_description_versions v join jobs j on j.id = v.job_id "
            "join companies c on c.id = j.company_id order by v.id")
        audits = await conn.fetch(
            "select id, action, actor_role, resource_type, resource_id, status, created_at "
            "from audit_logs order by id")
        ledger = await conn.fetchrow(
            "select count(*) n, coalesce(sum(coalesce(actual_tokens, reserved_tokens, 0)),0) tokens, "
            "count(distinct model) models, array_agg(distinct status) statuses from model_admissions")
        models = await conn.fetch("select distinct model from model_admissions")
        tasks = await conn.fetch(
            "select id, target_type, status, left(error_message, 120) err from public_data_refresh_tasks order by id")
        users = await conn.fetch("select id, email, role, is_active from users order by id")
        snapshots = await conn.fetch(
            "select id, content_hash, length(content_text) chars from source_snapshots order by id")
    finally:
        await conn.close()

    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "database": SANDBOX,
               "counts": counts,
               "publication_status": {r["publication_status"]: r["n"] for r in status},
               "jobs": [dict(r) for r in jobs],
               "audit_logs": [dict(r) for r in audits],
               "ledger": {**dict(ledger), "models": [r["model"] for r in models]},
               "refresh_tasks": [dict(r) for r in tasks],
               "users": [dict(r) for r in users],
               "snapshots": [dict(r) for r in snapshots]}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    print("counts:", json.dumps(counts, ensure_ascii=False))
    print("publication:", payload["publication_status"])
    for job in payload["jobs"]:
        print(f"  #{job['id']} {job['canonical_name'][:6]:8} {job['canonical_title'][:34]:36} "
              f"v{job['version_number']} {job['publication_status']:<15} reqs={job['reqs']}")
    print("ledger:", payload["ledger"])
    print("audit:", [(a["action"], a["resource_id"]) for a in payload["audit_logs"]])
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
