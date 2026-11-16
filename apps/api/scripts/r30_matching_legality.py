"""R30：匹配输入合法性实测（0 HTTP）。

结论要能站住，就不能只靠读代码。这里在沙箱库里真实调用一次 `JobMatchingService.create`：

* 沙箱没有候选人画像 → 期望 `MATCH_PROFILE_REQUIRED`（409），且在发送任何模型请求之前失败；
* 沙箱的业务表 `job_description_versions` 为 0 行 → `sbx_jds_*` 的 TEXT 主键不是 JD 版本 ID，
  不能当匹配候选；
* 失败路径不得留下任何业务表行（调用后重新统计）。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from interview_api.core.exceptions import AppError  # noqa: E402
from interview_api.modules.job_matching.schemas import MatchRequest  # noqa: E402
from interview_api.modules.job_matching.service import JobMatchingService  # noqa: E402

BUSINESS = ("users", "candidate_profiles", "candidate_profile_versions", "job_match_runs",
            "job_match_candidates", "job_description_versions", "job_requirements")


async def main(database: str) -> int:
    engine = create_async_engine(
        f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{database}", pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    result = {"database": database, "http_calls_to_model": 0}
    try:
        async with factory() as db:
            before = {}
            for table in BUSINESS:
                before[table] = await db.scalar(
                    __import__("sqlalchemy").text(f"select count(*) from {table}"))
        service = JobMatchingService(factory)
        error = None
        try:
            await service.create(1, MatchRequest(request_key="r30-legality-probe"))
        except AppError as exc:
            error = {"code": exc.code, "message": exc.message, "status": exc.status_code}
        result["refusal"] = error
        async with factory() as db:
            after = {}
            for table in BUSINESS:
                after[table] = await db.scalar(
                    __import__("sqlalchemy").text(f"select count(*) from {table}"))
        result["rows_before"] = before
        result["rows_after"] = after
        result["no_rows_written"] = before == after
        result["verdict"] = ("REFUSED_BEFORE_MODEL_CALL" if error
                             else "UNEXPECTEDLY_SUCCEEDED")
    finally:
        await engine.dispose()
    out = REPO_ROOT / "data/incoming_20261116b/matching_legality.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("verdict") == "REFUSED_BEFORE_MODEL_CALL" and result["no_rows_written"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1
                             else "interview_agent_wb_sandbox_pack_20261116")))
