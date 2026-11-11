"""The Main selection repair may only normalise a flattened draft it already contains.

Uses the real hosted-model output pair captured in fixtures/ (a malformed answer
and the repair the model produced for it) to prove the offline reproduction.
"""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from interview_api.modules.interview.adaptive import ScopedAdaptiveSelection
from interview_api.modules.interview.director_repair import (FlattenedQuestionRepair,
    MainSelectionRepair, SupportingRefsRepair)

FIXTURE = Path(__file__).parent / "fixtures" / "flattened_main_selection.json"
PAIR_PAYLOAD = {"snapshot": {"adaptive_policy": {"decision_contract": "TARGET_SOURCE_PAIR_V1"}}}


def load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def schema_error(value):
    with pytest.raises(ValidationError) as error:
        ScopedAdaptiveSelection.model_validate(value)
    return error.value


def test_real_flattened_output_is_authorised_and_the_real_repair_is_accepted():
    case = load()
    policy = FlattenedQuestionRepair(PAIR_PAYLOAD)
    plan = policy.prepare(case["original"], schema_error(case["original"]))
    assert plan["policy"] == "FLATTENED_QUESTION_DRAFT_V1"
    assert plan["draft"]["question"] == case["original"]["proposed_question"]
    assert plan["draft"]["expected_points"] == case["original"]["proposed_question_meta"]["expected_points"]
    result = policy.validate(case["original"], case["repaired"])
    assert result["fields"] == ["difficulty", "dimension", "expected_points", "question"]
    ScopedAdaptiveSelection.model_validate(case["repaired"])


def test_sibling_key_variant_is_also_normalised():
    case = load()
    meta = case["original"]["proposed_question_meta"]
    original = {k: v for k, v in case["original"].items() if k != "proposed_question_meta"}
    original.update(proposed_question_dimension=meta["dimension"],
        proposed_question_difficulty=meta["difficulty"],
        proposed_question_expected_points=meta["expected_points"])
    policy = FlattenedQuestionRepair(PAIR_PAYLOAD)
    assert policy.prepare(original, schema_error(original)) is not None
    assert policy.validate(original, case["repaired"])["policy"] == "FLATTENED_QUESTION_DRAFT_V1"


def test_repair_may_not_rewrite_the_question():
    case = load()
    repaired = json.loads(json.dumps(case["repaired"]))
    repaired["proposed_question"]["dimension"] = "改写后的维度"
    with pytest.raises(ValueError, match="invented or altered question content"):
        FlattenedQuestionRepair(PAIR_PAYLOAD).validate(case["original"], repaired)


def test_repair_may_not_change_selected_pair_or_references():
    case = load()
    for field, value in (("candidate_id", "C_other"), ("supporting_refs", ["skill:other"]),
                         ("focus_gap_ids", [])):
        repaired = json.loads(json.dumps(case["repaired"]))
        repaired[field] = value
        with pytest.raises(ValueError, match="changed another field"):
            FlattenedQuestionRepair(PAIR_PAYLOAD).validate(case["original"], repaired)


def test_unrelated_schema_error_is_not_authorised():
    case = load()
    broken = {k: v for k, v in case["original"].items() if k != "candidate_id"}
    with pytest.raises(ValidationError) as error:
        ScopedAdaptiveSelection.model_validate(broken)
    assert FlattenedQuestionRepair(PAIR_PAYLOAD).prepare(broken, error.value) is None
    assert MainSelectionRepair(PAIR_PAYLOAD).prepare(broken, error.value) is None


def test_flat_draft_without_expected_points_is_not_authorised():
    case = load()
    original = {k: v for k, v in case["original"].items() if k != "proposed_question_meta"}
    original["proposed_question_dimension"] = "维度"
    original["proposed_question_difficulty"] = "MEDIUM"
    assert FlattenedQuestionRepair(PAIR_PAYLOAD).prepare(original, schema_error(original)) is None


def test_composite_keeps_the_reference_subset_repair_working():
    refs = ["skill:ai_agent_application@1.0.0"] + [f"answer:{n}" for n in range(1, 13)]
    payload = {**PAIR_PAYLOAD, "answer": "回答原文", "adaptive": {
        "allowed_refs": refs, "current_answer_ref": "answer:1",
        "candidates": [dict(candidate_id="C_1", strategies=["NEXT"], source=dict(ref=refs[0]))]}}
    original = dict(candidate_id="C_1", strategy="NEXT", supporting_refs=refs,
        reason_tags=["OPENING"], focus_gap_ids=[],
        proposed_question=dict(question="问题？", expected_points=["点"], dimension="维度", difficulty="MEDIUM"))
    with pytest.raises(ValidationError) as error:
        ScopedAdaptiveSelection.model_validate(original)
    policy = MainSelectionRepair(payload)
    plan = policy.prepare(original, error.value)
    assert plan["policy"] == "DIRECTOR_SUPPORT_SUBSET_V1"
    repaired = {**original, "supporting_refs": [refs[0], "answer:1"]}
    assert policy.validate(original, repaired)["retained_count"] == 2
    with pytest.raises(ValueError):
        SupportingRefsRepair(payload).validate(original, {**original, "supporting_refs": ["answer:2"]})
