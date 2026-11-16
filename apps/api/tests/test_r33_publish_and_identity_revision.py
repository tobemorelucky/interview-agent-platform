"""Round 33 contracts: sandbox publication and the NIO identity revision.

Both artefacts are produced by scripts under zero model calls; these tests assert
the invariants an operator relies on, and they fail on a bad artefact rather than
describing it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PUBLISH = REPO / "data/r38_publish/publish_report.json"
REVISION = REPO / "data/r39_nio_revision/replay_report.json"
PRECHECK = REPO / "data/r37_precheck_49/precheck_49.json"


@pytest.fixture(scope="module")
def publish() -> dict:
    return json.loads(PUBLISH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def revision() -> dict:
    return json.loads(REVISION.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def precheck() -> dict:
    return json.loads(PRECHECK.read_text(encoding="utf-8"))


# ── publication ─────────────────────────────────────────────────────────────

def test_publication_happened_in_the_sandbox_only(publish):
    assert publish["sandbox"] == "interview_agent_wb_jd_r31_20261116"
    assert publish["model_http_calls"] == 0
    assert publish["blocking"] == {}
    for entry in publish["published"].values():
        assert entry["publication_status"] == "PUBLISHED"


def test_publication_was_idempotent(publish):
    for entry in publish["published"].values():
        assert entry["repeat_call"]["reused"] is True
        assert entry["repeat_call"]["jd_version_id"] == entry["jd_version_id"]
    checks = publish["post_checks"]
    assert checks["versions_total"] == checks["published_total"] == 2


def test_nothing_was_selectable_before_the_admin_record(publish):
    # The audit row keeps the pre-publication state, so this stays checkable later.
    for row in publish["post_checks"]["audit_logs"]:
        assert row["before_json"]["publication_status"] == "WAITING_REVIEW"
        assert row["after_json"]["publication_status"] == "PUBLISHED"
    assert publish["negative_control"]["selectable_jobs_before_first_publish"] == 0
    assert publish["post_checks"]["selectable_jobs_now"] == 2


def test_audit_log_names_the_admin_and_the_action(publish):
    logs = publish["post_checks"]["audit_logs"]
    assert len(logs) == 2
    for row in logs:
        assert row["action"] == "public_career.jd.publish"
        assert row["actor_role"] == "ADMIN"
        assert row["status"] == "SUCCESS"
        assert row["resource_type"] == "JobDescriptionVersion"


def test_targets_and_public_projection_return_both_jobs(publish):
    targets = publish["post_checks"]["targets"]
    assert targets["status"] == 200
    assert len(targets["jobs"]) == 2
    assert {job["skill_status"] for job in targets["jobs"]} == {"RECOMMENDED"}
    assert publish["post_checks"]["public_jobs"]["status"] == 200


def test_no_model_call_was_made_by_publication(publish):
    counts = publish["post_checks"]["counts"]
    assert counts["admissions"] == 6, "round 31 ledger unchanged"


# ── requirement strength and category at the consumption boundary ───────────

def test_kylinos_mixed_sentences_are_required_downstream(publish):
    kylinos = next(a for a in publish["pre_publish"] if a["jd_id"] == 1)
    assert kylinos["counts"]["stored_strength"] == {"REQUIRED": 5, "PREFERRED": 5}
    assert kylinos["counts"]["effective_strength"] == {"REQUIRED": 9, "PREFERRED": 1}
    assert len(kylinos["mixed_clauses"]) == 4
    for entry in kylinos["mixed_clauses"]:
        mandatory = [c for c in entry["clauses"] if c["strength"] == "REQUIRED"]
        preferred = [c for c in entry["clauses"] if c["strength"] == "PREFERRED"]
        assert mandatory and preferred, "a mixed sentence must keep both sides"
        assert all("优先" in c["text"] for c in preferred)


def test_mixed_sentence_never_becomes_wholly_preferred(publish):
    for audit in publish["pre_publish"]:
        for entry in audit["mixed_clauses"]:
            assert entry["effective"] == "REQUIRED"


def test_education_is_not_counted_as_a_technical_gap(publish):
    moon = next(a for a in publish["pre_publish"] if a["jd_id"] == 2)
    assert moon["type_status"]["ambiguous"] == [11]
    rows = {row["id"]: row for row in moon["requirements"]}
    assert rows[11]["is_hard_eligibility"] is True
    statuses = {row["id"]: row["status"] for row in moon["matching_preview"]["rows"]}
    assert statuses[11] == "NOT_APPLICABLE"


def test_interview_context_projection_uses_effective_strength(publish):
    projection = publish["post_checks"]["context_projection"]
    assert projection["1"]["strength"] == {"REQUIRED": 9, "PREFERRED": 1}
    assert projection["2"]["strength"] == {"REQUIRED": 8, "PREFERRED": 4}
    assert projection["1"]["fingerprints_unique"] is True


def test_whitespace_only_statement_variant_is_not_blocking(publish):
    moon = next(a for a in publish["pre_publish"] if a["jd_id"] == 2)
    assert any("STATEMENT_WHITESPACE_VARIANT" in item for item in moon["advisory"])
    assert not any("STATEMENT_NOT_IN_BODY" in item for item in moon["problems"])


# ── NIO identity revision ──────────────────────────────────────────────────

def test_revision_reproduces_then_fixes_the_identity_failure(revision):
    labels = revision["labels"]
    assert labels["display_frozen_in_round31"] == "车端AI Agent与应用生态系统架构工程师"
    assert labels["page_literal"] == "校招-车端 AI Agent 与应用生态系统架构工程师"
    assert revision["cases"]["original_failure"]["state"]["status"] == "FAILED"
    assert "IDENTITY_MISMATCH" in revision["cases"]["original_failure"]["state"]["error"]
    assert revision["cases"]["revision_run"]["state"]["status"] == "COMPLETED"


def test_revision_keeps_the_predecessor_frozen(revision):
    original = revision["cases"]["original_task_untouched"]
    assert original["status"] == "FAILED"
    assert original["job_title"] == "车端AI Agent与应用生态系统架构工程师"
    assert original["revision"] is None


def test_revision_is_traceable_and_does_not_change_the_source(revision):
    manifest = revision["cases"]["revision_run"]["state"]["revision"]
    assert manifest["of_task_id"] == revision["cases"]["original_failure"]["task"]
    assert manifest["same_canonical_url"] is True
    assert manifest["same_content_sha256"] is True
    assert manifest["label_occurs_in_snapshot"] is True
    assert manifest["authorized_by"].startswith("user:")


def test_revision_is_idempotent(revision):
    assert revision["cases"]["revision_idempotent"] is True
    first = revision["cases"]["revision_created"]["refresh_task_id"]
    second = revision["cases"]["revision_resubmitted"]["refresh_task_id"]
    assert first == second


@pytest.mark.parametrize("case,code", [
    ("wrong_company", "MANUAL_REVISION_COMPANY_CHANGED"),
    ("other_posting_title", "MANUAL_REVISION_LABEL_NOT_IN_SNAPSHOT"),
    ("over_short_label", "MANUAL_REVISION_LABEL_UNRELATED"),
    ("unchanged_label", "MANUAL_REVISION_LABEL_UNCHANGED"),
    ("predecessor_not_failed", "MANUAL_REVISION_PREDECESSOR_NOT_FAILED"),
])
def test_revision_refuses_illegal_labels(revision, case, code):
    outcome = revision["cases"][case]
    assert outcome["refused"] is True
    assert outcome["error"] == code


def test_revision_made_no_model_call(revision):
    assert revision["model_http_calls"] == 0
    assert revision["counts_before"]["tasks"] == 0


# ── remaining 49 precheck ──────────────────────────────────────────────────

def test_precheck_covers_the_remaining_corpus(precheck):
    assert precheck["scope"]["checked"] == 49
    assert precheck["scope"]["corpus_records"] == 52
    assert sum(precheck["verdicts"].values()) == 49
    assert precheck["model_http_calls"] == 0


def test_no_record_passes_with_a_stale_label_or_unproven_identity(precheck):
    for row in precheck["records"]:
        if row["verdict"] == "PASS":
            assert "IDENTITY_LABEL_WORDING_DIFFERS" not in row["review"]
            assert not row["blocking"]


def test_every_non_pass_record_states_a_reason(precheck):
    for row in precheck["records"]:
        if row["verdict"] != "PASS":
            assert row["blocking"] or row["review"], row["jd_id"]


def test_labels_are_never_claimed_literal_when_they_are_display_names(precheck):
    identity = precheck["identity"]
    total = (identity["label_has_channel_prefix"] + identity["label_spacing_only"]
             + identity["label_wording_differs"] + identity["no_dom_snapshot"]
             + identity["label_already_literal"])
    assert identity["label_already_literal"] == 0
    assert total == 49
