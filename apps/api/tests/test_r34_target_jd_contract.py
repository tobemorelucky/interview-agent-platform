"""R31 contract: the three target JDs and their zero-HTTP rehearsal artifacts.

Reads only the committed JSON/text artifacts. No database, no network, no model.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "r34_target_jd"

NAV_MARKERS = ("首页", "热招岗位", "培养与发展", "校招答疑", "立即投递", "投递简历")


@pytest.fixture(scope="module")
def manifest():
    return json.loads((DATA / "source_manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def precheck():
    return json.loads((DATA / "precheck.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def replay():
    return json.loads((DATA / "replay_report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def verify():
    return json.loads((DATA / "verify_report.json").read_text(encoding="utf-8"))


def test_three_targets_with_independent_ids(manifest):
    records = manifest["records"]
    assert len(records) == 3
    assert {record["company"] for record in records} == {"蔚来", "沐瞳科技", "麒麟软件"}
    assert len({record["job_id"] for record in records}) == 3
    for record in records:
        assert record["job_id_kind"] in {"FEISHU_POSITION_ID", "BEISEN_JOB_AD_ID"}
        assert record["source_grade"] == "OFFICIAL_PAGE_CAPTURED"
        assert record["source_url"].startswith("https://")


def test_source_text_hash_matches_manifest_and_has_no_navigation(manifest):
    for record in manifest["records"]:
        text = (REPO / record["text_path"]).read_text(encoding="utf-8")
        assert sha256(text.encode("utf-8")).hexdigest() == record["text_sha256"]
        assert record["identity_in_text"] == {"company": True, "job_title": True}
        assert not [marker for marker in NAV_MARKERS if marker in text]
        assert record["still_live"] is True
        assert record["content_missing_count"] == 0


def test_postings_are_the_same_ones_already_verified_in_round33(manifest):
    dedupe = {row["jd_key"]: row for row in manifest["dedupe_vs_round33_corpus"]["records"]}
    assert dedupe["NIO_AIA_025"]["verdict"] == "SAME_POSTING_IN_ROUND33_CORPUS"
    assert dedupe["MOONTON_AGENT_005"]["verdict"] == "SAME_POSTING_IN_ROUND33_CORPUS"
    assert dedupe["KYLINOS_AI_002"]["verdict"] == "SAME_POSTING_VIA_LIST_PAGE_LINK"
    for row in manifest["dedupe_vs_formal_db"]["records"]:
        assert row["duplicate"] is False


def test_precheck_is_clean_except_the_review_gate(precheck):
    assert precheck["ready"] is True
    assert precheck["blocking"] == []
    assert precheck["warnings"] == ["OFFICIAL_DOMAINS_EMPTY_ALL_3_WILL_BE_WAITING_REVIEW"]
    assert precheck["environment"]["process_database_matches"] is True
    assert precheck["environment"]["ledger_matches_process"] is True
    assert precheck["database"]["alembic_version"] == "0029"
    assert all(count == 0 for count in precheck["database"]["row_counts"].values())


def test_rehearsal_imports_all_three_with_evidence(replay, verify):
    assert replay["model_http_calls"] == 0
    assert len(replay["jds"]) == 3
    for key, entry in replay["jds"].items():
        assert entry["agent_result"]["status"] == "COMPLETED"
        assert entry["agent_result"]["manual_source_type"] == "official"
        # Nothing is published by the research agent itself.
        assert entry["agent_result"]["publication_status"] == "WAITING_REVIEW"
        assert entry["publish"]["publication_status"] == "PUBLISHED"
        assert verify["jds"][key]["raw_text_matches_snapshot"] is True
        assert verify["jds"][key]["snapshot_sha256_ok"] is True
        assert verify["jds"][key]["offset_exact"] == verify["jds"][key]["requirement_total"]
        assert verify["jds"][key]["literal"] == verify["jds"][key]["requirement_total"]
    assert sum(row["requirement_total"] for row in verify["jds"].values()) == 35


def test_failure_injection_leaves_no_partial_entity(replay):
    failure = replay["jds"]["KYLINOS_AI_002"]["failure_injection"]
    assert failure["result_status"] == "FAILED"
    assert failure["task_status"] == "FAILED"
    assert failure["companies_after_failure"] == 0
    assert failure["jd_versions_after_failure"] == 0
    assert failure["published_after_failure"] == 0
    retry = replay["jds"]["KYLINOS_AI_002"]["retry_prepare"]
    assert retry["reused"] is True and retry["refresh_task_id"] == 1


def test_repeat_import_is_idempotent(replay):
    idempotency = replay["idempotency"]
    assert idempotency["prepare_reused"] is True
    assert idempotency["same_task_id"] is True
    assert "READY_FOR_EXTRACTION" in idempotency["agent_rerun_refused"]
    assert idempotency["republish_reused"] is True
    assert replay["counts"]["jd_versions"] == 3


def test_company_and_version_isolation(replay):
    rows = replay["isolation"]
    assert len(rows) == 3
    assert len({row["job_id"] for row in rows}) == 3
    assert {row["version"] for row in rows} == {1}
    assert {row["company"] for row in rows} == {"蔚来", "沐瞳科技", "麒麟软件"}


def test_interview_target_api_lists_all_three_with_skill_reason(replay):
    targets = replay["targets_after_publish"]
    assert len(targets) == 3
    for target in targets:
        assert target["status"] == "RECOMMENDED"
        assert target["skill"] == "ai_agent_application"
        assert target["reason"]


def test_budget_matches_the_real_call_chain(precheck):
    budget = precheck["budget"]
    assert budget["normal_http"] == 6
    assert budget["normal_breakdown"] == {"job_jd_extraction": 3, "quality_assessment": 3}
    assert budget["hard_stop_http"] == 10
    assert budget["repair_http_allowance"] == 2


# ── Real-model run (R31 formal) ─────────────────────────────────────────────

REAL = DATA / "real_run"


@pytest.fixture(scope="module")
def real_run():
    return json.loads((REAL / "real_run_report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def audit():
    return json.loads((REAL / "audit.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def capture():
    return json.loads((REAL / "capture_replay.json").read_text(encoding="utf-8"))


def test_real_run_stayed_inside_the_authorised_budget(real_run, capture):
    ledger = real_run["ledger_total"]
    assert ledger["http_attempts"] <= real_run["limits"]["max_http"] == 10
    assert ledger["tokens"] <= real_run["limits"]["max_tokens"] == 30000
    assert ledger["statuses"] == ["SETTLED"], "no UNKNOWN or unpaid admission"
    assert capture["totals"]["repairs"] == 0
    assert {step["model"] for step in capture["steps"]} == {"deepseek-ai/DeepSeek-V4.1-Flash-1017"}
    assert {step["provider"] for step in capture["steps"]} == {"modelscope"}


def test_every_step_has_a_prompt_version_and_usage(capture):
    assert capture["steps"]
    for step in capture["steps"]:
        assert step["prompt"] in {"job_jd@1.0.1", "quality@1.0.0"}
        assert step["status"] == "SUCCEEDED"
        assert step["usage"]["total_tokens"] > 0


def test_completed_jds_are_waiting_review_with_full_evidence(audit):
    assert audit["jd_versions"], "at least one JD imported"
    for key, row in audit["jds"].items():
        assert row["publication_status"] == "WAITING_REVIEW"
        assert row["raw_text_equals_snapshot"] is True
        assert row["snapshot_sha256_ok"] is True
        assert row["counts"]["missing_claim"] == 0
        assert row["counts"]["offsets_bad"] == 0
        assert row["counts"]["literal_bad"] == 0
        # every requirement must be a literal substring of the JD body too
        assert all(item["literal_in_raw_text"] for item in row["requirements"])


def test_publish_readiness_is_true_but_nothing_is_published(audit):
    for key, row in audit["readiness"].items():
        assert row["publishable"] is True
        assert row["problems"] == []
    assert audit["audit_logs"] == 0, "no Admin publication happened"
    for row in audit["jds"].values():
        assert row["publication_status"] != "PUBLISHED"


def test_failed_task_left_no_entity_and_no_retry(real_run, audit):
    failed = [task for task in audit["tasks"] if task["status"] == "FAILED"]
    for task in failed:
        assert "MANUAL_EXTRACTED_IDENTITY_MISMATCH" in task["error"]
    companies = {row["company"] for row in audit["jds"].values()}
    assert len(audit["jd_versions"]) == len(companies) == len(audit["tasks"]) - len(failed)


def test_repeat_import_reused_the_completed_task(audit):
    assert audit["idempotency"]["reused"] is True
    assert audit["idempotency"]["prepare_status"] == "COMPLETED"


def test_capture_bundle_is_redacted(capture):
    # The bundle declares what it excludes; that declaration is not a secret.
    payload = {key: value for key, value in capture.items() if key != "redaction"}
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for secret in ("api_key", "authorization:", "bearer ", "sk-", "password"):
        assert secret not in blob
