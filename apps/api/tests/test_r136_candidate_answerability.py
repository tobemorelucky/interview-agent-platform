"""R136 P0: a retrieved reviewed question must be answerable at the decision point.

Zero model HTTP. The root cause in R135 was presentation separation: the reviewed
question's wording lived only in tool_feedback while the candidate the model must
select (candidate_id) stayed in adaptive.candidates with no text. The model then
fell back to the generic Skill candidate whose shape is fully described. The fix
binds the *already returned* reviewed wording onto the matching legal candidate
in the model wire, so a candidate_id can be selected with its evidence visible.

The function is pure and defensive:
  - only successful REVIEWED_EXPERIENCES feedback is used;
  - only candidates already present in the wire are enriched (by exact ref);
  - no candidate/source/target is added, removed or renamed;
  - a missing legal candidate or a stale ref is simply left untouched;
  - non-reviewed candidates and the frozen payload are never modified.
"""
from copy import deepcopy

from interview_api.modules.interview.main_harness import (
    bind_reviewed_questions_to_candidates)


def _feedback(*, code="REVIEWED_EXPERIENCES", status="OK", questions=None):
    return dict(tool="search_reviewed_interview_experiences", arguments={"topics": ["agent"]},
        result=dict(status=status, code=code, trust="UNTRUSTED_DATA",
            data=dict(items=[dict(experience_ref="experience:1", source_url="https://example.com/1",
                reviewed_at="2026-11-18", reliability=0.72, topics=["agent"],
                questions=questions or [])])))


def _wire():
    return {
        "adaptive": {"candidates": [
            {"candidate_id": "C_PQ2", "source": {"ref": "public_question:2", "source_type": "PUBLIC_QUESTION"},
             "strategies": ["NEXT"], "question_shapes": ["FUNDAMENTALS"]},
            {"candidate_id": "C_SKILL", "source": {"ref": "skill:ai_agent_application@1.0.1",
             "source_type": "DYNAMIC_GENERATED"}, "strategies": ["NEXT"],
             "question_shapes": ["FUNDAMENTALS", "SCENARIO"]},
        ]},
        "snapshot": {"public_questions": [
            {"id": 2, "question": "ON_DEMAND stub", "dimension": "Agent", "detail": "ON_DEMAND_VIA_TOOL"},
        ]},
    }


def test_reviewed_wording_bound_onto_matching_legal_candidate():
    wire = _wire()
    fb = [_feedback(questions=[dict(ref="public_question:2", candidate_id="C_PQ2",
        text="agent 修复效果如何评判？", dimension="Agent 评测")])]
    original = deepcopy(wire)
    out = bind_reviewed_questions_to_candidates(wire, fb)
    pq = out["adaptive"]["candidates"][0]
    assert pq["reported_question"] == "agent 修复效果如何评判？"
    assert pq["reported_source_ref"] == "experience:1"
    assert pq["reported_source_url"] == "https://example.com/1"
    assert pq["review_evidence"] == dict(reviewed_at="2026-11-18", reliability=0.72)
    # identity of the candidate is unchanged
    assert pq["candidate_id"] == "C_PQ2" and pq["source"]["ref"] == "public_question:2"
    # the generic skill candidate is untouched
    assert "reported_question" not in out["adaptive"]["candidates"][1]
    # the input wire was not mutated (pure)
    assert wire == original


def test_no_binding_for_error_or_empty_feedback():
    wire = _wire()
    for fb in ([_feedback(status="ERROR", code="TOPIC_OUT_OF_SCOPE")],
               [_feedback(code="ACTIVE_GAPS")], []):
        out = bind_reviewed_questions_to_candidates(wire, fb)
        assert "reported_question" not in out["adaptive"]["candidates"][0]


def test_no_binding_when_candidate_id_mismatch_or_ref_stale():
    wire = _wire()
    fb = [_feedback(questions=[
        dict(ref="public_question:2", candidate_id="C_STALE", text="wrong id"),
        dict(ref="public_question:999", candidate_id="C_PQ2", text="stale ref"),
    ])]
    out = bind_reviewed_questions_to_candidates(wire, fb)
    assert "reported_question" not in out["adaptive"]["candidates"][0]


def test_only_existing_candidates_are_enriched_not_added():
    wire = _wire()
    fb = [_feedback(questions=[
        dict(ref="public_question:2", candidate_id="C_PQ2", text="ok"),
        dict(ref="public_question:7", candidate_id="C_PQ7", text="not in this wire"),
    ])]
    out = bind_reviewed_questions_to_candidates(wire, fb)
    assert len(out["adaptive"]["candidates"]) == 2
    assert not any(c["candidate_id"] == "C_PQ7" for c in out["adaptive"]["candidates"])


def test_tool_audit_four_way_reviewed_usage():
    """Retrieved / selected-as-source / background-only / preloaded stay distinct."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch
    from interview_api.modules.interview import main_harness as mh

    def art(ref):
        return SimpleNamespace(question_source_ref=ref)

    def steps(tool_results):
        return [SimpleNamespace(step_name=mh.TOOL_STEP, output_json=r) for r in tool_results]

    def searched():
        return dict(tool="search_reviewed_interview_experiences",
                    result=dict(status="OK", code="REVIEWED_EXPERIENCES",
                                source_refs=["public_question:2", "experience:1"]))

    async def check(ref, tool_results):
        obj = mh.MainHarness.__new__(mh.MainHarness)
        with patch.object(mh.MainHarness, "_steps", return_value=steps(tool_results)):
            return await obj._tool_audit("run", art(ref))

    a = asyncio.run(check("public_question:2", [searched()]))
    assert a["used_tool_result"] is True
    assert a["reviewed_usage"] == "REVIEWED_SELECTED_AS_SOURCE"
    assert a["reviewed_retrieved_count"] == 1  # experience:1 not a question ref

    b = asyncio.run(check("skill:ai_agent_application@1.0.1", [searched()]))
    assert b["used_tool_result"] is False
    assert b["reviewed_usage"] == "NO_REVIEWED_QUESTION"

    c = asyncio.run(check("public_question:9", [searched()]))
    assert c["used_tool_result"] is False
    assert c["reviewed_usage"] == "REVIEWED_BACKGROUND_ONLY"

    d = asyncio.run(check("public_question:9", []))
    assert d["reviewed_usage"] == "PRELOADED_WITHOUT_TOOL"
