"""R29 第二批：《真实面经与JD扩充包_20261116》夹具契约（0 HTTP、0 数据库）。

约束来自包内 README 与本轮任务书：
* 23 道题是按来源主题**改写**的，不是逐字全文，也不是模型出题；
* 18 份回答是 AI 合成的 GOOD/POOR 样本，不是真实候选人回答，也不是人工金标；
* 题目与回答都必须带「不可作为官方题库/真实记录」的显式标记。

这些断言都能在坏输入上失败：改一个标记、删一份回答、把两条 POOR 写重复，都会红。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPO_ROOT / "data/sandbox_test_pkg_20261114/R29_面经扩充包_20261116"
PACK = REPO_ROOT / "data/incoming_20261116/真实面经与JD扩充包_20261116"
PUNCT = re.compile(r"[\s，。？！、；：“”‘’（）()【】\[\]《》<>·…—\-_,.!?;:\"']+")

TOPIC_MAP = {"rag": "rag_retrieval", "agent": "agent", "redis": "redis"}
UNMAPPED = {"cs_fundamentals", "algorithm"}


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize(text: str) -> str:
    return PUNCT.sub("", text or "")


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


@pytest.fixture(scope="module")
def questions() -> list[dict]:
    return load(FIXTURE / "01_面试改写题_夹具.jsonl")


@pytest.fixture(scope="module")
def answers() -> list[dict]:
    return load(FIXTURE / "02_模拟回答_夹具.jsonl")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((FIXTURE / "MANIFEST.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def annotations() -> dict:
    return json.loads((FIXTURE / "03_回答配对标注_非金标.json").read_text(encoding="utf-8"))


def test_question_count_and_unique_ids(questions):
    assert len(questions) == 23
    ids = [row["id"] for row in questions]
    assert len(set(ids)) == 23
    assert all(row["question"].strip() for row in questions)


def test_questions_declare_paraphrase_not_model_not_official(questions):
    for row in questions:
        assert row["question_origin"].startswith("PARAPHRASED_FROM_PUBLIC_INTERVIEW")
        assert row["source_kind"] == "PUBLIC_REPORTED_INTERVIEW"
        assert row["is_real_interview_transcript"] is False
        assert row["can_import_as_official_question"] is False
        assert row["official_question_bank_import_allowed"] is False
        assert row["model_generated_question"] is False
        assert row["not_model_generated"] is True
        assert row["real_candidate_answer_available"] is False


def test_questions_are_not_near_duplicates(questions):
    worst = 0.0
    for index, left in enumerate(questions):
        for right in questions[index + 1:]:
            worst = max(worst, similarity(left["question"], right["question"]))
    assert worst < 0.55, f"存在近重复题（最高 {worst:.2f}）"


def test_topic_mapping_is_explicit(questions):
    topics = Counter(row["topic"] for row in questions)
    mapped = {topic for topic in topics if topic in TOPIC_MAP}
    assert mapped | UNMAPPED == set(topics), f"出现未定义映射的主题: {set(topics) - mapped - UNMAPPED}"
    assert sum(topics[topic] for topic in mapped) == 16
    assert sum(topics[topic] for topic in UNMAPPED) == 7


def test_answer_pairs_are_complete_and_synthetic(answers):
    assert len(answers) == 18
    pairs = Counter(row["question_id"] for row in answers)
    assert set(pairs.values()) == {2}, f"配对不完整: {pairs}"
    assert len(pairs) == 9
    labels = Counter(row["quality_label_for_offline_gold_only"] for row in answers)
    assert labels == {"GOOD_SYNTHETIC": 9, "POOR_SYNTHETIC": 9}
    for row in answers:
        assert row["answer_provenance"] == "ASSISTANT_SYNTHETIC_NOT_REAL_INTERVIEW"
        assert row["real_candidate"] is False
        assert row["not_real_candidate_answer"] is True
        assert row["not_human_gold"] is True
        assert row["quality_label_must_be_blinded"] is True
        assert row["scoring_status"] == "CANDIDATE_REFERENCE_NOT_HUMAN_GOLD"


def test_good_answers_are_longer_and_not_copies(answers):
    grouped: dict[str, dict] = {}
    for row in answers:
        grouped.setdefault(row["question_id"], {})[row["quality_label_for_offline_gold_only"]] = row
    for qid, pair in grouped.items():
        good, poor = pair["GOOD_SYNTHETIC"], pair["POOR_SYNTHETIC"]
        ratio = len(good["answer"]) / len(poor["answer"])
        assert ratio >= 1.2, f"{qid} GOOD 未明显长于 POOR（{ratio:.2f}）"
        assert similarity(good["answer"], poor["answer"]) < 0.5, f"{qid} 两答案过于接近"
        assert len(good["technical_checkpoints"]) >= 2, f"{qid} GOOD 缺少检查点"
        assert len(poor["technical_checkpoints"]) >= 2, f"{qid} POOR 缺少检查点"


def test_quality_labels_do_not_leak_into_answer_text(answers):
    for row in answers:
        text = row["answer"]
        assert "GOOD_SYNTHETIC" not in text and "POOR_SYNTHETIC" not in text
        assert "quality_label" not in text


def test_annotations_are_explicitly_not_gold(annotations):
    assert annotations["is_human_gold"] is False
    assert annotations["annotation_author"] == "ASSISTANT_REVIEW"
    pairs = annotations["pairs"]
    assert len(pairs) == 9
    assert len({row["question_id"] for row in pairs}) == 9
    for row in pairs:
        assert row["poor_answer_errors"].strip(), "标注不能为空"


def test_manifest_hashes_match_source_pack(manifest):
    pack_dir = REPO_ROOT / manifest["source_pack"]["dir"]
    for name, expected in manifest["source_pack"]["files"].items():
        actual = hashlib.sha256((pack_dir / name).read_bytes()).hexdigest()
        assert actual == expected, f"{name} 与 manifest 记录的哈希不一致"
    assert manifest["counts"] == {"questions": 23, "answers": 18, "answer_pairs": 9}
    assert manifest["constraints"]["model_calls"] == 0
    assert manifest["constraints"]["written_to_business_tables"] is False
    assert manifest["constraints"]["official_question_bank"] is False
    assert manifest["source_provenance"]["verdict"] == "UNVERIFIED_SOURCE_ORIGINAL_PAGE_GONE"
