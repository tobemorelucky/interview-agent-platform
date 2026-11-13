"""Phase 0: the Coach receives structured memory, not a clipped Python repr.

The defect this file pins down: `approved_memory_context` and
`recent_performance_observations` were passed through `_clip()`, which stringifies
a dict and cuts it at 600 characters, and `memory_guided_plan` was not projected
at all — it was recorded as "omitted" and thrown away. A model cannot reason over
`{'semantics': 'USER_CONFIRMED...', 'items': [{'id': 1, 'con...`.

These tests use the real 18-round live fixture for the size/compatibility
guarantees and a synthetic snapshot for the shapes the fixture happens to have
empty (memory items, capability plan, training scope).
"""
import json
from pathlib import Path

import pytest

from interview_api.modules.interview_report.evidence import (
    MAX_INPUT_BYTES, VERSION, coach_evidence, evaluation_fingerprint,
)

FIXTURE = Path(__file__).parent / "fixtures" / "coach_live_18.json"


@pytest.fixture(scope="module")
def live():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def snapshot_with_memory():
    """Shapes copied from the real producers (`context.build_context`,
    `capability_rules.memory_plan`), not invented for the test."""
    return {
        "company_name": "某互联网公司", "job_title": "后端工程师",
        "jd_version_number": 3, "profile_version_number": 5,
        "profile": {"skills": [{"label": "Python"}]},
        "requirements": [{"statement": "熟悉 Kafka 消息可靠性"}],
        "interviewer_config": {"skill": {"skill_id": "java_backend", "version": "2.3.0", "name": "Java 后端"}},
        "budgets": {"question_count": 8, "max_model_tokens": 200000},
        "approved_memory_context": {
            "semantics": "Explicitly user-confirmed Career Memory; not resume evidence.",
            "epistemic_status": "USER_CONFIRMED_MEMORY_NOT_OBJECTIVE_TRUTH",
            "is_stable_fact": False,
            "items": [{"id": 11, "content": "希望做后端基础设施方向", "memory_type": "GOAL"},
                      {"id": 12, "content": "更偏好小团队" * 40, "memory_type": "PREFERENCE"}],
            "selected_count": 2, "omitted_count": 0},
        "recent_performance_observations": {
            "semantics": "Limited historical interview-answer performance observations.",
            "epistemic_status": "INTERVIEW_PERFORMANCE_OBSERVATION_NOT_USER_CONFIRMED",
            "is_stable_fact": False, "items": [], "selected_count": 0, "omitted_count": 0},
        "memory_guided_plan": {
            "version": "2.3.0", "inferred": True, "frozen_at": "2026-11-13T02:00:00+00:00",
            "fingerprint": "a" * 64,
            "items": [{
                "knowledge_id": "java_backend:kafka_reliability", "label": "Kafka 消息可靠性",
                "state": "CONFLICTED", "intent": "INDEPENDENT_VERIFICATION", "priority": 8,
                "needs_recheck": True, "memory_revision": 4, "reason_tags": ["CONFLICTING_OBSERVATIONS"],
                "observation_refs": ["evaluation:31", "evaluation:44", "evaluation:58", "evaluation:60"],
                "history_summary": "历史待核对：ACK 与幂等边界不清；" + "旧结论" * 200}],
            "deprioritized_targets": [{"knowledge_id": "java_backend:redis", "memory_revision": 2,
                                       "reason": "RECENT_INDEPENDENT_VERIFICATION"}]},
        "training": {"version": "2.3.0", "mode": "RECHECK",
                     "selected_knowledge_id": "java_backend:kafka_reliability",
                     "before": [{"knowledge_id": "java_backend:kafka_reliability",
                                 "label": "Kafka 消息可靠性", "state": "CONFLICTED", "revision": 4}]},
    }


def evaluations():
    return [dict(id=31, evidence_ref="evaluation:31", session_question_id=301,
                 question="Kafka 如何保证不丢消息？", answer="A" * 900, evaluation="E" * 900,
                 scores=dict(technical_correctness=3, coverage=3, depth=3, relevance=4, communication=4),
                 covered_points=["ACK 机制"], missing_points=["幂等", "事务边界"],
                 factual_or_reasoning_issues=[], follow_up_focus=[])]


def project(snapshot=None, rows=None, max_bytes=MAX_INPUT_BYTES):
    return coach_evidence(snapshot=snapshot or snapshot_with_memory(),
                          statistics={"evaluated_count": 1, "total_questions": 1},
                          questions=[dict(id=301, question="Q", status="COMPLETED", source="SKILL")],
                          evaluations=rows if rows is not None else evaluations(),
                          fingerprint="f" * 64, max_bytes=max_bytes)


def test_memory_is_structured_not_a_stringified_dict():
    payload, _ = project()
    snapshot = payload["snapshot"]
    for field, section in (("approved_memory_context", "USER_CONFIRMED_CONTEXT"),
                           ("recent_performance_observations", "PRIOR_PRACTICE_OBSERVATIONS")):
        layer = snapshot[field]
        assert isinstance(layer, dict), field
        assert isinstance(layer["items"], list), field
        assert layer["section"] == section
        assert layer["semantics"], "a layer must keep the meaning its producer declared"
    approved = snapshot["approved_memory_context"]
    assert [item["id"] for item in approved["items"]] == [11, 12]
    assert approved["items"][0]["content"] == "希望做后端基础设施方向"
    assert approved["items"][0]["memory_type"] == "GOAL"
    # Over-long text is clipped, counted, and never silently dropped.
    assert approved["items"][1]["content"].endswith("…")
    assert approved["clipped"] >= 1
    # The Python-repr failure mode must be impossible anywhere in the payload.
    assert "{'semantics'" not in json.dumps(payload, ensure_ascii=False)


