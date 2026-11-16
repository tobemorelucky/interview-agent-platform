"""R29 第二批：23 道面经改写题 + 18 份模拟回答的离线质量分析（0 次模型调用）。

分析项：
1. JSONL 契约：字段、ID 唯一、来源与「非真实」标注是否互相矛盾；
2. 题目近重复：包内两两相似度 + 与 V3 题目、与既有面经原件的跨语料比对；
3. 主题覆盖：把包内 topic 映射到系统 `topics.py` 词表，并计算系统正则能否识别；
4. 模拟回答质量：GOOD/POOR 长度、相似度、技术检查点命中（只做可证伪的机械检查，
   不把「看起来像好答案」当成人工金标）。

结果写入 `data/incoming_20261116/pack_analysis.json`，供 docs/109 与契约测试引用。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps/api/src"))

from interview_api.modules.interview.topics import TOPICS, mentioned_topics  # noqa: E402

BASE = REPO_ROOT / "data/incoming_20261116"
PACK = BASE / "真实面经与JD扩充包_20261116"

# 包内 topic → 系统词表。未映射的明确列出，不做「看起来差不多」的猜测。
TOPIC_MAP = {"rag": "rag_retrieval", "agent": "agent", "redis": "redis",
             "cs_fundamentals": None, "algorithm": None}
UNMAPPED_REASON = {
    "cs_fundamentals": "系统词表没有「计算机基础」主题，只有具体技术域；数据结构/设计模式题无法落到蓝图 target",
    "algorithm": "系统词表无「算法」主题；排序/动态规划题不属于任何技能 priority_topics",
}

PUNCT = re.compile(r"[\s，。？！、；：“”‘’（）()【】\[\]《》<>·…—\-_,.!?;:\"']+")


def normalize(text: str) -> str:
    return PUNCT.sub("", text or "")


def grams(text: str, size: int = 3) -> set[str]:
    text = normalize(text)
    return {text[i:i + size] for i in range(max(0, len(text) - size + 1))}


def similarity(left: str, right: str) -> tuple[float, float]:
    a, b = normalize(left), normalize(right)
    sequence = SequenceMatcher(None, a, b).ratio()
    ga, gb = grams(a), grams(b)
    jaccard = len(ga & gb) / len(ga | gb) if ga | gb else 0.0
    return round(sequence, 4), round(jaccard, 4)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def contract_issues(rows: list[dict]) -> list[str]:
    issues = []
    if len(rows) != 23:
        issues.append(f"题目数量应为 23，实际 {len(rows)}")
    required = {"id", "source_url", "source_title", "source_kind", "reported_interview_date",
                "publication_date", "question", "question_origin", "topic", "difficulty",
                "real_candidate_answer_available", "is_real_interview_transcript",
                "review_status", "can_import_as_official_question"}
    for row in rows:
        missing = required - set(row)
        if missing:
            issues.append(f"{row.get('id')} 缺字段 {sorted(missing)}")
        if row.get("is_real_interview_transcript"):
            issues.append(f"{row.get('id')} 标为真实逐字记录，与包说明冲突")
        if row.get("can_import_as_official_question"):
            issues.append(f"{row.get('id')} 允许作为官方题库导入，与包说明冲突")
        if not row.get("question_origin", "").startswith("PARAPHRASED"):
            issues.append(f"{row.get('id')} 来源类型不是改写：{row.get('question_origin')}")
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        issues.append("题目 ID 重复")
    return issues


def main() -> int:
    questions = load_jsonl(PACK / "01_2026真实面经_23道改写问题.jsonl")
    answers = load_jsonl(PACK / "02_9道题_18份模拟好坏回答.jsonl")
    by_id = {row["id"]: row for row in questions}

    # ---- 1. 契约 ----
    answer_issues = []
    if len(answers) != 18:
        answer_issues.append(f"回答应为 18 份，实际 {len(answers)}")
    for row in answers:
        if row.get("real_candidate"):
            answer_issues.append(f"{row['id']} 标为真实候选人回答")
        if row.get("answer_provenance") != "ASSISTANT_SYNTHETIC_NOT_REAL_INTERVIEW":
            answer_issues.append(f"{row['id']} 来源标注异常：{row.get('answer_provenance')}")
        if row.get("question_id") not in by_id:
            answer_issues.append(f"{row['id']} 指向不存在的题目")
    pairs = Counter(row["question_id"] for row in answers)
    bad_pairs = {qid: count for qid, count in pairs.items() if count != 2}

    # ---- 2. 近重复 ----
    near_pairs = []
    for index, left in enumerate(questions):
        for right in questions[index + 1:]:
            sequence, jaccard = similarity(left["question"], right["question"])
            if sequence >= 0.55 or jaccard >= 0.4:
                near_pairs.append({"a": left["id"], "b": right["id"],
                                   "sequence": sequence, "jaccard": jaccard})
    near_pairs.sort(key=lambda row: row["sequence"], reverse=True)

    v3_path = next((REPO_ROOT / "data/sandbox_test_pkg_20261114").glob(
        "2026校招JD_真实面经_模拟回答测试包/10_V3*.jsonl"))
    v3 = load_jsonl(v3_path)
    cross = []
    for row in questions:
        for ref in v3:
            sequence, jaccard = similarity(row["question"], ref["question"])
            if sequence >= 0.6:
                cross.append({"pack": row["id"], "v3": ref["question_id"],
                              "sequence": sequence, "jaccard": jaccard})

    # ---- 3. 主题覆盖 ----
    topics = Counter(row["topic"] for row in questions)
    mapping = {}
    for topic in topics:
        mapped = TOPIC_MAP.get(topic)
        mapping[topic] = {
            "count": topics[topic],
            "system_topic": mapped,
            "in_vocabulary": bool(mapped and mapped in TOPICS),
            "reason_if_unmapped": None if mapped else UNMAPPED_REASON.get(topic),
            "recognized_by_regex": sum(1 for row in questions
                                       if row["topic"] == topic
                                       and mentioned_topics(row["question"])),
        }
    mapped_questions = sum(item["count"] for item in mapping.values() if item["in_vocabulary"])

    # ---- 4. 模拟回答质量（只做机械可证伪检查）----
    pair_rows = []
    grouped: dict[str, dict] = {}
    for row in answers:
        grouped.setdefault(row["question_id"], {})[row["quality_label_for_offline_gold_only"]] = row
    for qid, item in grouped.items():
        good, poor = item.get("GOOD_SYNTHETIC"), item.get("POOR_SYNTHETIC")
        if not good or not poor:
            continue
        checkpoints = good.get("technical_checkpoints") or []
        # 逐条检查点与答案的 2-gram 覆盖率：只说明「措辞沾边」，不代表答对，故只作排序信号。
        coverage = []
        for point in checkpoints:
            gp, ga = grams(point, 2), grams(good["answer"], 2)
            coverage.append(round(len(gp & ga) / len(gp), 3) if gp else 0.0)
        sequence, jaccard = similarity(good["answer"], poor["answer"])
        pair_rows.append({
            "question_id": qid,
            "good_chars": len(good["answer"]), "poor_chars": len(poor["answer"]),
            "length_ratio": round(len(good["answer"]) / max(1, len(poor["answer"])), 2),
            "good_poor_sequence": sequence, "good_poor_jaccard": jaccard,
            "checkpoints": len(checkpoints),
            "checkpoint_bigram_coverage": coverage,
            "poor_checkpoints": len(poor.get("technical_checkpoints") or []),
            "labels_leaked_into_text": any(token in (good["answer"] + poor["answer"])
                                           for token in ("GOOD_SYNTHETIC", "POOR_SYNTHETIC", "quality_label")),
        })

    report = {
        "http_calls_to_model": 0,
        "source": {"url": questions[0]["source_url"], "title": questions[0]["source_title"],
                   "source_kind": questions[0]["source_kind"],
                   "reported_interview_date": questions[0]["reported_interview_date"],
                   "publication_date": questions[0]["publication_date"],
                   "source_page_checked": "HTTP 404（原页已不可访问；站点根域可访问）",
                   "archive_checked": "此环境无法连接 archive.org（代理 502），未确认是否存在快照",
                   "identity_verdict": "UNVERIFIED_SOURCE_ORIGINAL_PAGE_GONE"},
        "questions": {"count": len(questions), "contract_issues": contract_issues(questions),
                      "topics": mapping, "mapped_questions": mapped_questions,
                      "unmapped_questions": len(questions) - mapped_questions},
        "duplicates": {"near_pairs": near_pairs, "max_sequence": max(
            (row["sequence"] for row in near_pairs), default=0.0),
            "cross_corpus_v3": cross, "cross_max": max(
                (row["sequence"] for row in cross), default=0.0)},
        "answers": {"count": len(answers), "issues": answer_issues,
                    "pair_count_anomalies": bad_pairs,
                    "labels": dict(Counter(row["quality_label_for_offline_gold_only"] for row in answers)),
                    "pairs": pair_rows},
    }
    out = BASE / "pack_analysis.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "questions": report["questions"]["count"],
        "contract_issues": len(report["questions"]["contract_issues"]),
        "mapped_questions": mapped_questions,
        "unmapped_questions": report["questions"]["unmapped_questions"],
        "max_near_duplicate": report["duplicates"]["max_sequence"],
        "max_cross_v3": report["duplicates"]["cross_max"],
        "answer_issues": len(answer_issues),
        "pair_anomalies": bad_pairs,
        "pairs": len(pair_rows),
        "min_length_ratio": min((row["length_ratio"] for row in pair_rows), default=None),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
