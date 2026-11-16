"""R31：真实简历 × 真实 JD 的零 HTTP 预检。

两个层次，可以分开跑：

* **环境预检**（`--env-only` 或默认）：沙箱库、进程库与账本目标、Owner 作用域、MinIO、Provider、冻结提示词；
* **简历预检**（`--resume <path>`）：格式/大小/文本抽取/字段段落/脱敏残留 —— 只读文件，不写库。

设计原则：**不猜**。简历缺哪个字段就报哪个字段缺失，不补写、不脑补；抽取不出文本（扫描版 PDF）
直接判失败，不做 OCR 兜底。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(SRC_ROOT))

from sqlalchemy.engine import make_url  # noqa: E402

from interview_api.core.config import settings  # noqa: E402
from interview_api.modules.resume.parser import ResumeParser  # noqa: E402

SUPPORTED = {"pdf", "docx"}
SECTION_PATTERNS = {
    "education": r"(教育|学历|教育背景|毕业院校)",
    "work_experience": r"(工作经历|工作经验|职业经历)",
    "internship": r"(实习|实习经历)",
    "projects": r"(项目|项目经历|项目经验)",
    "skills": r"(技能|专业技能|技术栈|掌握)",
    "awards": r"(获奖|荣誉|奖项|竞赛)",
}
PII_PATTERNS = {
    "phone": r"(?<!\d)1[3-9]\d{9}(?!\d)",
    "email": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "id_card": r"(?<!\d)\d{17}[\dXx](?!\d)",
    "bank_card": r"(?<!\d)\d{16,19}(?!\d)",
}


def provider_block() -> dict:
    return {"llm_provider": settings.llm_provider, "use_modelscope": settings.use_modelscope,
            "model": settings.modelscope_model,
            "api_key_present": bool(settings.modelscope_api_key.get_secret_value()),
            "api_key_prefix": (settings.modelscope_api_key.get_secret_value()[:3] + "***")
            if settings.modelscope_api_key.get_secret_value() else None}


def frozen_prompt_block() -> dict:
    import hashlib as _hash

    from interview_api.modules.interview.adaptive_prompts import EVALUATION_BOUNDARIES_PROMPT
    return {"version": EVALUATION_BOUNDARIES_PROMPT.version,
            "system_chars": len(EVALUATION_BOUNDARIES_PROMPT.system),
            "fingerprint": _hash.sha256(EVALUATION_BOUNDARIES_PROMPT.system.encode()).hexdigest()[:16],
            "matches_docs_108": (EVALUATION_BOUNDARIES_PROMPT.version == "2.3.4"
                                 and len(EVALUATION_BOUNDARIES_PROMPT.system) == 7452)}


async def env_checks(database: str) -> dict:
    import asyncpg

    url = make_url(settings.database_url)
    dsn = f"postgresql://{url.username}:{url.password}@{url.host}:{url.port or 5432}/{database}"
    conn = await asyncpg.connect(dsn)
    try:
        version = await conn.fetchval("select version_num from alembic_version")
        tables = await conn.fetchval(
            "select count(*) from information_schema.tables where table_schema='public'")
        counts = {}
        for table in ("users", "resumes", "resume_reports", "candidate_profiles",
                      "companies", "job_description_versions", "job_requirements",
                      "user_job_targets", "interview_sessions", "interview_evaluations",
                      "model_admissions"):
            counts[table] = await conn.fetchval(f"select count(*) from {table}")
    finally:
        await conn.close()

    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.infrastructure.models.scope import database_of, expected_ledger_database
    from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider

    client = MinioObjectStorageProvider()._client
    bucket = settings.minio_bucket
    exists = client.bucket_exists(bucket)
    objects = len(list(client.list_objects(bucket, recursive=True))) if exists else 0

    return {
        "provider": provider_block(),
        "gates": {"purge_execution_enabled": settings.purge_execution_enabled,
                  "interview_coach_harness": settings.interview_coach_harness},
        "sandbox": {"database": database, "alembic_version": version, "table_count": tables,
                    "row_counts": counts, "empty": all(v == 0 for v in counts.values())},
        "ledger": {"process_database": database_of(async_session_factory),
                   "expected_ledger_database": expected_ledger_database(),
                   "declared_env": settings.model_ledger_database or None},
        "storage": {"endpoint": settings.minio_endpoint, "bucket": bucket,
                    "bucket_exists": exists, "object_count": objects},
        "frozen_prompt": frozen_prompt_block(),
        "resume_limits": {"max_file_size_mb": settings.resume_max_file_size_mb,
                          "supported": sorted(SUPPORTED), "ocr": False},
    }


def resume_checks(path: Path) -> dict:
    suffix = path.suffix.lower().lstrip(".")
    data = path.read_bytes()
    result = {"path": str(path), "suffix": suffix, "bytes": len(data),
              "sha256": hashlib.sha256(data).hexdigest()}
    if suffix not in SUPPORTED:
        result["verdict"] = "UNSUPPORTED_FORMAT"
        result["note"] = "只支持 pdf / docx（无 OCR；扫描版 PDF 会抽不出文本；.doc 不支持）"
        return result
    if len(data) > settings.resume_max_file_size_mb * 1024 * 1024:
        result["verdict"] = "TOO_LARGE"
        return result
    try:
        text = ResumeParser().parse(data, suffix)
    except Exception as exc:  # noqa: BLE001 - 解析失败要如实报类型
        result["verdict"] = "PARSE_FAILED"
        result["error"] = type(exc).__name__
        return result
    text = text or ""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    sections = {name: bool(re.search(pattern, text)) for name, pattern in SECTION_PATTERNS.items()}
    pii = {name: len(re.findall(pattern, text)) for name, pattern in PII_PATTERNS.items()}
    result.update({
        "chars": len(text), "cjk_chars": cjk,
        "cjk_ratio": round(cjk / max(1, len(text)), 3),
        "lines": len([line for line in text.splitlines() if line.strip()]),
        "sections": sections,
        "pii_hits": pii,
        "pii_clean": all(value == 0 for value in pii.values()),
        "preview_head": text.strip()[:200],
    })
    if len(text.strip()) < 200:
        result["verdict"] = "TEXT_TOO_SHORT"
        result["note"] = "疑似扫描版 PDF 或空文档；平台无 OCR，不会兜底"
    elif not all(sections[name] for name in ("education", "projects", "skills")):
        result["verdict"] = "SECTION_HEADINGS_MISSING"
        result["note"] = "教育/项目/技能至少缺一段；画像证据选择会缺依据"
    elif not result["pii_clean"]:
        result["verdict"] = "PII_REMAINS"
        result["note"] = "仍有手机号/邮箱/证件号；请脱敏后重新提供"
    else:
        result["verdict"] = "READY_FOR_MAPPING"
    return result


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox", default="interview_agent_wb_r26_alt_20261115")
    parser.add_argument("--resume")
    parser.add_argument("--env-only", action="store_true")
    parser.add_argument("--output", default=str(REPO_ROOT / "data/r31_resume_precheck/precheck.json"))
    args = parser.parse_args()

    report = {"round": "R31_PRECHECK", "http_calls_to_model": 0,
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "environment": await env_checks(args.sandbox)}
    if args.resume and not args.env_only:
        report["resume"] = resume_checks(Path(args.resume))

    blocking = []
    env = report["environment"]
    if not env["provider"]["use_modelscope"]:
        blocking.append(f"PROVIDER_NOT_MODELSCOPE:{env['provider']['llm_provider'] or '(empty)'}")
    if not env["provider"]["api_key_present"]:
        blocking.append("MODELSCOPE_KEY_MISSING")
    if any(env["gates"].values()):
        blocking.append(f"GATE_ON:{env['gates']}")
    if env["sandbox"]["alembic_version"] != "0029" or env["sandbox"]["table_count"] != 68:
        blocking.append(f"SANDBOX_NOT_READY:{env['sandbox']['alembic_version']}/{env['sandbox']['table_count']}")
    if not env["sandbox"]["empty"]:
        blocking.append(f"SANDBOX_NOT_EMPTY:{ {k: v for k, v in env['sandbox']['row_counts'].items() if v} }")
    if env["ledger"]["process_database"] != args.sandbox:
        blocking.append(f"PROCESS_DATABASE_NOT_SANDBOX:{env['ledger']['process_database']}")
    if env["ledger"]["expected_ledger_database"] != args.sandbox:
        blocking.append(f"EXPECTED_LEDGER_NOT_SANDBOX:{env['ledger']['expected_ledger_database']}")
    if not env["storage"]["bucket_exists"]:
        blocking.append("MINIO_BUCKET_MISSING")
    if not env["frozen_prompt"]["matches_docs_108"]:
        blocking.append(f"FROZEN_PROMPT_DRIFT:{env['frozen_prompt']['version']}/{env['frozen_prompt']['system_chars']}")
    if "resume" in report:
        if report["resume"]["verdict"] != "READY_FOR_MAPPING":
            blocking.append(f"RESUME_{report['resume']['verdict']}")
    else:
        blocking.append("RESUME_NOT_PROVIDED")

    report["blocking"] = blocking
    report["ready"] = not blocking
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ready": report["ready"], "blocking": blocking,
                      "sandbox": args.sandbox, "output": str(out)}, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
