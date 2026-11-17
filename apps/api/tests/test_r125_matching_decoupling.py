"""R125 P0-A: a failed AI interpretation must never hide the deterministic ranking.

Production evidence: run 4 (2026-11-17) failed with ``MATCH_INPUT_BUDGET`` before
any model call, so the page showed only "生成失败" even though 20 ranked
candidates were already persisted. Two defects are pinned here:

* the model envelope carried server-derived duplicates (``evidence_matrix.supports``
  repeated the same 64-char evidence keys for every job) and crossed the 200 KB
  budget, so the AI layer could not even start;
* one AI failure flipped the whole run to FAILED, which read as "no result".

The fix bounds the model view without dropping evidence and separates
"deterministic ranking exists" from "AI interpretation completed".
"""
import json

import pytest
from sqlalchemy import select

from interview_api.core.config import settings
from interview_api.modules.job_matching.context import (
    MAX_SUPPORTS_PER_REQUIREMENT, PROJECTION_VERSION, model_context,
)
from interview_api.modules.job_matching.models import JobMatchAttempt
from interview_api.modules.job_matching.prompts import MATCH_PROMPT
from interview_api.modules.job_matching.schemas import MatchArtifact
from interview_api.modules.job_matching.service import JobMatchingService
from test_m4_interview import run_with_database
from test_m6_job_matching import artifact, seed_matching


def _support(fact_id, status, size=3):
    return dict(candidate_fact_id=fact_id, fact_ref=f"fact:{fact_id}", category="skills", record_id=fact_id,
                evidence_ids=[f"re_{fact_id}_{n:02d}" + "0" * 40 for n in range(size)],
                resume_evidence_ids=[fact_id], user_evidence_ids=[], status=status,
                matched_topics=["java"], explanation="当前材料事实与岗位要求命中的技术点一致；不代表熟练度。")


def _matrix(requirement_id, status, supports):
    return dict(id=f"alignment:{requirement_id}", job_requirement_id=requirement_id,
                requirement_ref=f"requirement:{requirement_id}", statement=f"岗位要求 {requirement_id}：熟悉相关技术",
                job_description_version_id=1, candidate_profile_version_id=1, importance="REQUIRED",
                status=status, supports=supports, matched_topics=["java"], message="文本交集")


def _snapshot(candidates=20, requirements=5, supports_per_row=24):
    rows = []
    for job_id in range(1, candidates + 1):
        matrix = []
        for index in range(requirements):
            requirement_id = job_id * 100 + index
            status = "NOT_APPLICABLE" if index == 0 else "SUPPORTED"
            linked = [] if status == "NOT_APPLICABLE" else [
                _support(1000 + index * 100 + n, "SUPPORTED" if n % 2 == 0 else "PARTIAL")
                for n in range(supports_per_row)]
            matrix.append(_matrix(requirement_id, status, linked))
        rows.append(dict(job_id=job_id, company_id=job_id, company_name=f"公司{job_id}", title=f"岗位{job_id}",
                         job_family="BACKEND", campus_type="CAMPUS", status="ACTIVE", jd_id=job_id,
                         jd_version_number=1, jd_fingerprint="f" * 64, tags=["Java"], matching_constraints={},
                         demo=False, sql_tag_overlap=0, topic_overlap=0, job_terms=["java"], input_fingerprint="a" * 64,
                         eligibility_status="SATISFIED", eligibility_reasons=[], unknown_constraints=["工作地点"],
                         requirements=[dict(id=row["job_requirement_id"], requirement_type="OTHER",
                                            normalized_value=None, statement=row["statement"],
                                            importance="REQUIRED", claim_id=None) for row in matrix],
                         evidence_matrix=matrix,
                         ranking_components=dict(policy_version="x", fit_band_ceiling="GOOD_MATCH",
                                                 required_counts=dict(SUPPORTED=4, PARTIAL=0, NO_EVIDENCE=1)),
                         pre_rank=job_id))
    return dict(request={"request_key": "k", "candidate_profile_version_id": 1,
                         "filters": dict(campus_types=[], job_families=[], locations=[], tags=[],
                                         exclude_company_ids=[], exclude_job_ids=[], exclude_tags=[])},
                profile=dict(id=1, version_number=1, producer="AGENT", summary="摘要", unknowns=[],
                             facts=[dict(id=i, label=f"技能{i}", category="skills", statement="Java") for i in range(26)],
                             evidence=[dict(key=f"re_{i}", id=i, kind="RESUME", section="skills",
                                            excerpt="简历原文段落" * 8) for i in range(35)],
                             fingerprint="b" * 64),
                approved_preferences=dict(items=[], omitted_count=0), candidates=rows, excluded_candidates=[],
                policy_version="literal-evidence-eligibility-v2", prompt_version=MATCH_PROMPT.version,
                prompt_hash=MATCH_PROMPT.content_hash, ranking_policy="x", data_mode="PERSISTED_PUBLIC_DATA")


def _envelope(snapshot):
    return json.dumps(dict(input=model_context(snapshot), schema=MatchArtifact.model_json_schema(),
                           system=MATCH_PROMPT.system), ensure_ascii=False).encode()