def test_capability_plan_is_projected_with_state_priority_and_refs():
    payload, report = project()
    capability = payload["snapshot"]["capability_context"]
    assert capability["fingerprint"] == "a" * 64
    assert capability["frozen_at"].startswith("2026-11-13")
    item = capability["items"][0]
    assert item["knowledge_id"] == "java_backend:kafka_reliability"
    assert item["state"] == "CONFLICTED"
    assert item["priority"] == 8 and item["needs_recheck"] is True
    assert item["memory_revision"] == 4
    # Four refs were frozen, three are shown, and the truncation is counted.
    assert item["observation_refs"] == ["evaluation:31", "evaluation:44", "evaluation:58"]
    assert item["observation_refs_total"] == 4
    assert item["history_clipped"] is True
    assert capability["deprioritized_targets"][0]["reason"] == "RECENT_INDEPENDENT_VERIFICATION"
    # It is projected now, so it must not still be listed as dropped material.
    assert "memory_guided_plan" not in report["omitted"]["snapshot_fields"]
    assert "training" not in report["omitted"]["snapshot_fields"]


def test_capability_scope_says_it_is_not_job_specific():
    payload, _ = project()
    capability = payload["snapshot"]["capability_context"]
    # Requirement: a cross-job aggregate must not read as this job's mastery.
    assert "not evidence of mastery or weakness on this particular job" in capability["scope_note"]
    assert "across all interviews" in capability["scope_note"]
    assert capability["semantics"].startswith("RULE_GENERATED_TRAINING_HINT_FROZEN_AT_SESSION_START")


def test_training_context_is_separate_and_labelled():
    payload, _ = project()
    training = payload["snapshot"]["training_context"]
    assert training["mode"] == "RECHECK"
    assert training["selected_knowledge_id"] == "java_backend:kafka_reliability"
    assert training["targets"][0]["state"] == "CONFLICTED"
    assert training["semantics"].startswith("THIS_SESSION_FROZEN_TRAINING_SCOPE")
    # A non-training session carries no training block at all.
    plain = snapshot_with_memory()
    plain.pop("training")
    payload_plain, _ = project(snapshot=plain)
    assert payload_plain["snapshot"]["training_context"] is None


def test_each_evaluation_carries_a_stable_fingerprint():
    rows = evaluations()
    payload, _ = project(rows=rows)
    projected = payload["evaluations"][0]
    assert projected["fingerprint"] == evaluation_fingerprint(rows[0])
    # Clipping does not change the fingerprint: it is computed before clipping.
    changed = [dict(rows[0], answer="B" * 900)]
    payload_changed, _ = project(rows=changed)
    assert payload_changed["evaluations"][0]["fingerprint"] != projected["fingerprint"]


def test_projection_stays_bounded_and_reports_its_sections():
    payload, report = project()
    assert report["version"] == VERSION
    assert report["bytes"] < MAX_INPUT_BYTES
    # Every new section is visible in the size report, so growth is attributable.
    for section in ("snapshot", "evaluations", "questions", "statistics"):
        assert report["section_bytes"][section] > 0
    # Ten memory items would be the cap; only two exist and the total is kept.
    assert payload["snapshot"]["approved_memory_context"]["total"] == 2


def test_memory_growth_is_capped_not_unbounded():
    snapshot = snapshot_with_memory()
    snapshot["approved_memory_context"]["items"] = [
        {"id": i, "content": f"第 {i} 条用户确认的偏好", "memory_type": "PREFERENCE"} for i in range(40)]
    payload, report = project(snapshot=snapshot)
    layer = payload["snapshot"]["approved_memory_context"]
    assert layer["shown"] == 8 and layer["total"] == 40
    assert report["omitted"]["approved_memory_items"] == 32
    # The cap is what keeps the payload from growing with the history.
    _, baseline = project()
    assert report["bytes"] - baseline["bytes"] < 800


def test_real_eighteen_round_fixture_still_fits_and_still_resolves(live):
    payload, report = project(snapshot=live["input"]["snapshot"], rows=live["input"]["evaluations"])
    assert report["bytes"] < MAX_INPUT_BYTES
    assert report["bytes"] < len(json.dumps(live["input"], ensure_ascii=False).encode()) * 0.5
    assert [e["id"] for e in payload["evaluations"]] == [e["id"] for e in live["input"]["evaluations"]]
    # The live snapshot has an empty plan and empty memory: projected as empty,
    # not as "missing", so the model can tell the difference.
    assert payload["snapshot"]["capability_context"]["items"] == []
    assert payload["snapshot"]["approved_memory_context"]["items"] == []
    from interview_api.modules.interview_report.schemas import CoachArtifact
    from interview_api.modules.interview_report.resolver import resolve_coach_artifact
    artifact = CoachArtifact.model_validate(live["artifact"])
    resolved = resolve_coach_artifact(artifact, payload["evaluations"], payload["statistics"])
    assert len(resolved["strengths"]) == 5


def test_oversized_projection_fails_with_section_diagnostics():
    from interview_api.core.exceptions import AppError
    with pytest.raises(AppError) as error:
        project(max_bytes=1500)
    assert error.value.code == "COACH_EVIDENCE_BUDGET"
    assert error.value.diagnostics["section_bytes"]["evaluations"] > 0
