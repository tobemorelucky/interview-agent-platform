"""R134: harness reviewed-experience detail is on demand; old projections stay frozen.

Zero model HTTP. The frozen snapshot and the read tool keep the real question
text; only the harness model wire is slimmed down.
"""
import json

from interview_api.modules.interview.adaptive import GAP_SCOPE_CONTRACT
from interview_api.modules.interview.adaptive_prompts import (
    MAIN_HARNESS_CONTEXT_HINTS_V4, MAIN_HARNESS_CONTEXT_HINTS_V5)
from interview_api.modules.interview.context_projection import model_payload
from interview_api.modules.interview.main_harness import VERSION as HARNESS_VERSION
from interview_api.modules.interview.skill_engine import agent_payload
from interview_api.modules.interview.adaptive_prompts import freeze_prompts
from test_v211_director_contract import packet

QUESTION_ID = 987654
QUESTION_TEXT = "面经里报告的检索融合原题，不应在初始输入里全文出现"


def _packet(*, harness_version=None, prompt=None, with_scope=True):
    result = packet()
    policy = result["snapshot"]["adaptive_policy"]
    prompts = freeze_prompts(context_projection=True, evaluation_context=True,
                             gap_scope=True, evaluation_boundaries=True)
    if prompt is not None:
        prompts["interview_main"] = dict(version=prompt.version, system=prompt.system,
                                         fingerprint=prompt.content_hash)
    policy.update(decision_contract="TARGET_SOURCE_PAIR_V1", context_projection="ROLE_CONTEXT_V1",
                  evaluation_context="FOLLOWUP_SCORING_V1", gap_scope_contract=GAP_SCOPE_CONTRACT,
                  evaluation_boundary_contract="FIVE_DIMENSION_BOUNDARIES_V1", prompts=prompts)
    if harness_version:
        policy["main_harness_version"] = harness_version
    kid = result["snapshot"]["adaptive_blueprint"]["targets"][0]["knowledge_id"]
    result["adaptive"]["active_knowledge_id"] = kid
    result["snapshot"]["public_questions"] = [
        dict(id=QUESTION_ID, question=QUESTION_TEXT, dimension="检索",
             expected_points=["融合依据"], fingerprint="x")]
    if with_scope:
        result["snapshot"]["reviewed_experience_scope"] = dict(
            version="REVIEWED_EXPERIENCE_SCOPE_V1",
            items=[dict(experience_ref="experience:1", round="一面",
                        reliability=0.72, topics=["rag_retrieval"],
                        question_refs=[f"public_question:{QUESTION_ID}"])],
            source_refs=[f"public_question:{QUESTION_ID}", "experience:1"])
    return result


def test_harness_v5_does_not_preload_reviewed_question_text():
    p = _packet(harness_version=HARNESS_VERSION, prompt=MAIN_HARNESS_CONTEXT_HINTS_V5)
    out = model_payload(agent_payload(p, "interview_main"), "interview_main")
    rows = out["snapshot"]["public_questions"]
    assert len(rows) == 1
    assert rows[0]["id"] == QUESTION_ID
    assert rows[0]["detail"] == "ON_DEMAND_VIA_TOOL"
    assert rows[0]["tool"] == "search_reviewed_interview_experiences"
    wire = json.dumps(out, ensure_ascii=False)
    assert QUESTION_TEXT not in wire and "融合依据" not in wire
    assert "reviewed_experience_scope" not in wire
    availability = out["snapshot"]["context_availability"]["reviewed_experiences"]
    assert availability == dict(count=1, detail="AVAILABLE_ON_DEMAND",
                                tool="search_reviewed_interview_experiences")
    # The frozen payload, which the tool reads, still holds the real text.
    assert p["snapshot"]["public_questions"][0]["question"] == QUESTION_TEXT


def test_harness_v5_without_scope_has_no_stub_and_reports_empty():
    p = _packet(harness_version=HARNESS_VERSION, prompt=MAIN_HARNESS_CONTEXT_HINTS_V5, with_scope=False)
    out = model_payload(agent_payload(p, "interview_main"), "interview_main")
    assert out["snapshot"]["public_questions"][0]["question"] == QUESTION_TEXT
    assert out["snapshot"]["context_availability"]["reviewed_experiences"]["detail"] == (
        "NO_REVIEWED_EXPERIENCE_FOR_SCOPE")


def test_non_harness_and_frozen_v4_keep_full_preloading():
    plain = model_payload(agent_payload(_packet(), "interview_main"), "interview_main")
    assert plain["snapshot"]["public_questions"][0]["question"] == QUESTION_TEXT
    old = _packet(harness_version=HARNESS_VERSION, prompt=MAIN_HARNESS_CONTEXT_HINTS_V4)
    out = model_payload(agent_payload(old, "interview_main"), "interview_main")
    assert out["snapshot"]["public_questions"][0]["question"] == QUESTION_TEXT