def test_full_window_projection_fits_the_input_budget():
    snapshot = _snapshot()
    envelope = _envelope(snapshot)
    assert len(envelope) <= settings.job_matching_max_input_bytes, len(envelope)
    # Sanity: the same window without the bounded projection blows the budget.
    assert len(json.dumps(snapshot, ensure_ascii=False).encode()) > settings.job_matching_max_input_bytes


def test_projection_keeps_every_requirement_and_its_verdict():
    snapshot = _snapshot(candidates=3, requirements=4)
    projected = model_context(snapshot)
    for original, kept in zip(snapshot["candidates"], projected["candidates"]):
        assert len(kept["evidence_matrix"]) == len(original["evidence_matrix"])
        assert [row["status"] for row in kept["evidence_matrix"]] == [row["status"] for row in original["evidence_matrix"]]
        assert [row["statement"] for row in kept["evidence_matrix"]] == [row["statement"] for row in original["evidence_matrix"]]
        # Requirement text and profile evidence are the evidence, never dropped.
        assert kept["requirements"] == original["requirements"]
        assert len(kept["evidence_matrix"][1]["supports"]) == MAX_SUPPORTS_PER_REQUIREMENT
    assert len(projected["profile"]["facts"]) == 26
    assert len(projected["profile"]["evidence"]) == 35


def test_projection_keeps_the_strongest_association_first():
    snapshot = _snapshot(candidates=1, requirements=2, supports_per_row=8)
    row = model_context(snapshot)["candidates"][0]["evidence_matrix"][1]
    assert len(row["supports"]) == MAX_SUPPORTS_PER_REQUIREMENT
    ranks = [0 if s["status"] == "SUPPORTED" else 1 for s in row["supports"]]
    assert ranks == sorted(ranks), row["supports"]
    # A row with no SUPPORTED association still keeps its best PARTIAL evidence.
    snapshot["candidates"][0]["evidence_matrix"][1]["supports"] = [
        _support(2000 + n, "PARTIAL") for n in range(5)]
    partial_only = model_context(snapshot)["candidates"][0]["evidence_matrix"][1]
    assert [s["status"] for s in partial_only["supports"]] == ["PARTIAL", "PARTIAL"]


def test_projection_drops_only_server_derived_duplicates():
    projected = model_context(_snapshot(candidates=1))["candidates"][0]
    for server_only in ("ranking_components", "job_terms", "topic_overlap", "sql_tag_overlap",
                        "jd_fingerprint", "input_fingerprint"):
        assert server_only not in projected
    # The ceiling is a real constraint for the prompt, so it survives as a scalar.
    assert projected["fit_band_ceiling"] == "GOOD_MATCH"
    assert projected["evidence_counts"] == dict(SUPPORTED=4, PARTIAL=0, NO_EVIDENCE=1)
    assert projected["eligibility_status"] == "SATISFIED"


def test_projection_is_versioned_so_old_runs_keep_their_own_shape():
    assert PROJECTION_VERSION in json.dumps(model_context(_snapshot(candidates=1)))


@pytest.mark.parametrize("status,error,usable,expected", [
    ("COMPLETED", None, True, "AI_COMPLETED"),
    ("RUNNING", None, True, "AI_RUNNING"),
    ("READY", None, True, "RULE_READY"),
    ("READY", "MATCH_INPUT_BUDGET", True, "AI_FAILED_RULE_READY"),
    ("FAILED", "MATCH_COVERAGE_INSUFFICIENT", False, "NO_RESULT"),
])
def test_result_state_never_pretends_the_ai_ran(status, error, usable, expected):
    assert JobMatchingService.result_state(status, "MODEL", error, usable) == expected


def test_ai_failure_keeps_ranked_candidates_and_does_not_charge_again(database_url):
    async def run(factory):
        core, provider, uid, _, _, _, created = await seed_matching(factory)
        assert created["result_state"] == "RULE_READY" and provider.calls == 0
        rule_order = [c["job_id"] for c in created["candidates"]]

        # Model layer dies: the rule result must stay readable and in the same order.
        failed = await core.generate(uid, created["id"])
        assert failed["result_state"] == "AI_FAILED_RULE_READY"
        assert failed["rule_ready"] is True and failed["error"]
        assert [c["job_id"] for c in failed["candidates"]] == rule_order
        assert all(c["recommendation"] is None for c in failed["candidates"])
        assert failed["coverage"] == created["coverage"]

        # A later retry is explicit, bounded, and completes on the existing run.
        provider.responses.append(artifact(created))
        retried = await core.generate(uid, created["id"])
        assert retried["result_state"] == "AI_COMPLETED" and retried["status"] == "COMPLETED"
        assert provider.calls == 2
        async with factory() as db:
            attempts = list((await db.scalars(select(JobMatchAttempt).where(
                JobMatchAttempt.run_id == created["id"]).order_by(JobMatchAttempt.id))).all())
            assert [a.status for a in attempts] == ["FAILED", "SUCCEEDED"]
        # Reading again never triggers another model call.
        assert (await core.detail(uid, created["id"]))["status"] == "COMPLETED" and provider.calls == 2
        history = await core.history(uid)
        assert history[0]["result_state"] == "AI_COMPLETED"
    asyncio_run(database_url, run)


def asyncio_run(database_url, fn):
    import asyncio
    asyncio.run(run_with_database(database_url, fn))
