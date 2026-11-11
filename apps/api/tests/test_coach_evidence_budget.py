"""Bounded Coach evidence: the real 18-round input, 20-round scale, and a hard budget.

The fixture is the exact `input` object the round-4 live batch could not fit into
the 200KB Coach budget.
"""
import json
from pathlib import Path

import pytest

from interview_api.core.exceptions import AppError
from interview_api.modules.interview_report.evidence import MAX_INPUT_BYTES, coach_evidence
from interview_api.modules.interview_report.resolver import resolve_coach_artifact
from interview_api.modules.interview_report.schemas import CoachArtifact

FIXTURE = Path(__file__).parent / "fixtures" / "coach_live_18.json"


@pytest.fixture(scope="module")
def live():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def project(data, max_bytes=MAX_INPUT_BYTES):
    return coach_evidence(snapshot=data["snapshot"], statistics=data["statistics"],
        questions=data["questions"], evaluations=data["evaluations"],
        completion=data["session_completion"], review=data.get("adaptive_review"),
        fingerprint="f" * 64, max_bytes=max_bytes)


def test_eighteen_evaluations_fit_with_headroom(live):
    data = live["input"]
    raw = len(json.dumps(data, ensure_ascii=False).encode())
    payload, report = project(data)
    assert raw > 200_000, "the live input really did exceed the production cap"
    assert report["bytes"] < MAX_INPUT_BYTES
    assert report["bytes"] < raw * 0.5
    assert report["evidence_ids"] == [e["id"] for e in data["evaluations"]]
    assert report["questions"] == len(data["questions"])


def test_every_required_evidence_field_survives(live):
    data = live["input"]
    payload, _ = project(data)
    for projected, original in zip(payload["evaluations"], data["evaluations"]):
        assert projected["id"] == original["id"]
        assert projected["session_question_id"] == original["session_question_id"]
        assert projected["question"] == original["question"]
        assert set(projected["scores"]) == {"technical_correctness", "coverage", "depth",
                                           "relevance", "communication"}
        assert projected["evaluation"] == original["evaluation"]
        assert projected["covered_points_total"] == len(original["covered_points"])
        assert projected["missing_points_total"] == len(original["missing_points"])
        assert len(projected["missing_points"]) == min(4, len(original["missing_points"]))
        assert [p.rstrip("…") for p in projected["missing_points"]] == [
            p[:140] for p in original["missing_points"][:4]]
        assert projected["factual_or_reasoning_issues"] is not None
        # Nothing invented: the kept answer is a prefix of the original text.
        assert original["answer"].startswith(projected["answer"].rstrip("…"))
    assert payload["session_completion"]["completion_reason"] == "TOKEN_LIMIT"
    assert payload["session_completion"]["early_terminated"] is True
    assert payload["evidence_projection"]["omitted"]["evaluation_fields"]


def test_duplicated_material_is_dropped_not_copied(live):
    payload, _ = project(live["input"])
    for entry in payload["evaluations"]:
        assert "reference_answer" not in entry
        assert "answer_framework" not in entry
        assert "issues" not in entry
        assert "provider" not in entry and "model" not in entry
    assert payload["evidence_projection"]["version"] == "COACH_EVIDENCE_V1"


def test_twenty_rounds_stay_bounded(live):
    data = live["input"]
    _, report18 = project(data)
    extended = dict(data)
    extended["evaluations"] = data["evaluations"] + [
        dict(e, id=e["id"] + 100, session_question_id=e["session_question_id"] + 100)
        for e in data["evaluations"][-2:]]
    extended["questions"] = data["questions"] + [
        dict(q, id=q["id"] + 100) for q in data["questions"][-2:]]
    payload20, report20 = project(extended)
    assert report20["evidence_ids"] == [e["id"] for e in extended["evaluations"]]
    assert report20["bytes"] < MAX_INPUT_BYTES
    assert report20["bytes"] / report18["bytes"] < 1.15
    # Two more items cost about two items, not a second copy of the whole session.
    assert report20["bytes"] - report18["bytes"] < 12000
    assert len(payload20["evaluations"]) == 20


def test_projection_failure_is_observable(live):
    with pytest.raises(AppError) as error:
        project(live["input"], max_bytes=2000)
    assert error.value.code == "COACH_EVIDENCE_BUDGET"
    diagnostics = error.value.diagnostics
    assert diagnostics["rule"] == "COACH_EVIDENCE_BUDGET"
    assert diagnostics["bytes"] > diagnostics["max_bytes"] == 2000
    assert diagnostics["section_bytes"]["evaluations"] > 0
    assert diagnostics["omitted"]["evaluations"] == 18


def test_projected_evidence_still_supports_citation_resolution(live):
    payload, _ = project(live["input"])
    artifact = CoachArtifact.model_validate(live["artifact"])
    resolved = resolve_coach_artifact(artifact, payload["evaluations"], payload["statistics"])
    assert len(resolved["strengths"]) == 5
    assert len(resolved["recurring_issues"]) == 3
