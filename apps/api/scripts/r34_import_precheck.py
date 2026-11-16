"""R31: zero-HTTP precheck for the approved 3-JD formal import trial.

Read-only: config assertions, read-only SQL, storage listing, source file
integrity and content-hygiene checks. No model call, no write.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import asyncpg

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "r34_target_jd"
MANIFEST = DATA / "source_manifest.json"

EXPECTED_DATABASE = "interview_agent_wb_jd_r31_20261116"
EXPECTED_MINIO_BUCKET = "interview-agent-r31-jd"
EXPECTED_REDIS_DB = "/7"

BUSINESS_TABLES = (
    "users", "resumes", "companies", "jobs", "job_description_versions", "job_requirements",
    "sources", "source_snapshots", "claims", "claim_evidence", "public_data_refresh_tasks",
    "interview_sessions", "interview_evaluations", "final_interview_reports",
    "model_admissions", "capability_memories", "audit_logs", "user_job_targets",
)
NAV_MARKERS = ("首页", "热招岗位", "培养与发展", "校招答疑", "登录", "立即投递", "收藏", "投递简历", "站内搜索")


async def db_state(database: str) -> dict:
    conn = await asyncpg.connect(f"postgresql://interview_agent:interview_agent@localhost:5432/{database}")
    try:
        version = await conn.fetchval("select version_num from alembic_version")
        tables = await conn.fetchval(
            "select count(*) from information_schema.tables where table_schema='public'")
        counts = {}
        for table in BUSINESS_TABLES:
            exists = await conn.fetchval(
                "select exists(select 1 from information_schema.tables where table_schema='public' and table_name=$1)",
                table)
            counts[table] = (await conn.fetchval(f"select count(*) from {table}")) if exists else None
        return {"alembic_version": version, "public_tables": tables, "row_counts": counts}
    finally:
        await conn.close()


def storage_state(bucket: str) -> dict:
    from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
    provider = MinioObjectStorageProvider()
    buckets = [b.name for b in provider._client.list_buckets()]
    objects = 0
    if bucket in buckets:
        objects = len(list(provider._client.list_objects(bucket, recursive=True)))
    return {"bucket": bucket, "bucket_exists": bucket in buckets, "objects_in_bucket": objects,
            "bucket_list": buckets}


def redis_state() -> dict:
    import socket

    def info(db_index: int) -> dict:
        with socket.create_connection(("localhost", 6379), timeout=3) as sock:
            sock.sendall(f"SELECT {db_index}\r\nDBSIZE\r\n".encode())
            payload = sock.recv(4096).decode(errors="ignore")
        numbers = [int(value) for value in re.findall(r":(\d+)", payload)]
        return {"db": db_index, "dbsize": numbers[-1] if numbers else None}

    return {"url_configured": os.getenv("REDIS_URL"), "db0": info(0), "db7": info(7)}


def content_hygiene(text: str, others: list[str]) -> dict:
    nav_hits = [marker for marker in NAV_MARKERS if marker in text]
    other_titles = [title for title in others if title and title in text]
    return {"nav_markers_found": nav_hits, "other_job_titles_found": other_titles,
            "chars": len(text), "lines": len([line for line in text.splitlines() if line.strip()])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=EXPECTED_DATABASE)
    parser.add_argument("--output", default=str(DATA / "precheck.json"))
    args = parser.parse_args()

    from interview_api.core.config import settings
    from interview_api.infrastructure.models.router import build_model_router

    blocking: list[str] = []
    warnings: list[str] = []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    database_url = os.getenv("DATABASE_URL", "")
    ledger = os.getenv("MODEL_LEDGER_DATABASE", "")
    env = {
        "DATABASE_URL_database": database_url.rsplit("/", 1)[-1] if database_url else None,
        "MODEL_LEDGER_DATABASE": ledger or None,
        "expected_database": args.database,
        "process_database_matches": database_url.rsplit("/", 1)[-1] == args.database if database_url else False,
        "ledger_matches_process": ledger == args.database if ledger else False,
        "REDIS_URL": os.getenv("REDIS_URL"),
        "MINIO_BUCKET": os.getenv("MINIO_BUCKET"),
    }
    if not env["process_database_matches"]:
        blocking.append(f"DATABASE_URL_NOT_SANDBOX:{env['DATABASE_URL_database']}")
    if not env["ledger_matches_process"]:
        blocking.append(f"LEDGER_TARGET_NOT_SANDBOX:{env['MODEL_LEDGER_DATABASE']}")
    if (env["MINIO_BUCKET"] or "") != EXPECTED_MINIO_BUCKET:
        warnings.append(f"MINIO_BUCKET_NOT_ISOLATED:{env['MINIO_BUCKET']}")
    if EXPECTED_REDIS_DB not in (env["REDIS_URL"] or ""):
        warnings.append(f"REDIS_URL_NOT_ISOLATED:{env['REDIS_URL']}")

    router = build_model_router()
    providers = {}
    for route in ("public_research_extract", "public_research_quality"):
        provider = router.primary(route)
        providers[route] = {"provider": provider.provider_name, "model": provider.model_name,
                            "governed": bool(getattr(provider, "governed", False)),
                            "fallbacks": list(router.fallback_routes)}
    if any(item["provider"] != "modelscope" or item["model"] != "deepseek-ai/DeepSeek-V4.1-Flash-1017"
           for item in providers.values()):
        blocking.append("PROVIDER_MODEL_NOT_APPROVED")
    if any(item["fallbacks"] for item in providers.values()):
        blocking.append("FALLBACK_ROUTE_CONFIGURED")

    db = asyncio.run(db_state(args.database))
    if db["alembic_version"] != "0029":
        blocking.append(f"ALEMBIC_VERSION:{db['alembic_version']}")
    dirty = {table: count for table, count in db["row_counts"].items() if count not in (None, 0)}
    if dirty:
        blocking.append(f"SANDBOX_NOT_EMPTY:{dirty}")

    storage = storage_state(EXPECTED_MINIO_BUCKET)
    if not storage["bucket_exists"]:
        warnings.append("MINIO_BUCKET_ABSENT_WILL_BE_CREATED_ON_UPLOAD")
    redis = redis_state()
    if redis["db7"]["dbsize"]:
        warnings.append(f"REDIS_DB7_NOT_EMPTY:{redis['db7']['dbsize']}")

    corpus_titles = [to for to in (
        json.loads((REPO / "data/incoming_20261116c/jd_corpus.json").read_text(encoding="utf-8"))["records"])]
    targets = []
    for record in manifest["records"]:
        text_path = REPO / record["text_path"]
        text = text_path.read_text(encoding="utf-8")
        digest_ok = sha256(text.encode("utf-8")).hexdigest() == record["text_sha256"]
        if not digest_ok:
            blocking.append(f"SOURCE_SHA256_MISMATCH:{record['jd_key']}")
        if not record["still_live"]:
            blocking.append(f"POSTING_OFFLINE:{record['jd_key']}")
        # Same title from another company is not "another job body"; only a
        # *different* job's title appearing inside this page is a defect.
        own = re.sub(r"[\s\u3000]", "", record["job_title"])
        others = [row["title"] for row in corpus_titles
                  if re.sub(r"[\s\u3000]", "", row["title"] or "") != own
                  and row["company"] != record["company"]]
        hygiene = content_hygiene(text, others)
        if hygiene["other_job_titles_found"]:
            blocking.append(f"OTHER_JOB_BODY:{record['jd_key']}")
        targets.append({
            "jd_key": record["jd_key"], "company": record["company"], "job_title": record["job_title"],
            "job_id_kind": record["job_id_kind"], "job_id": record["job_id"],
            "source_url": record["source_url"], "source_grade": record["source_grade"],
            "text_sha256": record["text_sha256"], "sha256_matches_file": digest_ok,
            "captured_at": record["render"]["rendered_at"],
            "still_live": record["still_live"],
            "identity_in_text": record["identity_in_text"],
            "sections": record["sections"]["counts"],
            "hygiene": hygiene,
        })

    official_domains = settings.public_research_official_domains
    if not official_domains:
        warnings.append("OFFICIAL_DOMAINS_EMPTY_ALL_3_WILL_BE_WAITING_REVIEW")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_http_calls": 0,
        "environment": env,
        "providers": providers,
        "database": db,
        "storage": storage,
        "redis": redis,
        "official_domains": official_domains,
        "targets": targets,
        "blocking": blocking,
        "warnings": warnings,
        "ready": not blocking,
        "budget": {
            "normal_http": 6,
            "normal_breakdown": {"job_jd_extraction": 3, "quality_assessment": 3},
            "repair_http_allowance": 2,
            "hard_stop_http": 10,
            "token_cap": 30000,
            "prompt_versions": {"job_jd": "1.0.1 (manual)", "quality": "1.0.0"},
        },
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ready": report["ready"], "blocking": blocking, "warnings": warnings},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
