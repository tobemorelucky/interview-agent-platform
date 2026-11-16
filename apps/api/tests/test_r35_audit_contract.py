"""R32 contract: identity recheck, vocabulary fix and pre-publish audit artefacts.

Reads committed JSON only. No database, no network, no model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[3] / "data" / "r34_target_jd"


@pytest.fixture(scope="module")
def identity():
    return json.loads((DATA / "identity_audit.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def publish_audit():
    return json.loads((DATA / "publish_audit.json").read_text(encoding="utf-8"))


def test_official_literal_title_differs_from_the_manifest_label(identity):
    literals = identity["literals"]
    assert literals["job_title_element"] == "校招-车端 AI Agent 与应用生态系统架构工程师"
    assert literals["job_id_in_body"] == "A25338A"
    assert identity["manifest_expectation"]["job_title"] == "车端AI Agent与应用生态系统架构工程师"


def test_corrected_mapping_passes_the_existing_strict_guards(identity):
    assert identity["corrected_mapping"] == {
        "job_title": "车端 AI Agent 与应用生态系统架构工程师", "expected_identity": "PASS"}
    by_title = {row["candidate"]: row for row in identity["guards"]}
    page_literal = identity["literals"]["job_title_element"]
    # The page literal passes the evidence guard (so the extraction was not wrong)
    assert by_title[page_literal]["evidence_identity_ok"] is True
    # ... but fails the expectation guard while the manifest still holds the old label
    assert by_title[page_literal]["expected_identity_ok"] is False
    assert by_title[identity["manifest_expectation"]["job_title"]]["expected_identity_ok"] is True


def test_corrected_label_retry_is_blocked_by_the_frozen_task_manifest(identity):
    retry = identity["corrected_label_retry"]
    assert retry["prepare_ok"] is False
    assert "MANUAL_IMPORT_IDENTITY_CONFLICT" in retry["error"]


def test_failed_task_left_no_residue_and_is_retry_legal(identity):
    task = identity["task_2"]
    assert task["task_status"] == "FAILED"
    assert task["retry_legal"] is True
    assert task["snapshot_hash_ok"] is True
    assert task["residual"]["nio_entities"] == 0
    assert task["residual"]["jd_versions"] == 2


def test_audit_covers_both_imported_jds_and_flags_the_failed_one(publish_audit):
    assert len(publish_audit["jds"]) == 2
    assert [row["company"] for row in publish_audit["not_imported"]] == ["蔚来"]
    assert publish_audit["audit_logs"] == 0, "nothing was published in this round"
    for entry in publish_audit["jds"].values():
        assert entry["publication_status"] == "WAITING_REVIEW"
        assert entry["raw_text_equals_snapshot"] is True
        assert entry["snapshot_sha256_ok"] is True
        assert entry["identity_claim_ok"] is True


def test_no_blocking_issues_and_strength_is_reported_both_ways(publish_audit):
    for entry in publish_audit["jds"].values():
        assert entry["blocking"] == []
        assert entry["requirement_total"] > 0
    kirin = next(e for k, e in publish_audit["jds"].items() if k.startswith("麒麟"))
    assert kirin["stored_strength_counts"] == {"REQUIRED": 5, "PREFERRED": 5, "OTHER": 0}
    assert kirin["effective_strength_counts"] == {"REQUIRED": 9, "PREFERRED": 1}
    assert sum(1 for p in kirin["advisory"] if p.startswith("STRENGTH_OVER_GENERALISED")) == 4


def test_moonton_keeps_its_lowercase_source_values_and_ambiguous_education(publish_audit):
    moonton = next(e for k, e in publish_audit["jds"].items() if k.startswith("沐瞳"))
    assert moonton["stored_strength_raw"] == ["preferred", "required"]
    assert any(p.startswith("REQUIREMENT_TYPE_AMBIGUOUS") for p in moonton["advisory"])
    assert moonton["hard_eligibility_count"] >= 1


def test_skill_preview_is_available_before_publishing(publish_audit):
    for entry in publish_audit["jds"].values():
        preview = entry["skill_preview_before_publish"]
        assert preview["status"] == "RECOMMENDED"
        assert preview["recommended_skill_id"] == "ai_agent_application"
        assert preview["reason"]
        # After publish the same rule answers from the public read path.
        assert entry["skill_after_publish"] is None
