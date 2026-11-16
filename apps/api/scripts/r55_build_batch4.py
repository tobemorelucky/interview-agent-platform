"""Round 36, phase 4: the final six-JD list for the next real batch.

Round 35's list included a product-manager role and a supply-chain role as
generalisation probes. Round 36 keeps the four software roles and swaps those two
for software / Agent roles from the same verified corpus, so the batch tests the
pipeline on the kind of JD the product actually serves.

Same projection rule, same hard refusals as round 34/35. No network, no model, no
database writes. The four carried-over records are asserted byte-identical to the
round-35 build, so this run cannot silently re-project them.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "scripts"))

import r47_build_batch3 as r47  # noqa: E402

SANDBOX = "interview_agent_wb_jd_r31_20261116"
DSN = f"postgresql://interview_agent:interview_agent@localhost:5432/{SANDBOX}"
OUT = REPO / "data/r55_batch4"
BATCH3 = REPO / "data/r47_batch3/source_manifest.json"

PICKS = [
    {"jd_key": "KYLINOS_SEC_5480b909", "record": "KYLINOS_5480b909", "company": "麒麟软件",
     "direction": "安全研发", "why": "官方详情页；安全研发方向，与已发布的内核/AI研发不重叠"},
    {"jd_key": "KYLINOS_EMB_cd3ac686", "record": "KYLINOS_cd3ac686", "company": "麒麟软件",
     "direction": "嵌入式", "why": "官方详情页；嵌入式/RTOS/BSP 方向，补操作系统生态侧第二种岗位"},
    {"jd_key": "MOONTON_QA_008", "record": "OFFICIAL_CAND_008", "company": "沐瞳科技",
     "direction": "测试/QA", "why": "官方飞书页；AI 测试平台方向，已发布 5 份没有测试岗"},
    {"jd_key": "NIO_CV_023", "record": "OFFICIAL_CAND_023", "company": "蔚来",
     "direction": "视觉算法", "why": "官方飞书页；CV 算法方向，与被扣下的推理引擎岗不同"},
    {"jd_key": "NIO_POSTTRAIN_018", "record": "OFFICIAL_CAND_018", "company": "蔚来",
     "direction": "大模型后训练", "why": "官方飞书页；座舱大模型后训练/Agentic 算法，替换 R35 的供应链算法，属 LLM 训练与 Agent 交叉方向"},
    {"jd_key": "MOONTON_UNITY_012", "record": "OFFICIAL_CAND_012", "company": "沐瞳科技",
     "direction": "游戏客户端", "why": "官方飞书页；Unity 客户端工程（活动玩法），替换 R35 的产品经理岗，属真正的软件开发方向"},
]

ALTERNATES = [
    {"jd_key": "NIO_HARNESS_017", "record": "OFFICIAL_CAND_017", "company": "蔚来",
     "direction": "Agent Harness", "why": "831 字，座舱 Agent Harness；与 018 同族，仅作替补"},
    {"jd_key": "MOONTON_CLIENT_011", "record": "OFFICIAL_CAND_011", "company": "沐瞳科技",
     "direction": "AI 客户端", "why": "834 字，AI 客户端-MOBA；与 012 同族，仅作替补"},
]


async def existing() -> dict:
    conn = await asyncpg.connect(DSN)
    try:
        assert await conn.fetchval("select current_database()") == SANDBOX
        rows = await conn.fetch(
            "select v.id, v.publication_status, v.content_fingerprint, j.canonical_title, "
            "j.normalized_title, c.canonical_name from job_description_versions v "
            "join jobs j on j.id = v.job_id join companies c on c.id = j.company_id order by v.id")
    finally:
        await conn.close()
    return {"rows": [dict(r) for r in rows],
            "titles": {"".join((r["canonical_title"] or "").split()) for r in rows},
            "fingerprints": {r["content_fingerprint"] for r in rows}}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=str(OUT / "source_manifest.json"))
    args = ap.parse_args()

    corpus = {row["jd_id"]: row for row in
              json.loads((r47.BASE / "jd_corpus.json").read_text(encoding="utf-8"))["records"]}
    r47.OUT = OUT  # build_one writes text files next to this batch's manifest
    records = [r47.build_one(pick, corpus) for pick in PICKS]

    batch3 = {row["jd_key"]: row for row in
              json.loads(BATCH3.read_text(encoding="utf-8"))["records"]}
    carried = [row for row in records if row["jd_key"] in batch3]
    if len(carried) != 4:
        raise SystemExit(f"expected 4 carried-over records, got {len(carried)}")
    for row in carried:
        if row["text_sha256"] != batch3[row["jd_key"]]["text_sha256"]:
            raise SystemExit(f"projection drifted for {row['jd_key']}")

    held = await existing()
    prior = json.loads((REPO / "data/r40_batch2/source_manifest.json").read_text(encoding="utf-8"))
    prior_ids = {row["job_id"] for row in prior["records"]}
    ids = [row["job_id"] for row in records]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate job id inside batch 4")
    for row in records:
        if row["job_id"] in prior_ids:
            raise SystemExit(f"job id already used in batch 2: {row['job_id']}")
        if row["text_sha256"] in held["fingerprints"]:
            raise SystemExit(f"identical body already stored: {row['jd_key']}")
        if "".join(row["job_title"].split()) in held["titles"]:
            raise SystemExit(f"same title already stored: {row['job_title']}")

    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "model_http_calls": 0,
               "database": SANDBOX, "batch": 4, "records": records,
               "alternates": [{"jd_key": row["jd_key"], "record": row["record"],
                               "company": row["company"], "direction": row["direction"],
                               "why": row["why"]} for row in ALTERNATES],
               "existing_sandbox": held["rows"]}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for row in records:
        print(f"{row['jd_key']:26} {row['company'][:6]:8} {row['job_title'][:34]:36} "
              f"{row['text_chars']:>5}字 {row['direction']:<12} "
              f"heads={bool(row['section_headers']['duty_heading'])}/{bool(row['section_headers']['skill_heading'])} "
              f"split={row['evidence_chunk_risk']['split_lines']}")
    print("written", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
