"""R29 第二批：把面经题与模拟回答转成沙箱夹具（0 次模型调用，不写业务表）。

夹具的用途只有一个：**离线评测素材**。它显式声明自己不是模型出题、不是官方题库、
不是真实候选人回答，任何人把它当真实记录使用都能从字段上看出来。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK = REPO_ROOT / "data/incoming_20261116/真实面经与JD扩充包_20261116"
OUT = REPO_ROOT / "data/sandbox_test_pkg_20261114/R29_面经扩充包_20261116"
FIXTURE_VERSION = "R29_PACK_20261116_V1"

# 9 对 POOR 的可证伪错误点（本轮人工标注，明确不是金标、不参与自动打分）
POOR_ANNOTATIONS = {
    "FH20260609_Q01": "检索与生成顺序颠倒（先编答案再找证据）；无命中时凭常识作答；引用可自动生成",
    "FH20260609_Q03": "固定 100 字不重叠切块；表格按字符拆；称标题无用；称切得越短越准",
    "FH20260609_Q07": "删除否定词与时间词；改写得越长越好；不保留原问题",
    "FH20260609_Q08": "把阈值设成负无穷强行出结果；自动编造文档引用",
    "FH20260609_Q09": "称 BM25 与向量得分同量纲且在 0–1；把 RRF 说成得分相乘取对数",
    "FH20260609_Q12": "所有步骤塞进一个节点、状态只存最后答案；异常无限递归；断言 LangGraph 必须配 Checkpointer",
    "FH20260609_Q14": "用 GET 加 SET 冒充原子；DEL 不校验持有者；称锁过期不会引发并发写入",
    "FH20260609_Q15": "穿透/雪崩/击穿三者定义互换；主张所有 key 统一 TTL",
    "FH20260609_Q16": "称所有命令 O(1)、每请求新建线程；称大 key 不会阻塞",
}


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    questions = load(PACK / "01_2026真实面经_23道改写问题.jsonl")
    answers = load(PACK / "02_9道题_18份模拟好坏回答.jsonl")

    question_lines = []
    for row in questions:
        question_lines.append(json.dumps({
            **row,
            "fixture_version": FIXTURE_VERSION,
            "fixture_kind": "PUBLIC_INTERVIEW_PARAPHRASED_QUESTION",
            "not_model_generated": True,
            "model_generated_question": False,
            "official_question_bank_import_allowed": False,
            "usable_for": ["OFFLINE_TOPIC_COVERAGE", "OFFLINE_DUPLICATE_CHECK", "HUMAN_REFERENCE_ONLY"],
            "forbidden_use": ["AS_MAIN_AGENT_LIVE_QUESTION_RECORD", "AS_OFFICIAL_PUBLISHED_QUESTION"],
        }, ensure_ascii=False))
    (OUT / "01_面试改写题_夹具.jsonl").write_text("\n".join(question_lines) + "\n", encoding="utf-8")

    answer_lines = []
    for row in answers:
        answer_lines.append(json.dumps({
            **row,
            "fixture_version": FIXTURE_VERSION,
            "fixture_kind": "ASSISTANT_SYNTHETIC_ANSWER",
            "not_real_candidate_answer": True,
            "not_human_gold": True,
            "quality_label_must_be_blinded": True,
            "usable_for": ["OFFLINE_EVALUATION_SHAPE_CHECK", "HUMAN_REFERENCE_ONLY"],
            "forbidden_use": ["AS_TRAINING_GOLD", "AS_REAL_USER_HISTORY", "AS_MEMORY_EVIDENCE"],
        }, ensure_ascii=False))
    (OUT / "02_模拟回答_夹具.jsonl").write_text("\n".join(answer_lines) + "\n", encoding="utf-8")

    annotations = {
        "fixture_version": FIXTURE_VERSION,
        "annotation_author": "ASSISTANT_REVIEW",
        "is_human_gold": False,
        "semantics": "逐条列出 POOR 答案中可指认的技术错误，仅用于人工复核对照；不是评分金标，不参与自动打分",
        "pairs": [{"question_id": qid, "poor_answer_errors": text}
                  for qid, text in POOR_ANNOTATIONS.items()],
    }
    (OUT / "03_回答配对标注_非金标.json").write_text(
        json.dumps(annotations, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "fixture_version": FIXTURE_VERSION,
        "created_at": "2026-11-16",
        "purpose": "离线评测素材；不得作为官方题库或真实面试记录",
        "source_pack": {
            "dir": str(PACK.relative_to(REPO_ROOT)),
            "files": {path.name: sha256(path) for path in sorted(PACK.iterdir()) if path.is_file()},
        },
        "source_provenance": {
            "url": questions[0]["source_url"],
            "title": questions[0]["source_title"],
            "reported_interview_date": questions[0]["reported_interview_date"],
            "publication_date": questions[0]["publication_date"],
            "page_status_2026_11_16": "HTTP 404（原页不可访问）",
            "archive_lookup": "未完成：本机无法连接 archive.org（代理 502）",
            "verdict": "UNVERIFIED_SOURCE_ORIGINAL_PAGE_GONE",
            "meaning": "23 道题只能按「包内自述来源」使用；不能声称公开页面仍可核验",
        },
        "counts": {"questions": len(questions), "answers": len(answers),
                   "answer_pairs": len({row["question_id"] for row in answers})},
        "constraints": {
            "model_calls": 0,
            "written_to_business_tables": False,
            "official_question_bank": False,
            "human_gold": False,
        },
    }
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dir": str(OUT.relative_to(REPO_ROOT)), **manifest["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
