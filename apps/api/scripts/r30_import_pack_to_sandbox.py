"""R30：把《2026_真实岗位与面经_可入库纠正版》写入**明确指定的隔离沙箱**的 sbx_* 夹具表。

为什么不用包内的 `08_仅隔离PostgreSQL导入.py`：它依赖 `psycopg`，本环境未安装；
本脚本用项目已有的 `asyncpg` 复刻同样的三道守卫，并额外做读回校验：

1. 必须显式给出 `--database`，且库名必须含 `_sandbox_`；
2. 必须显式给出 `--confirm` 且与库名逐字相等；
3. 只建/写 5 张 `sbx_*` 表，**绝不触碰业务表**（导入后立刻统计业务表行数证明为 0）。

不做任何模型调用。不改动 JSONL 内容：`payload_json` 原样保存，另存规范化后的 sha256。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
PACK = REPO_ROOT / "data/incoming_20261116b/2026_真实岗位与面经_可入库纠正版"

FILES = {
    "sbx_jds_sourced": ("01_真实岗位来源_完整结构化改写_非逐字.jsonl", "jd_id"),
    "sbx_jds_synthetic": ("02_50份完整合成JD_可直接测试入库.jsonl", "jd_id"),
    "sbx_interview_flows": ("03_三份2026公开面经_完整流程元数据.jsonl", "flow_id"),
    "sbx_interview_questions": ("04_三份2026面经_按顺序改写问题.jsonl", "question_id"),
    "sbx_answer_pairs": ("05_20题40份模拟正确错误回答.jsonl", "answer_id"),
}
BUSINESS_TABLES = ("users", "resumes", "candidate_profiles", "companies", "jobs",
                   "job_description_versions", "job_requirements", "user_job_targets",
                   "interview_sessions", "interview_questions", "interview_answers",
                   "interview_evaluations", "final_interview_reports",
                   "capability_memories", "model_admissions")


def canonical(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()

    if "_sandbox_" not in args.database:
        raise SystemExit(f"拒绝：库名必须含 _sandbox_，实际 {args.database}")
    if args.confirm != args.database:
        raise SystemExit("拒绝：--confirm 必须与 --database 逐字相等")

    import asyncpg

    dsn = ("postgresql://interview_agent:interview_agent@localhost:5432/" + args.database)
    conn = await asyncpg.connect(dsn)
    summary = {"database": args.database, "http_calls_to_model": 0, "tables": {}}
    try:
        current = await conn.fetchval("select current_database()")
        assert current == args.database, f"连到了 {current}"
        for table, (filename, key) in FILES.items():
            rows = [json.loads(line) for line in
                    (PACK / filename).read_text(encoding="utf-8").splitlines() if line.strip()]
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id TEXT PRIMARY KEY,
                    provenance TEXT NOT NULL,
                    payload_json JSONB NOT NULL,
                    content_sha256 TEXT NOT NULL)""")
            inserted = 0
            for row in rows:
                raw = canonical(row)
                provenance = (row.get("provenance") or row.get("source_type")
                              or row.get("question_provenance") or "SOURCE_METADATA")
                await conn.execute(f"""
                    INSERT INTO {table}(id, provenance, payload_json, content_sha256)
                    VALUES ($1,$2,$3::jsonb,$4)
                    ON CONFLICT (id) DO UPDATE SET provenance=EXCLUDED.provenance,
                        payload_json=EXCLUDED.payload_json,
                        content_sha256=EXCLUDED.content_sha256""",
                    row[key], provenance, raw, hashlib.sha256(raw.encode()).hexdigest())
                inserted += 1
            stored = await conn.fetch(f"select id, content_sha256 from {table}")
            by_id = {record["id"]: record["content_sha256"] for record in stored}
            mismatched = [row[key] for row in rows
                          if by_id.get(row[key]) != hashlib.sha256(canonical(row).encode()).hexdigest()]
            summary["tables"][table] = {"source_rows": len(rows), "inserted": inserted,
                                        "stored_rows": len(stored),
                                        "hash_mismatched": mismatched}
            assert len(stored) == len(rows) and not mismatched, f"{table} 读回校验失败"

        business = {}
        for table in BUSINESS_TABLES:
            exists = await conn.fetchval(
                "select exists(select 1 from information_schema.tables "
                "where table_schema='public' and table_name=$1)", table)
            business[table] = (await conn.fetchval(f"select count(*) from {table}")) if exists else None
        summary["business_tables_after_import"] = business
        summary["business_tables_untouched"] = all(value in (0, None) for value in business.values())
    finally:
        await conn.close()

    out = REPO_ROOT / "data/incoming_20261116b/import_result.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["business_tables_untouched"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
