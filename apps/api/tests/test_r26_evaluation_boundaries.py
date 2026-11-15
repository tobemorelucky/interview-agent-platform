"""Round 26 P1: five-dimension boundaries, versioned, without touching old sessions.

What changed and what did not:

* The five fields, their 1..5 range and the database schema are unchanged.
* Prompt 2.3.4 adds dimension ownership, a 1..5 descriptor for each dimension,
  severity rules, PARTIAL/NEAR_MISS handling and a quote cap. Sessions created
  before it keep 2.3.3 and are still read with 2.3.3.
* A policy that claims the boundary contract but froze an older prompt (or the
  reverse) is refused, because the stored scores would no longer mean what the
  rubric says.
"""
import pytest

from interview_api.modules.interview import context_projection
from interview_api.modules.interview.adaptive_prompts import (
    EVALUATION_BOUNDARIES_PROMPT, EVALUATION_GAP_SCOPED, freeze_prompts,
)
from interview_api.modules.interview.adaptive import AdaptiveEvaluation
from interview_api.core.exceptions import AppError

CONTRACT = context_projection.BOUNDARY_CONTRACT


def test_new_sessions_freeze_the_boundary_prompt_and_its_contract():
    prompts = freeze_prompts(contract="TARGET_SOURCE_PAIR_V1", memory_guided=True, context_projection=True,
                             evaluation_context=True, gap_scope=True, evaluation_boundaries=True)
    evaluation = prompts["answer_evaluation"]
    assert evaluation["version"] == "2.3.4"
    assert evaluation["system"] == EVALUATION_BOUNDARIES_PROMPT.system
    for phrase in ("Omission alone is not a",
                   "Coverage never lowers",
                   "at most 3",
                   "optional for stateless graphs",
                   'no transactional "offset rollback"'):
        assert phrase in evaluation["system"], phrase
    assert "PARTIAL" in evaluation["system"] and "NEAR_MISS" in evaluation["system"]


def test_old_sessions_keep_2_3_3_and_its_text_is_untouched():
    prompts = freeze_prompts(contract="TARGET_SOURCE_PAIR_V1", memory_guided=True, context_projection=True,
                             evaluation_context=True, gap_scope=True)
    assert prompts["answer_evaluation"]["version"] == "2.3.3"
    assert prompts["answer_evaluation"]["system"] == EVALUATION_GAP_SCOPED.system
    assert "Omission alone is not a technical error" not in prompts["answer_evaluation"]["system"]


def test_prompt_resolution_is_decided_by_the_frozen_contract():
    assert context_projection.expected_evaluation_prompt(
        {"evaluation_boundary_contract": CONTRACT}, gap_scope=True, scoring=True) == "2.3.4"
    assert context_projection.expected_evaluation_prompt({}, gap_scope=True, scoring=True) == "2.3.3"
    assert context_projection.expected_evaluation_prompt({}, gap_scope=False, scoring=True) == "2.3.2"
    assert context_projection.expected_evaluation_prompt({}, gap_scope=False, scoring=False) == "2.3.1"


def test_a_mixed_boundary_contract_is_refused_not_upgraded():
    with pytest.raises(AppError) as caught:
        context_projection.expected_evaluation_prompt(
            {"evaluation_boundary_contract": "SOMETHING_ELSE_V2"}, gap_scope=True, scoring=True)
    assert caught.value.code == "EVALUATION_BOUNDARY_UNSUPPORTED"


def test_scoring_schema_stays_five_dimensions_1_to_5():
    fields = AdaptiveEvaluation.model_json_schema()["properties"]
    for name in ("technical_correctness", "coverage", "depth", "relevance", "communication"):
        assert fields[name]["minimum"] == 1 and fields[name]["maximum"] == 5, name
    assert EVALUATION_BOUNDARIES_PROMPT.version == "2.3.4"
