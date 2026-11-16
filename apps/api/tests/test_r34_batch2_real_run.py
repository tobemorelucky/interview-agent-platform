"""Round 34 contracts: the two real-model sub-runs, the audit gate and the probe.

Every artefact is produced under an explicit budget; these tests fail if a budget,
an audit decision or the synthetic-candidate labelling drifts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data/r40_batch2"


@pytest.fixture(scope="module")
def nio() -> dict:
    return json.loads((DATA / "real_run/nio_report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def batch() -> dict:
    return json.loads((DATA / "real_run/batch2_report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def audit() -> dict:
    return json.loads((DATA / "audit_report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def probe() -> dict:
    return json.loads((DATA / "context_probe.json").read_text(encoding="utf-8"))


# ── NIO revision ────────────────────────────────────────────────────────────

def test_nio_revision_used_exactly_the_authorised_budget(nio):
    assert nio["budget"]["normal_http"] == 2 and nio["budget"]["max_http"] == 2
    assert nio["ledger"]["http"] == 2
    assert nio["ledger"]["tokens"] <= nio["budget"]["max_tokens"] == 8000
    assert nio["ledger"]["statuses"] == ["SETTLED"], "no UNKNOWN admission"
    assert not nio.get("budget_breach")


def test_nio_revision_was_idempotent_and_traceable(nio):
    revision = nio["revision"]
    assert revision["revision_of"] == nio["predecessor"]["task_id"]
    assert revision["idempotent"] is True
    assert revision["second_call_reused"] is True
    assert nio["predecessor"]["status"] == "FAILED"


def test_nio_identity_gate_passed_and_the_next_gate_failed(nio):
    """The revision fixed identity; the run then stopped on attribute literality."""
    assert nio["run"]["status"] == "FAILED"
    assert "Job metadata must be literal" in nio["run"]["error"]
    assert "IDENTITY_MISMATCH" not in nio["run"]["error"]
    assert nio["run"]["jd_version_id"] is None
    assert nio["stopped"]["reason"] == "NIO_NOT_COMPLETED"


# ── five new JDs ────────────────────────────────────────────────────────────

def test_batch2_stayed_inside_its_own_budget(batch):
    spent = batch["spent"]
    assert spent["http"] == 10 == spent["normal_http"]
    assert spent["http"] <= spent["max_http"] == 14
    assert spent["tokens"] <= spent["max_tokens"] == 40000
    assert batch["ledger_total"]["statuses"] == ["SETTLED"]
    assert batch["ledger_total"]["models"] == ["deepseek-ai/DeepSeek-V4.1-Flash-1017"]


def test_all_five_completed_and_none_was_retried(batch):
    assert batch.get("stopped") is None
    assert batch.get("failed") is None
    assert len(batch["jds"]) == 5
    for entry in batch["jds"].values():
        assert entry["agent"]["status"] == "COMPLETED"
        assert entry["ledger"]["http"] == 2, "two calls per JD, no repair"
        assert entry["prepared"]["status"] == "READY_FOR_EXTRACTION"


def test_each_jd_came_from_a_different_company(batch):
    companies = {entry["company"] for entry in batch["jds"].values()}
    # Huawei had no DOM snapshot (curated text only), so it was replaced by a
    # second Kylin posting; every other slot is the requested company.
    assert companies == {"Unity中国", "面壁智能", "沐瞳科技", "蔚来", "麒麟软件"}


# ── audit gate ──────────────────────────────────────────────────────────────

def test_only_verbatim_jds_were_published(audit):
    assert audit["decisions"]["3"] == "PUBLISHED"
    assert audit["decisions"]["5"] == "PUBLISHED"
    assert audit["decisions"]["7"] == "PUBLISHED"
    assert audit["decisions"]["4"] == "HELD_WAITING_REVIEW"
    assert audit["decisions"]["6"] == "HELD_WAITING_REVIEW"
    for jd_id in ("4", "6"):
        assert any("STATEMENT_PARAPHRASED" in reason for reason in audit["gate"][jd_id]["reasons"])


def test_held_jds_have_no_paraphrase_free_alternative(audit):
    for jd_id in ("4", "6"):
        buckets = audit["audits"][jd_id]["verbatim_buckets"]
        assert buckets["PARAPHRASE"] >= 1
        assert buckets["LABEL_INVENTED"] == 0


def test_published_jds_are_verbatim_or_formatting_only(audit):
    for jd_id in ("3", "5", "7"):
        buckets = audit["audits"][jd_id]["verbatim_buckets"]
        assert buckets["PARAPHRASE"] == 0
        assert buckets["LABEL_INVENTED"] == 0


def test_published_jds_have_exact_evidence_and_decided_types(audit):
    for jd_id in ("3", "5", "7"):
        row = audit["audits"][jd_id]
        assert row["counts"]["evidence_problems"] == 0
        assert row["body_equals_snapshot"] is True and row["snapshot_hash_ok"] is True
        assert row["type_status"]["unknown_not_hard_eligibility"] == []
        assert row["counts"]["requirements"] >= 3


def test_repeat_publish_reused_the_same_version(audit):
    for entry in audit["published"].values():
        assert entry["repeat_call"]["reused"] is True
        assert entry["repeat_call"]["jd_version_id"] == entry["jd_version_id"]


def test_sandbox_now_serves_five_published_jobs(audit):
    post = audit["post"]
    assert post["selectable_jobs"] == 5
    assert post["targets"]["status"] == 200
    assert len(post["targets"]["jobs"]) == 5
    assert post["public_jobs"]["status"] == 200 and post["public_jobs"]["count"] == 5
    published = [v for v in post["versions"] if v["publication_status"] == "PUBLISHED"]
    assert len(published) == 5
    assert len(post["versions"]) == 7


def test_audit_log_records_every_review_action(audit):
    actions = [(row["action"], row["resource_id"]) for row in audit["post"]["audit_logs"]]
    assert ("public_career.jd.publish", "3") in actions
    assert ("public_career.jd.publish", "5") in actions
    assert ("public_career.jd.publish", "7") in actions
    assert ("public_career.jd.identity_revision", "4") in actions
    for row in audit["post"]["audit_logs"]:
        assert row["status"] == "SUCCESS"


# ── Interview Context ───────────────────────────────────────────────────────

def test_context_reads_the_published_jd_with_a_labelled_synthetic_profile(probe):
    assert probe["model_http_calls"] == 0
    assert probe["synthetic_candidate"] is True and probe["not_a_real_candidate"] is True
    context = probe["build_context"]
    assert context["ok"] is True
    assert context["profile_is_synthetic"] is True
    assert context["source_resume_matches_fixture"] is True
    assert context["jd_fingerprint_matches_row"] is True
    assert context["requirements"] == 10
    assert context["requirement_strength"] == {"REQUIRED": 7, "PREFERRED": 3, "UNKNOWN": 0}
    assert context["fingerprints_unique"] is True
