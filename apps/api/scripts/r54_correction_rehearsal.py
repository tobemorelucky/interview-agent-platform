"""Round 36, phase 3: offline rehearsal of the M1 correction path.

Zero HTTP. For 面壁 #4 and 蔚来 #6 this asks the service to restore the page's own
wording, validates every correction against the already-cited evidence, and then
runs the full write (successor version + claims + evidence + audit) inside a
savepoint that is always rolled back. Whatever the database refuses is reported as
the blocker rather than worked around.

Also runs one negative probe: a statement that sounds plausible but is not in the
cited text must be rejected before any write.

Output: data/r54_corrections/corrections.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from interview_api.core.exceptions import AppError  # noqa: E402
from interview_api.modules.public_career.corrections import (  # noqa: E402
    Correction, JdCorrectionService, literal_within,
)

SANDBOX = "interview_agent_wb_jd_r31_20261116"
OUT = REPO / "data/r54_corrections"

# Restoring the page's own wording, quoted from the stored body.
CASES = [
    {
        "jd_version_id": 4, "company": "面壁智能", "label": "语序还原",
        "expect": {"requirement_id": 40,
                   "after": "计算机、人工智能或相关专业的2027届应届毕业生，本科及以上学历",
                   "why": "原文为「计算机、人工智能或相关专业的2027届应届毕业生，本科及以上学历；」，"
                          "模型把修饰语换位；此处按原文顺序还原，语义不变，但文字回到原文"},
    },
    {
        "jd_version_id": 6, "company": "蔚来", "label": "限定词还原",
        "expect": {"requirement_id": 57,
                   "after": "通过各种技术手段持续优化性能，降低推理成本，包括但不限于:算子/编译优化、异构推理、分布式并行等",
                   "why": "原文有「包括但不限于:」；模型删掉后把开放清单收窄成封闭清单"},
    },
    {
        "jd_version_id": 6, "company": "蔚来", "label": "英文注释与连接词还原",
        "expect": {"requirement_id": 58,
                   "after": "学习并应用业界前沿的推理优化技术，KV Cache优化、模型量化(Quantization)、剪枝(Pruning)，跨机分布式推理优化等。",
                   "why": "原文保留英文注释并用逗号分隔；模型加了「包括」、删了注释"},
    },
    {
        "jd_version_id": 6, "company": "蔚来", "label": "虚词还原",
        "expect": {"requirement_id": 63,
                   "after": "熟悉至少一种主流的深度学习框架(如 PyTorch、TensorFlow)，了解其基本实现原理",
                   "why": "原文是「主流的深度学习框架」；模型删了一个「的」"},
    },
]

NEGATIVE = {
    "jd_version_id": 6, "requirement_id": 63,
    "statement": "熟悉至少一种主流的深度学习框架，了解其编译器后端实现与算子融合",
    "why": "听起来合理但原文没有「编译器后端」「算子融合」——必须被拒绝",
}


async def counts(conn) -> dict:
    return {
        "versions": await conn.fetchval("select count(*) from job_description_versions"),
        "requirements": await conn.fetchval("select count(*) from job_requirements"),
        "claims": await conn.fetchval("select count(*) from claims"),
        "evidence": await conn.fetchval("select count(*) from claim_evidence"),
        "audit": await conn.fetchval("select count(*) from audit_logs"),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "corrections.json"))
    args = ap.parse_args()

    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{SANDBOX}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    conn = await asyncpg.connect(f"postgresql://interview_agent:interview_agent@localhost:5432/{SANDBOX}")
    try:
        assert await conn.fetchval("select current_database()") == SANDBOX
        admin = await conn.fetchrow("select id, email, role from users where role = 'ADMIN' order by id limit 1")
        before = await counts(conn)
    finally:
        await conn.close()

    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "database": SANDBOX,
              "model_http_calls": 0, "reviewer": {"id": admin["id"], "email": admin["email"]},
              "before": before, "runs": [], "negative": None}

    for case in CASES:
        correction = Correction(case["expect"]["requirement_id"], case["expect"]["after"])
        async with factory() as session:
            service = JdCorrectionService(session)
            try:
                outcome = await service.apply(
                    case["jd_version_id"], reviewer_id=admin["id"],
                    reason=f"R36 离线演练：{case['label']}。{case['expect']['why']}",
                    corrections=[correction], dry_run=True)
                entry = {k: case[k] for k in ("jd_version_id", "company", "label")}
                entry["expect"] = case["expect"]
                entry["plan"] = [{k: item[k] for k in
                                  ("requirement_id", "before", "after", "before_supported",
                                   "after_supported", "importance", "requirement_type")}
                                 for item in outcome["plan"]["requirements"]]
                entry["fingerprint"] = outcome["plan"]["fingerprint"]
                entry["applied"] = outcome["applied"]
                entry["rolled_back"] = outcome.get("rolled_back")
                entry["blocked_by"] = outcome.get("blocked_by")
                entry["detail"] = outcome.get("detail")
                entry["version_number_would_be"] = outcome.get("version_number")
                report["runs"].append(entry)
            except AppError as exc:
                report["runs"].append({**{k: case[k] for k in ("jd_version_id", "company", "label")},
                                       "refused": {"code": exc.code, "message": str(exc)}})
            finally:
                await session.rollback()

    async with factory() as session:
        service = JdCorrectionService(session)
        try:
            await service.apply(NEGATIVE["jd_version_id"], reviewer_id=admin["id"],
                                reason="R36 负向探针：验证不得放行非原文语句",
                                corrections=[Correction(NEGATIVE["requirement_id"], NEGATIVE["statement"])],
                                dry_run=True)
            report["negative"] = {"statement": NEGATIVE["statement"], "rejected": False}
        except AppError as exc:
            report["negative"] = {"statement": NEGATIVE["statement"], "rejected": True,
                                  "code": exc.code, "message": str(exc)}
        finally:
            await session.rollback()

    conn = await asyncpg.connect(f"postgresql://interview_agent:interview_agent@localhost:5432/{SANDBOX}")
    try:
        after = await counts(conn)
    finally:
        await conn.close()
    report["after"] = after
    report["rows_written"] = {key: after[key] - before[key] for key in before}
    report["literal_within_selfcheck"] = {
        "supported": literal_within([{"source_snapshot_id": 1, "start": 0, "end": 4, "excerpt": "包括但不限于"}],
                                    "包括但不限于"),
        "substring_still_supported": literal_within(
            [{"source_snapshot_id": 1, "start": 0, "end": 4, "excerpt": "包括但不限于"}], "包括"),
        "unsupported": literal_within(
            [{"source_snapshot_id": 1, "start": 0, "end": 4, "excerpt": "包括但不限于"}], "包括算子融合"),
        "gap_not_bridged": literal_within(
            [{"source_snapshot_id": 1, "start": 0, "end": 2, "excerpt": "第一段"},
             {"source_snapshot_id": 1, "start": 5, "end": 8, "excerpt": "第二段"}], "第一段第二段"),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    for entry in report["runs"]:
        if "refused" in entry:
            print(f"#{entry['jd_version_id']} {entry['label']}: REFUSED {entry['refused']}")
            continue
        plan = entry["plan"][0]
        print(f"#{entry['jd_version_id']} {entry['label']}: r{plan['requirement_id']} "
              f"before_supported={plan['before_supported']} after_supported={plan['after_supported']} "
              f"applied={entry['applied']} blocked={entry['blocked_by']}")
    print("negative:", report["negative"])
    print("rows written:", report["rows_written"])
    print("written:", out)
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
