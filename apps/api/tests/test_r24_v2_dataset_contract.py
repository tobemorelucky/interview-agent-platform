"""Contract for the V2 evaluation sample set (offline, no database, no model).

The set exists to separate *quality tiers* from *answer length*. These assertions
fail if a later edit reintroduces the round-24 defect: answers that are only
distinguishable because the bad one is shorter.
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SAMPLES = ROOT / "data" / "sandbox_test_pkg_20261114" / "2026校招JD_真实面经_模拟回答测试包" / "09_V2_评测样本.jsonl"
QUESTIONS = ROOT / "data" / "sandbox_test_pkg_20261114" / "2026校招JD_真实面经_模拟回答测试包" / "08_V2_题目与评分要点.jsonl"
TIERS = {"GOOD_FULL", "GOOD_SHORT", "PARTIAL", "NEAR_MISS", "POOR_LONG_WRONG", "POOR_CONFUSED"}
FORBIDDEN = ("quality_label", "expected_band", "expected_relation", "errors", "human_check_points")


def _load(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def samples():
    return _load(SAMPLES)


@pytest.fixture(scope="module")
def questions():
    return _load(QUESTIONS)


def test_three_topics_and_nine_pairs_with_every_tier(samples, questions):
    assert len(questions) == 3
    assert {q["topic"] for q in questions} == {"rag_retrieval", "agent", "kafka_reliability"}
    assert len(samples) == 18
    pairs = defaultdict(list)
    for row in samples:
        pairs[row["pair_id"]].append(row)
    assert len(pairs) == 9
    assert all(len(members) == 2 for members in pairs.values())
    assert {row["quality_label"] for row in samples} == TIERS
    assert len({row["sample_id"] for row in samples}) == 18


def test_every_pair_has_one_higher_and_one_lower_side(samples):
    pairs = defaultdict(list)
    for row in samples:
        pairs[row["pair_id"]].append(row)
    for pair_id, members in pairs.items():
        assert Counter(m["expected_relation"] for m in members) == {"HIGHER": 1, "LOWER": 1}, pair_id
        higher = next(m for m in members if m["expected_relation"] == "HIGHER")
        lower = next(m for m in members if m["expected_relation"] == "LOWER")
        assert higher["expected_band"][0] > lower["expected_band"][1], pair_id
        assert {m["question_id"] for m in members} == {members[0]["question_id"]}, pair_id


def test_at_least_eight_pairs_control_for_length(samples):
    pairs = defaultdict(list)
    for row in samples:
        pairs[row["pair_id"]].append(row)
    controlled, reverse, detail = 0, 0, {}
    for pair_id, members in sorted(pairs.items()):
        higher = next(m for m in members if m["expected_relation"] == "HIGHER")
        lower = next(m for m in members if m["expected_relation"] == "LOWER")
        ratio = lower["char_count"] / higher["char_count"]
        detail[pair_id] = round(ratio, 2)
        close = abs(ratio - 1) <= 0.20          # near-equal length
        reversed_length = ratio > 1.05          # the worse answer is longer
        controlled += bool(close or reversed_length)
        reverse += bool(reversed_length)
    assert controlled >= 8, detail
    # A set where the good answer is always longer cannot test a length shortcut.
    assert reverse >= 4, detail


def test_char_count_and_hash_match_the_text(samples):
    import hashlib
    for row in samples:
        assert row["char_count"] == len(row["answer_text"]), row["sample_id"]
        assert row["sha256"] == hashlib.sha256(row["answer_text"].encode()).hexdigest(), row["sample_id"]


def test_wrong_answers_carry_an_explicit_correction(samples):
    for row in samples:
        label, errors = row["quality_label"], row["errors"]
        if label.startswith("GOOD"):
            assert errors == [], row["sample_id"]
            assert len(row["human_check_points"]) >= 3, row["sample_id"]
            continue
        assert errors, f"{row['sample_id']} must name at least one concrete error"
        for error in errors:
            assert error["claim"] and error["why_wrong"], row["sample_id"]
            assert error["claim"] != error["why_wrong"]
        if label in ("PARTIAL", "NEAR_MISS"):
            # "Looks reasonable but is partly wrong" must be representable.
            assert len(errors) <= 2, row["sample_id"]


def test_neither_labels_nor_reference_scores_can_reach_the_model(samples, questions):
    rubric = {q["question_id"]: q["rubric"] for q in questions}
    for row in samples:
        blind = dict(row["blind_context"])
        assert set(blind) == {"question_id", "answer_text"}
        assert blind["answer_text"] == row["answer_text"]
        assert blind["question_id"] in rubric
        serialized = json.dumps(blind, ensure_ascii=False)
        for forbidden in FORBIDDEN:
            assert forbidden not in serialized
        assert str(row["expected_band"]) not in serialized


def test_pairs_share_one_question_and_rubric(samples, questions):
    rubric = {q["question_id"]: q["rubric"] for q in questions}
    assert all(len(rubric[q]) >= 4 for q in rubric)
    pairs = defaultdict(set)
    for row in samples:
        pairs[row["pair_id"]].add(row["question_id"])
    assert all(len(ids) == 1 for ids in pairs.values())


def test_questions_declare_a_real_skill_and_topic():
    from interview_api.modules.interview.skill_registry import discover, ROOT as SKILL_ROOT
    from interview_api.modules.interview.topics import TOPICS
    entries, errors = discover(SKILL_ROOT)
    assert not errors
    for row in _load(QUESTIONS):
        assert row["skill"] in entries, row["skill"]
        assert row["topic"] in TOPICS, row["topic"]
        # The interviewer skill must actually be able to ask this topic.
        definition = json.loads(
            (SKILL_ROOT / row["skill"] / entries[row["skill"]]["manifest"]["version"] / "skill.json")
            .read_text(encoding="utf-8"))
        assert row["topic"] in definition["priority_topics"], (row["skill"], row["topic"])
