"""R29 前置：真实 JD × 真实简历 全链路验收的零 HTTP 预检。

本脚本**不发送任何模型请求**，只做四类只读/本地检查：

1. 运行环境断言：Provider、模型名、闸门开关、账本目标库。
2. 沙箱就绪：指定库的迁移版本、表数量、关键业务表是否为空。
3. 文件存储：MinIO bucket 可枚举（只列，不写不删）。
4. 材料准入：`data/manual_sources` 的真实 JD / 面经原件逐份核对 sha256，
   用规则化 `mentioned_topics` 计算每份 JD 能命中哪些主题，
   并明确统计「真实简历」为 0 份的缺口。

所有结论写入 JSON，供 docs/108 引用。任何一项失败都以非零退出码返回，
不允许把「没检查到」写成「通过」。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sqlalchemy.engine import make_url  # noqa: E402

from interview_api.core.config import settings  # noqa: E402
from interview_api.modules.interview.topics import TOPICS, mentioned_topics  # noqa: E402

REPO_ROOT = API_ROOT.parents[1]
MANUAL_DIR = REPO_ROOT / "data/manual_sources/2026-10-05"
SKILL_ROOT = SRC_ROOT / "interview_api/modules/interview/interviewer_skills"


def skill_priority_topics() -> dict:
    """每个技能声明的 priority_topics —— 主题能不能进入蓝图取决于它，而不是硬编码列表。"""
    topics = {}
    for path in sorted(SKILL_ROOT.glob("*/1.0.0/skill.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        topics[data["id"]] = list(data.get("priority_topics") or [])
    return topics


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_environment() -> dict:
    provider = {
        "llm_provider": settings.llm_provider,
        "use_modelscope": settings.use_modelscope,
        "model": settings.modelscope_model,
        "api_key_present": bool(settings.modelscope_api_key.get_secret_value()),
        "api_key_prefix": (settings.modelscope_api_key.get_secret_value()[:3] + "***")
        if settings.modelscope_api_key.get_secret_value() else None,
    }
    gates = {
        "purge_execution_enabled": settings.purge_execution_enabled,
        "interview_coach_harness": settings.interview_coach_harness,
    }
    return {"provider": provider, "gates": gates}


async def check_sandbox(database: str) -> dict:
    import asyncpg

    url = make_url(settings.database_url)
    dsn = (f"postgresql://{url.username}:{url.password}@{url.host}:{url.port or 5432}/{database}")
    conn = await asyncpg.connect(dsn)
    try:
        version = await conn.fetchval("select version_num from alembic_version")
        tables = await conn.fetchval(
            "select count(*) from information_schema.tables where table_schema='public'")
        counts = {}
        for table in ("users", "resumes", "candidate_profiles", "companies",
                      "job_description_versions", "user_job_targets",
                      "interview_sessions", "interview_answers", "interview_questions",
                      "interview_evaluations", "final_interview_reports",
                      "model_admissions", "capability_memories"):
            exists = await conn.fetchval(
                "select exists(select 1 from information_schema.tables "
                "where table_schema='public' and table_name=$1)", table)
            counts[table] = (await conn.fetchval(f"select count(*) from {table}")) if exists else None
        return {"database": database, "alembic_version": version, "table_count": tables,
                "row_counts": counts,
                "empty": all(value == 0 for value in counts.values() if value is not None)}
    finally:
        await conn.close()


def check_process_database() -> dict:
    """进程级账本目标：简历抽取走全局 ``async_session_factory``，不看注入的 factory。

    ``infrastructure/llm/provider.py`` 的 ``resume_report`` 路由直接使用
    ``async_session_factory``，因此只把 harness 自己的 factory 指向沙箱是不够的：
    进程的 ``DATABASE_URL`` 也必须指向沙箱，否则简历抽取会把准入记录
    静默写进正式库的账本。
    """
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.infrastructure.models.scope import database_of, expected_ledger_database

    return {"session_factory_database": database_of(async_session_factory),
            "expected_ledger_database": expected_ledger_database(),
            "resume_extraction_uses_global_factory": True}


def check_storage() -> dict:
    from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
    provider = MinioObjectStorageProvider()
    client = provider._client
    bucket = settings.minio_bucket
    exists = client.bucket_exists(bucket)
    objects = list(client.list_objects(bucket, recursive=True)) if exists else []
    return {"endpoint": settings.minio_endpoint, "bucket": bucket, "bucket_exists": exists,
            "object_count": len(objects)}


def check_materials() -> dict:
    manifest = json.loads((MANUAL_DIR / "manifest.json").read_text(encoding="utf-8"))
    skills = skill_priority_topics()
    union = {topic for items in skills.values() for topic in items}
    jd_rows, exp_rows = [], []
    for source in manifest["sources"]:
        path = REPO_ROOT / source["local_file"]
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        digest = sha256_of(path) if path.exists() else None
        row = {
            "id": source["id"], "company": source["company"], "job_title": source["job_title"],
            "source_type": source["source_type"], "document_type": source["document_type"],
            "source_url": source["source_url"], "chars": len(text),
            "sha256_match": digest == source["content_sha256"],
            "acquisition_mode": source.get("acquisition_mode"),
        }
        topics = mentioned_topics(text)
        row["topics"] = topics
        row["skills"] = sorted(name for name, items in skills.items() if set(topics) & set(items))
        row["blueprint_targets"] = sorted(set(topics) & union)
        row["runnable"] = bool(row["blueprint_targets"])
        (jd_rows if source["document_type"] == "job_jd" else exp_rows).append(row)

    resumes = [p for p in REPO_ROOT.glob("data/**/*简历*") if p.is_file()]
    real_resumes = [str(p.relative_to(REPO_ROOT)) for p in resumes
                    if "测试" not in p.name and "synthetic" not in p.name.lower()]
    return {
        "manifest": {"path": str(MANUAL_DIR / "manifest.json"),
                     "live_search_used": manifest.get("live_search_used"),
                     "acquisition_mode": manifest.get("acquisition_mode"),
                     "sources": len(manifest["sources"])},
        "jd": jd_rows, "experience": exp_rows,
        "resume": {"candidate_paths": [str(p.relative_to(REPO_ROOT)) for p in resumes],
                   "real_resume_count": len(real_resumes), "real_resumes": real_resumes},
        "topic_vocabulary_size": len(TOPICS),
        "skill_priority_topics": skills,
    }


def verdict(report: dict, *, sandbox_expected: str) -> dict:
    blocking, warnings = [], []
    env = report["environment"]
    if not env["provider"]["use_modelscope"]:
        blocking.append(f"PROVIDER_NOT_MODELSCOPE:{env['provider']['llm_provider'] or '(empty)'}")
    if not env["provider"]["api_key_present"]:
        blocking.append("MODELSCOPE_KEY_MISSING")
    for gate, value in env["gates"].items():
        if value:
            blocking.append(f"GATE_ON:{gate}")
    ledger = env.get("ledger_target") or {}
    if ledger.get("database") != sandbox_expected:
        blocking.append(f"LEDGER_TARGET_MISMATCH:{ledger.get('database')}")

    process_db = report["process_database"]
    if process_db["session_factory_database"] != sandbox_expected:
        blocking.append(f"PROCESS_DATABASE_NOT_SANDBOX:{process_db['session_factory_database']}")
    if process_db["expected_ledger_database"] != sandbox_expected:
        blocking.append(f"EXPECTED_LEDGER_NOT_SANDBOX:{process_db['expected_ledger_database']}")

    sandbox = report["sandbox"]
    if sandbox["alembic_version"] != "0029":
        blocking.append(f"SANDBOX_MIGRATION:{sandbox['alembic_version']}")
    if sandbox["table_count"] != 68:
        blocking.append(f"SANDBOX_TABLE_COUNT:{sandbox['table_count']}")
    if not sandbox["empty"]:
        dirty = {k: v for k, v in sandbox["row_counts"].items() if v}
        blocking.append(f"SANDBOX_NOT_EMPTY:{dirty}")

    if not report["storage"]["bucket_exists"]:
        blocking.append("MINIO_BUCKET_MISSING")

    materials = report["materials"]
    bad_sha = [row["id"] for row in materials["jd"] + materials["experience"] if not row["sha256_match"]]
    if bad_sha:
        blocking.append(f"SOURCE_SHA256_MISMATCH:{bad_sha}")
    runnable = [row["id"] for row in materials["jd"] if row["runnable"]]
    if not runnable:
        blocking.append("NO_RUNNABLE_REAL_JD")
    if materials["resume"]["real_resume_count"] == 0:
        blocking.append("REAL_RESUME_MISSING")

    missing = ["真实简历（脱敏）", "候选人授权与岗位意向", "人工参考答案/评分金标复核"]
    return {"ready": not blocking, "blocking": blocking, "warnings": warnings,
            "missing_materials": missing, "runnable_jd": runnable}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox", default="interview_agent_wb_r26_alt_20261115")
    parser.add_argument("--output", default=str(
        REPO_ROOT / "data/sandbox_test_pkg_20261114/r29_precheck.json"))
    args = parser.parse_args()

    report = {"round": "R29_PRECHECK", "http_calls": 0,
              "generated_at": datetime.now(timezone.utc).isoformat()}
    report["environment"] = check_environment()
    report["environment"]["ledger_target"] = {
        "database": settings.model_ledger_database or None,
        "env_declared": bool(settings.model_ledger_database),
    }
    report["sandbox"] = await check_sandbox(args.sandbox)
    report["process_database"] = check_process_database()
    report["storage"] = check_storage()
    report["materials"] = check_materials()
    report["verdict"] = verdict(report, sandbox_expected=args.sandbox)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ready": report["verdict"]["ready"],
                      "blocking": report["verdict"]["blocking"],
                      "runnable_jd": report["verdict"]["runnable_jd"],
                      "sandbox": report["sandbox"]["database"],
                      "sandbox_empty": report["sandbox"]["empty"],
                      "output": str(out)}, ensure_ascii=False, indent=2))
    return 0 if report["verdict"]["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(__import__("asyncio").run(main()))
