"""R32：6 场公开面经 / 59 道改写题的离线核验（0 次模型调用）。

四项：来源一致性、题号与题数一致、题目近重复（含跨包）、系统主题覆盖。
结论只描述这 59 道题本身，不推断面试事件真实性——来源是发帖者自述。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from interview_api.modules.interview.topics import mentioned_topics  # noqa: E402

BASE = REPO_ROOT / "data/incoming_20261116c"
PUNCT = re.compile(r"[\s，。？！、；：“”‘’（）()【】\[\]《》<>·…—\-_,.!?;:\"']+")


def normalize(text: str) -> str:
    return PUNCT.sub("", text or "")


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    flows = load(BASE / "02_公开面经流程_6场.jsonl")
    questions = load(BASE / "03_面经有出处改写题目.jsonl")

    problems: list[str] = []
    by_flow: dict[str, list[dict]] = {}
    for row in questions:
        by_flow.setdefault(row["flow_id"], []).append(row)

    if len(flows) != 6:
        problems.append(f"流程数应为 6，实际 {len(flows)}")
    if len(questions) != 59:
        problems.append(f"题目数应为 59，实际 {len(questions)}")

    flow_rows = []
    for flow in flows:
        items = by_flow.get(flow["flow_id"], [])
        turns = sorted(row["turn_index_within_selected_set"] for row in items)
        expected = list(range(1, len(items) + 1))
        mismatched_url = [row["id"] for row in items if row.get("source_url") != flow.get("report_url")]
        flow_rows.append({
            "flow_id": flow["flow_id"], "company": flow.get("company"),
            "role": flow.get("role"), "reported_date": flow.get("interview_date_reported"),
            "declared": flow.get("selected_question_count"), "actual": len(items),
            "turn_contiguous": turns == expected,
            "url_mismatch": mismatched_url,
            "provenance": flow.get("provenance"),
            "event_independently_verified": flow.get("independently_verified_event"),
            "wording": flow.get("question_wording"),
        })
        if flow.get("selected_question_count") != len(items):
            problems.append(f"{flow['flow_id']} 声明 {flow.get('selected_question_count')} 题、实际 {len(items)} 题")
        if turns != expected:
            problems.append(f"{flow['flow_id']} 题号不连续：{turns}")
        if mismatched_url:
            problems.append(f"{flow['flow_id']} 有题目来源与流程不一致：{mismatched_url}")

    for row in questions:
        if row.get("is_model_generated_interview_record"):
            problems.append(f"{row['id']} 被称为模型生成的面试记录")
        if row.get("is_actual_human_answer"):
            problems.append(f"{row['id']} 声称含真实回答")
        if row.get("is_human_gold"):
            problems.append(f"{row['id']} 被称为人工金标")

    near = []
    for index, left in enumerate(questions):
        for right in questions[index + 1:]:
            ratio = similarity(left["question_paraphrase"], right["question_paraphrase"])
            if ratio >= 0.6:
                near.append({"a": left["id"], "b": right["id"], "ratio": round(ratio, 3)})
    near.sort(key=lambda row: row["ratio"], reverse=True)

    # 跨包：与第 30 轮 23 题、第 31 轮 39 题比对
    cross = []
    for label, path in (("pack30", REPO_ROOT / "data/incoming_20261116/真实面经与JD扩充包_20261116/01_2026真实面经_23道改写问题.jsonl"),
                        ("pack31", REPO_ROOT / "data/incoming_20261116b/2026_真实岗位与面经_可入库纠正版/04_三份2026面经_按顺序改写问题.jsonl")):
        if not path.exists():
            continue
        for other in load(path):
            text = other.get("question") or other.get("question_text") or ""
            for row in questions:
                ratio = similarity(row["question_paraphrase"], text)
                if ratio >= 0.7:
                    cross.append({"pack": label, "other_id": other.get("id") or other.get("question_id"),
                                  "this_id": row["id"], "ratio": round(ratio, 3)})

    topics = []
    for row in questions:
        hits = mentioned_topics(row["question_paraphrase"])
        topics.append({"id": row["id"], "topics": hits})
    covered = sum(1 for row in topics if row["topics"])

    report = {
        "http_calls_to_model": 0,
        "counts": {"flows": len(flows), "questions": len(questions),
                   "declared_total": sum(flow.get("selected_question_count") or 0 for flow in flows)},
        "flows": flow_rows,
        "problems": problems,
        "duplicates": {"near_pairs": near, "max_ratio": max((r["ratio"] for r in near), default=0.0),
                       "cross_pack": cross},
        "topics": {"covered_questions": covered, "uncovered": len(questions) - covered,
                   "distribution": dict(Counter(topic for row in topics for topic in row["topics"]))},
        "provenance_verdict": "PUBLIC_USER_REPORTED_INTERVIEW / 未独立核实事件 / 题目为改写",
    }
    out = BASE / "flow_analysis.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("counts", "problems")}, ensure_ascii=False, indent=2))
    print("duplicates max:", report["duplicates"]["max_ratio"], "near pairs:", len(near),
          "cross:", len(cross))
    print("topics:", report["topics"]["covered_questions"], "/", len(questions),
          report["topics"]["distribution"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
