"""V3 hard-sample set is data only: no model, no database, no HTTP.

The contract exists because the round-27 live run showed two ways a test set
can quietly stop testing what it claims: a human reference leaking into the
blind payload, and a model being credited for correct knowledge the candidate
never stated. Both are checked here before any future live run.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[3] / "data" / "sandbox_test_pkg_20261114" / "2026校招JD_真实面经_模拟回答测试包"
QUESTIONS = PACKAGE / "10_V3_SYNTHETIC_题目与评分要点.jsonl"
SAMPLES = PACKAGE / "11_V3_SYNTHETIC_困难样本.jsonl"
MANIFEST = PACKAGE / "12_V3_manifest.json"
V2_SAMPLES = PACKAGE / "09_V2_评测样本.jsonl"

CATEGORIES = (
    "A_TECHNICALLY_CORRECT_SHORT", "B_CORRECT_WITH_OMISSIONS",
    "C_KEY_MECHANISM_ERROR", "D_SIDE_DETAIL_ERROR",
    "E_LONG_ON_TOPIC_CORE_WRONG", "F_CONCEPT_SWAP_OR_UNSUPPORTED",
)
TOPICS = ("rag_retrieval", "agent", "kafka_reliability", "python", "redis")


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def dataset():
    return dict(questions=_jsonl(QUESTIONS), samples=_jsonl(SAMPLES),
                manifest=json.loads(MANIFEST.read_text(encoding="utf-8")),
                v2=_jsonl(V2_SAMPLES))


def test_files_are_present_and_counts_match_manifest(dataset):
    assert len(dataset["questions"]) == 5
    assert len(dataset["samples"]) == 16
    assert dataset["manifest"]["sample_count"] == len(dataset["samples"])
    assert dataset["manifest"]["question_count"] == len(dataset["questions"])
    ids = [row["sample_id"] for row in dataset["samples"]]
    assert len(ids) == len(set(ids))
    assert all(row["dataset_version"] == "V3_HARD_20261115" for row in dataset["samples"])


def test_every_answer_is_frozen_by_hash_and_length(dataset):
    for row in dataset["samples"]:
        assert row["answer_sha256"] == _sha(row["answer_text"]), row["sample_id"]
        assert row["char_count"] == len(row["answer_text"]), row["sample_id"]
        assert row["blind_context"]["answer"] == row["answer_text"], row["sample_id"]


def test_v2_history_is_untouched_and_v3_is_independent(dataset):
    assert len(dataset["v2"]) == 18
    v2_text = json.dumps(dataset["v2"], ensure_ascii=False)
    assert "V3_HARD_20261115" not in v2_text
    assert not any(row["sample_id"].startswith("V3-") for row in dataset["v2"])
    assert all("SYNTHETIC_V3" in row["provenance"] for row in dataset["samples"])
    assert dataset["manifest"]["historical_db_warning"]


def test_blind_payload_contains_only_question_rubric_and_answer(dataset):
    for row in dataset["samples"]:
        blind = row["blind_context"]
        assert set(blind) == {"question", "rubric", "answer"}, row["sample_id"]
        assert blind["question"] == row["original_question"], row["sample_id"]
        assert blind["rubric"] == row["rubric"], row["sample_id"]
        rendered = json.dumps(blind, ensure_ascii=False)
        for forbidden in row["forbidden_in_model_context"]:
            assert f'"{forbidden}"' not in rendered, (row["sample_id"], forbidden)
        assert row["human_reference_must_not_enter_model_context"] is True
        # Literal labels and bands must not be discoverable in the blind payload.
        for token in (row["sample_id"], row["quality_label"], row["difficulty_category"]):
            assert token not in rendered, (row["sample_id"], token)
        for point in row["candidate_did_not_say"]:
            assert point not in blind["answer"], (row["sample_id"], point)


def test_candidate_said_vs_inferred_knowledge_is_explicit(dataset):
    for row in dataset["samples"]:
        assert row["candidate_did_not_say"], row["sample_id"]
        assert row["correct_points"] or row["errors"], row["sample_id"]
        assert isinstance(row["stated_points"], list) and row["stated_points"]
        assert isinstance(row["missing_points"], list)
        assert row["band_uncertainty"], row["sample_id"]
    assert "不得把后者计入" in dataset["manifest"]["strict_distinction_rule"]


def test_difficulty_and_topic_coverage(dataset):
    cats = Counter(row["difficulty_category"] for row in dataset["samples"])
    assert set(cats) == set(CATEGORIES)
    assert all(count >= 2 for count in cats.values()), cats
    topics = {row["topic"] for row in dataset["samples"]}
    assert topics == set(TOPICS), topics
    by_id = {row["question_id"]: row for row in dataset["questions"]}
    for row in dataset["samples"]:
        assert by_id[row["question_id"]]["topic"] == row["topic"]


def test_bands_are_directional_priors_not_ground_truth(dataset):
    for row in dataset["samples"]:
        lo, hi = row["expected_band"]
        assert isinstance(lo, int) and isinstance(hi, int)
        assert 5 <= lo <= hi <= 25, row["sample_id"]
    assert "不是真值" in dataset["manifest"]["band_policy"]
    assert "不用于调 Prompt" in dataset["manifest"]["band_policy"]
    assert "不为提高预期带命中率而调整评分" in dataset["manifest"]["no_scoring_changes"]
    assert "不修改五维字段" in dataset["manifest"]["no_scoring_changes"]


def test_pairs_are_well_formed_and_reverse_length_is_real(dataset):
    rows = {row["sample_id"]: row for row in dataset["samples"]}
    pairs = dataset["manifest"]["pairs"]
    assert len(pairs) == 8
    used = [sid for pair in pairs for sid in pair["samples"]]
    assert len(used) == len(set(used)) == len(rows)
    for pair in pairs:
        assert len(pair["samples"]) == 2
        first, second = (rows[sid] for sid in pair["samples"])
        if pair["comparability"] == "SAME_TOPIC_STRICT":
            assert first["question_id"] == second["question_id"], pair["pair_id"]
        else:
            assert first["question_id"] != second["question_id"], pair["pair_id"]
            assert pair["expected"] == "UNCERTAIN"
    reverse = 0
    for pair in pairs:
        if pair["expected"] == "UNCERTAIN":
            continue
        higher = rows[pair["samples"][0]] if pair["expected"].endswith("HIGHER") else rows[pair["samples"][1]]
        lower = rows[pair["samples"][1]] if pair["expected"].endswith("HIGHER") else rows[pair["samples"][0]]
        if lower["char_count"] >= higher["char_count"] * 1.2:
            reverse += 1
    assert reverse >= 3, reverse


def test_severity_consistency_groups_are_falsifiable(dataset):
    rows = {row["sample_id"]: row for row in dataset["samples"]}
    groups = {group["group"]: group for group in dataset["manifest"]["severity_consistency_groups"]}
    assert set(groups) == {"SIDE_DETAIL", "KEY_MECHANISM", "GRADIENT"}
    for sid in groups["SIDE_DETAIL"]["samples"]:
        assert rows[sid]["severity"] == "SIDE_DETAIL", sid
    for sid in groups["KEY_MECHANISM"]["samples"]:
        assert rows[sid]["severity"] == "KEY_MECHANISM", sid
    for row in dataset["samples"]:
        for error in row["errors"]:
            assert error["severity"] in ("SIDE_DETAIL", "KEY_MECHANISM", "CONCEPT_SWAP", "UNSUPPORTED_CLAIM")
            assert error["why_wrong"] and error["severity_reason"]


def test_new_topics_are_flagged_as_not_yet_runnable(dataset):
    readiness = {row["question_id"]: row for row in dataset["manifest"]["readiness"]}
    assert readiness["Q_RAG_01"]["official_chain_runnable"] is True
    assert readiness["Q_LG_01"]["official_chain_runnable"] is True
    assert readiness["Q_KF_01"]["official_chain_runnable"] is True
    assert readiness["Q_FASTAPI_01"]["official_chain_runnable"] is False
    assert readiness["Q_REDIS_01"]["official_chain_runnable"] is False
    assert readiness["Q_FASTAPI_01"]["reason"]
    assert readiness["Q_REDIS_01"]["reason"]
    by_id = {row["question_id"]: row for row in dataset["questions"]}
    assert by_id["Q_FASTAPI_01"]["version_sensitive"] is True
    assert any("版本" in fact for fact in by_id["Q_FASTAPI_01"]["reference_facts"])
