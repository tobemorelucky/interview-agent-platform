"""R30：读取沙箱里已导入的 sbx_* 表，做零 HTTP 的结构、去重、主题与技能识别分析。

数据来源是 **PostgreSQL 沙箱表**（不是本地 JSONL），这样才能证明「导入后」的数据可用：
* 数量与来源标注是否自洽（合成 JD 不得被标为真实岗位）；
* 正文是否为空/过短、同企业同岗位是否重复、正文指纹是否重复；
* `topics.py` 词表能覆盖多少岗位、`skill_recommendation.recommend()` 能给出哪个技能；
* 正式 JD 业务表的导入前提（逐字 Evidence）是否满足——不满足就明确写「不可导入」。

不做模型调用。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from interview_api.modules.interview.skill_recommendation import recommend  # noqa: E402
from interview_api.modules.job_target.service import rule_extract  # noqa: E402
from interview_api.modules.interview.topics import mentioned_topics  # noqa: E402

MANUAL = REPO_ROOT / "data/manual_sources/2026-10-05"
PUNCT = re.compile(r"[\s，。？！、；：“”‘’（）()【】\[\]《》<>·…—\-_,.!?;:\"']+")
TOPIC_MAP = {"rag": "rag_retrieval", "agent": "agent", "redis": "redis", "java": "java",
             "backend": None, "algorithm": None, "evaluation": None,
             "self_intro": None, "reverse": None}


def normalize(text: str) -> str:
    return PUNCT.sub("", text or "")


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def skill_for(record: dict) -> dict:
    job = {"title": record.get("job_title", ""),
           "responsibilities": record.get("responsibilities") or [],
           "requirements": [{"statement": item} for item in (record.get("requirements") or [])],
           "tags": (record.get("required_skills") or [])[:10]}
    result = recommend(job)
    ranking = result.get("ranking") or []
    leader = ranking[0] if ranking else None
    return {"status": result.get("status"), "auto_select": result.get("auto_select"),
            "recommended_skill_id": result.get("recommended_skill_id"),
            "leader": leader["skill_id"] if leader else None,
            "leader_points": leader["ranking_points"] if leader else None,
            "evidence": len(leader["evidence"]) if leader else 0,
            "reason": result.get("reason")}


async def load(conn, table: str) -> list[dict]:
    rows = await conn.fetch(f"select payload_json from {table} order by id")
    return [json.loads(row["payload_json"]) for row in rows]


async def main(database: str) -> int:
    import asyncpg
    conn = await asyncpg.connect("postgresql://interview_agent:interview_agent@localhost:5432/" + database)
    try:
        sourced = await load(conn, "sbx_jds_sourced")
        synthetic = await load(conn, "sbx_jds_synthetic")
        flows = await load(conn, "sbx_interview_flows")
        questions = await load(conn, "sbx_interview_questions")
        answers = await load(conn, "sbx_answer_pairs")
    finally:
        await conn.close()

    problems: list[str] = []

    # ---------- 1. 真实性标注不得被篡改 ----------
    for row in sourced:
        if row.get("full_verbatim_from_official") is not False:
            problems.append(f"{row['jd_id']} 声称逐字原文")
        if row.get("evidence_verified") is not False:
            problems.append(f"{row['jd_id']} 声称 evidence 已验证")
        if row.get("provenance") != "REAL_JOB_SOURCE_ORIGINAL_PARAPHRASE":
            problems.append(f"{row['jd_id']} 来源标注异常")
        if not row.get("source_url"):
            problems.append(f"{row['jd_id']} 缺来源链接")
    for row in synthetic:
        if row.get("is_real_job") is not False:
            problems.append(f"{row['jd_id']} 合成 JD 被标为真实岗位")
        if row.get("source_verified") is not False:
            problems.append(f"{row['jd_id']} 合成 JD 声称来源已验证")
        if "合成" not in row.get("company", "") and "非真实" not in row.get("company", ""):
            problems.append(f"{row['jd_id']} 公司名未标明合成：{row.get('company')}")
        if row.get("source_url"):
            problems.append(f"{row['jd_id']} 合成 JD 带来源链接")

    # ---------- 2. 空正文 / 过短 ----------
    short = [{"id": row["jd_id"], "chars": len(row.get("jd_text") or "")}
             for row in sourced + synthetic if len(row.get("jd_text") or "") < 200]
    missing_fields = [row["jd_id"] for row in sourced + synthetic
                      if not row.get("responsibilities") or not row.get("requirements")]

    # ---------- 3. 去重 ----------
    def dup_within(rows: list[dict]) -> list[dict]:
        found = []
        for index, left in enumerate(rows):
            for right in rows[index + 1:]:
                if left["sha256"] == right["sha256"]:
                    found.append({"a": left["jd_id"], "b": right["jd_id"], "kind": "SAME_SHA256"})
                    continue
                ratio = similarity(left["jd_text"], right["jd_text"])
                if ratio >= 0.9:
                    found.append({"a": left["jd_id"], "b": right["jd_id"],
                                  "kind": "NEAR_TEXT", "ratio": round(ratio, 3)})
        return found

    same_role = [{"company": key[0], "title": key[1], "count": count}
                 for key, count in Counter((row["company"], row["job_title"])
                                           for row in sourced).items() if count > 1]

    manual_bodies = {path.name: path.read_text(encoding="utf-8")
                     for path in MANUAL.glob("jd_*.txt")}
    cross_manual = []
    for row in sourced:
        for name, body in manual_bodies.items():
            ratio = similarity(row["jd_text"], body)
            if ratio >= 0.6:
                cross_manual.append({"pack": row["jd_id"], "manual": name,
                                     "ratio": round(ratio, 3)})

    # ---------- 4. 主题与技能 ----------
    topic_rows, skill_rows = [], []
    for row in sourced + synthetic:
        text = "\n".join([row["job_title"], *(row.get("responsibilities") or []),
                          *(row.get("requirements") or [])])
        topics = mentioned_topics(text)
        skill = skill_for(row)
        topic_rows.append({"id": row["jd_id"], "set": "sourced" if row in sourced else "synthetic",
                           "topics": topics, "topic_count": len(topics)})
        skill_rows.append({"id": row["jd_id"], "set": "sourced" if row in sourced else "synthetic",
                           **skill})

    coverage = {
        "sourced_with_topic": sum(1 for row in topic_rows if row["set"] == "sourced" and row["topics"]),
        "synthetic_with_topic": sum(1 for row in topic_rows if row["set"] == "synthetic" and row["topics"]),
        "sourced_status": dict(Counter(row["status"] for row in skill_rows if row["set"] == "sourced")),
        "synthetic_status": dict(Counter(row["status"] for row in skill_rows if row["set"] == "synthetic")),
        "sourced_recommended_skill": dict(Counter(
            row["recommended_skill_id"] for row in skill_rows if row["set"] == "sourced")),
        "synthetic_recommended_skill": dict(Counter(
            row["recommended_skill_id"] for row in skill_rows if row["set"] == "synthetic")),
        "sourced_leader": dict(Counter(row["leader"] for row in skill_rows if row["set"] == "sourced")),
        "synthetic_leader": dict(Counter(row["leader"] for row in skill_rows if row["set"] == "synthetic")),
        "topic_distribution": dict(Counter(
            topic for row in topic_rows for topic in row["topics"])),
    }

    # ---------- 4b. 解析（规则路径，非模型）→ Requirement → Skill ----------
    parse_rows = []
    for row in sourced + synthetic:
        parsed = rule_extract(row["jd_text"], row.get("company"), row.get("job_title"))
        requirements = parsed.get("requirements") or []
        result = recommend({"title": parsed.get("job_title") or row.get("job_title", ""),
                            "responsibilities": parsed.get("responsibilities") or [],
                            "requirements": requirements})
        parse_rows.append({
            "id": row["jd_id"], "set": "sourced" if row in sourced else "synthetic",
            "parsed_requirements": len(requirements),
            "parsed_responsibilities": len(parsed.get("responsibilities") or []),
            "skill_status": result.get("status"),
            "skill": result.get("recommended_skill_id"),
        })
    parse_summary = {
        "sourced": {"jobs": sum(1 for r in parse_rows if r["set"] == "sourced"),
                    "with_requirements": sum(1 for r in parse_rows if r["set"] == "sourced" and r["parsed_requirements"]),
                    "avg_requirements": round(sum(r["parsed_requirements"] for r in parse_rows if r["set"] == "sourced") / max(1, sum(1 for r in parse_rows if r["set"] == "sourced")), 1),
                    "status": dict(Counter(r["skill_status"] for r in parse_rows if r["set"] == "sourced")),
                    "recommended": dict(Counter(r["skill"] for r in parse_rows if r["set"] == "sourced" and r["skill"]))},
        "synthetic": {"jobs": sum(1 for r in parse_rows if r["set"] == "synthetic"),
                      "with_requirements": sum(1 for r in parse_rows if r["set"] == "synthetic" and r["parsed_requirements"]),
                      "avg_requirements": round(sum(r["parsed_requirements"] for r in parse_rows if r["set"] == "synthetic") / max(1, sum(1 for r in parse_rows if r["set"] == "synthetic")), 1),
                      "status": dict(Counter(r["skill_status"] for r in parse_rows if r["set"] == "synthetic")),
                      "recommended": dict(Counter(r["skill"] for r in parse_rows if r["set"] == "synthetic" and r["skill"]))},
        "zero_requirement_jobs": [r["id"] for r in parse_rows if not r["parsed_requirements"]],
        "note": "rule_extract 是零模型路径（parse_source=RULE），只证明文本可解析出可用要求，不代表模型解析质量",
    }

    # ---------- 5. 正式业务表导入前提 ----------
    formal_blockers = {
        "verbatim_evidence": "15 份正文是改写（full_verbatim_from_official=false）→ 无法通过逐字 Evidence 校验",
        "requirement_labels": "需求条目是自然语言列表，不是带 external_ref 的结构化 requirement",
        "owner_scope": "没有用户/岗位归属，user_job_targets 无法建立",
        "research_flow": "正式导入需 public_research_extract 解析（本轮 HTTP=0，未执行）",
    }

    report = {
        "database": database,
        "http_calls_to_model": 0,
        "counts": {"sourced": len(sourced), "synthetic": len(synthetic), "flows": len(flows),
                   "questions": len(questions), "answers": len(answers)},
        "provenance_problems": problems,
        "empty_or_short_bodies": short,
        "missing_responsibility_or_requirement": missing_fields,
        "duplicates": {"within_sourced": dup_within(sourced), "within_synthetic": dup_within(synthetic),
                       "same_company_role": same_role, "cross_manual": cross_manual},
        "coverage": coverage,
        "parse_chain": parse_summary,
        "formal_import_blockers": formal_blockers,
        "question_flows": {"per_flow": dict(Counter(row["flow_id"] for row in questions)),
                           "declared": {row["flow_id"]: row["question_count"] for row in flows}},
        "answers": {"labels": dict(Counter(row["quality_label"] for row in answers)),
                    "unique_questions": len({row["question_id"] for row in answers}),
                    "human_gold": sum(1 for row in answers if row.get("is_human_gold")),
                    "with_pii": sum(1 for row in answers if row.get("contains_private_personal_data"))},
    }
    out = REPO_ROOT / "data/incoming_20261116b/pack_analysis.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("counts", "provenance_problems", "empty_or_short_bodies",
                       "missing_responsibility_or_requirement", "coverage", "parse_chain", "answers")},
                     ensure_ascii=False, indent=2))
    print("duplicates:", json.dumps(report["duplicates"], ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1
                             else "interview_agent_wb_sandbox_pack_20261116")))
