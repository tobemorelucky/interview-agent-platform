"""Round 16: deterministic training candidates from *formal* capability state.

Every state here comes from real replay evaluations committed through the
service (see `test_private_recheck`), never from a hand-written projection. The
candidate set is a read-time projection: no Coach report is generated or
rewritten, Harness-OFF contracts stay byte-for-byte unchanged, nothing is
written to CapabilityMemory, and clicking a candidate still goes through the
existing `TrainingService.create` admission + request-key idempotency.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.training import TrainingService, modes
from interview_api.modules.interview_report.models import FinalInterviewReport
from interview_api.modules.memory.capability_models import CapabilityMemoryVersion
from test_job_target import create_target, seed_user
from test_private_jd_skill_recommendation import ai_target
from test_private_recheck import (
    CONFLICT_HISTORY, GOOD, VERIFIED_HISTORY, WEAK, body, core_for,
    request as private_request, session_for,
)


def candidates(factory, user_id, request):
    return TrainingService(factory).candidates(user_id, request)


def test_no_memory_never_invents_a_weakness_or_recheck(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        result = await candidates(factory, user_id, private_request(target_id, question_count=8))
        assert result["projection"] == "TRAINING_CANDIDATES_V1"
        assert result["source"] == "DETERMINISTIC_RULE" and result["model_calls"] == 0
        assert result["candidates"] == []
        assert result["empty_reason"] == "NO_HISTORY_EVALUATIONS"
        # FULL is always legal and is offered as a generic start, never a
        # fabricated weakness; it cites no evaluation.
        assert result["fallback"]["mode"] == "FULL"
        assert result["fallback"]["evidence_refs"] == []
        assert "不代表当前岗位已完全胜任" in result["verified_note"]
    body(database_url, scenario)


def test_practicing_and_gap_suspected_produce_legal_weakness_with_formal_refs(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        request = private_request(target_id, question_count=8)
        await session_for(factory, user_id, target_id, GOOD)
        first = await candidates(factory, user_id, request)
        weak1 = [c for c in first["candidates"] if c["mode"] == "WEAKNESS"]
        assert weak1 and any(c["state"] == "PRACTICING" for c in weak1)
        assert all("WEAKNESS" in c["available_modes"] for c in weak1)
        assert all(c["evidence"]["evaluation_refs"] for c in weak1)
        assert all(ref.startswith("evaluation:") for c in weak1
                   for ref in c["evidence"]["evaluation_refs"])
        assert all(c["evidence"]["capability_source"] == "CAPABILITY_MEMORY_SERVICE" for c in weak1)
        # Same legal snapshot -> identical, reproducible ordering.
        repeat = await candidates(factory, user_id, request)
        assert [c["knowledge_id"] for c in first["candidates"]] == \
               [c["knowledge_id"] for c in repeat["candidates"]]
        assert first["state_fingerprint"] == repeat["state_fingerprint"]
        # Two more formal sessions rotate the blueprint to GAP_SUSPECTED.
        await session_for(factory, user_id, target_id, GOOD)
        await session_for(factory, user_id, target_id, WEAK)
        later = await candidates(factory, user_id, request)
        assert any(c["state"] == "GAP_SUSPECTED" and c["mode"] == "WEAKNESS"
                   for c in later["candidates"]), [(c["state"], c["mode"]) for c in later["candidates"]]
        assert all(c["mode"] in modes(c["state"]) for c in later["candidates"])
    body(database_url, scenario)


def test_conflicted_and_recheck_due_offer_only_legal_recheck(database_url):
    async def scenario(factory):
        # CONFLICTED, built from five real replay sessions (round-11 fixture).
        user1 = await seed_user(factory)
        target1 = await create_target(factory, user1)
        for answer in CONFLICT_HISTORY:
            await session_for(factory, user1, target1, answer)
        conflicted = await candidates(factory, user1, private_request(target1, question_count=8))
        rechecks = [c for c in conflicted["candidates"] if c["mode"] == "RECHECK"]
        assert rechecks and any(c["state"] == "CONFLICTED" for c in rechecks)
        assert not any(c["mode"] == "WEAKNESS" and c["state"] == "CONFLICTED"
                       for c in conflicted["candidates"])
        assert all(c["evidence"]["conflicts"] for c in rechecks if c["state"] == "CONFLICTED")

        # RECHECK_DUE: VERIFIED from two independent sessions, then only the
        # read clock moves (no observation or projection row is rewritten).
        user2 = await seed_user(factory)
        target2 = await create_target(factory, user2)
        for answer in VERIFIED_HISTORY:
            await session_for(factory, user2, target2, answer)
        from interview_api.modules.interview import training as training_module
        from interview_api.modules.memory import capability_service as memory_module
        future = lambda: datetime.now(timezone.utc) + timedelta(days=120)
        bound, defining = (training_module.CapabilityMemoryService,
                           memory_module.CapabilityMemoryService)
        training_module.CapabilityMemoryService = lambda db: bound(db, clock=future)
        memory_module.CapabilityMemoryService = lambda db, **kw: defining(db, clock=future, **kw)
        try:
            due = await candidates(factory, user2, private_request(target2, question_count=8))
        finally:
            training_module.CapabilityMemoryService = bound
            memory_module.CapabilityMemoryService = defining
        assert any(c["state"] == "RECHECK_DUE" and c["mode"] == "RECHECK" for c in due["candidates"]), \
            [(c["state"], c["mode"]) for c in due["candidates"]]
        assert all(c["mode"] in modes(c["state"]) for c in due["candidates"])
    body(database_url, scenario)


def test_a_stale_candidate_cannot_bypass_admission_after_the_state_changes(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        await session_for(factory, user_id, target_id, GOOD)
        request = private_request(target_id, question_count=8)
        old = await candidates(factory, user_id, request)
        stale = next(c for c in old["candidates"] if c["mode"] == "WEAKNESS")
        # A second independent good session makes the topic VERIFIED -> FULL only.
        await session_for(factory, user_id, target_id, GOOD)
        fresh = await candidates(factory, user_id, request)
        assert fresh["state_fingerprint"] != old["state_fingerprint"]
        assert not any(c["knowledge_id"] == stale["knowledge_id"] for c in fresh["candidates"])
        assert "请刷新候选" in stale["revalidation_note"]
        with pytest.raises(AppError) as denied:
            await core_for(factory).create(user_id, private_request(target_id,
                training_mode="WEAKNESS", training_knowledge_id=stale["knowledge_id"],
                training_request_key=uuid4().hex))
        assert denied.value.code == "TRAINING_TARGET_INVALID"
    body(database_url, scenario)


def test_cross_job_history_is_reused_as_evidence_but_not_mixed_with_this_target(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        first_target = await create_target(factory, user_id)
        await session_for(factory, user_id, first_target, GOOD)
        # A different private JD and a different Skill: the blueprint topics
        # are unrelated to the java_backend history.
        second_target = await ai_target(factory, user_id)
        request = CreateSessionRequest(job_target_id=second_target,
            interviewer_skill_id="ai_agent_application", adaptive_interview=True, question_count=8)
        result = await candidates(factory, user_id, request)
        assert result["job_target_id"] == second_target
        assert result["skill_id"] == "ai_agent_application"
        assert result["candidates"] == []
        assert result["empty_reason"] == "NO_HISTORY_EVALUATIONS"
        general = {g["topic"] for g in result["general_only"]}
        assert general & {"redis", "kafka_reliability"}
        assert all(g["relation"] == "GENERAL_HISTORY_NOT_CURRENT_TARGET"
                   for g in result["general_only"])
        assert "不代表当前岗位已完全胜任" in result["verified_note"]
        # R130: the public copy is user-facing; no internal function names leak.
        assert "当前推荐的练习内容" in result["semantics"]
        assert "training.modes()" not in result["semantics"]
    body(database_url, scenario)


def test_candidate_ranking_is_deterministic_and_keeps_multiple_targets():
    """Pure projection: several near-equivalent goals stay visible, fixed order."""
    from interview_api.modules.interview.training_candidates import (
        CAPABILITY_SCOPE, build_training_candidates)

    def view(topic, state, rev):
        return dict(knowledge_id="ai_agent_application:" + topic, label=topic.upper(),
                    state=state, revision=rev, revoked=False,
                    available_modes=modes(state), reason_tags=["RECENT_MISSING_EVIDENCE"],
                    independent_sessions=1, conflicts=[],
                    observations=[dict(session_id=1 + i, assisted=False, state="PARTIALLY_DEMONSTRATED",
                                       evaluation_id=10 + i, observed_at="2026-11-01T00:00:00+00:00")
                                  for i, _ in enumerate([topic])])
    views = [view("agent", "PRACTICING", 2), view("rag_retrieval", "PRACTICING", 2),
             view("kafka", "CONFLICTED", 5), view("llm", "UNKNOWN", None)]
    states = [dict(topic=v["knowledge_id"].rsplit(":", 1)[-1], state=v["state"],
                   revision=v["revision"], last_observed_at="2026-11-01T00:00:00+00:00",
                   revoked=False, observations=[dict(evaluation_id=10)]) for v in views]
    kwargs = dict(skill_id="ai_agent_application", computed_at="2026-11-13T00:00:00+00:00",
                  job_target_id=4, target_version=2)
    first = build_training_candidates(views, states, **kwargs)
    second = build_training_candidates(list(reversed(views)), list(reversed(states)), **kwargs)
    assert [(c["knowledge_id"], c["mode"]) for c in first["candidates"]] == \
           [(c["knowledge_id"], c["mode"]) for c in second["candidates"]]
    # CONFLICTED first, then the two PRACTICING goals; UNKNOWN yields nothing.
    assert [c["mode"] for c in first["candidates"]] == ["RECHECK", "WEAKNESS", "WEAKNESS"]
    assert len(first["candidates"]) == 3  # several goals shown together, not one winner
    assert all(c["capability_scope"] == CAPABILITY_SCOPE for c in first["candidates"])
    assert all(c["target_relevance"] == "CURRENT_TARGET_BLUEPRINT" for c in first["candidates"])
    assert all("跨岗位聚合" in c["scope_note"] for c in first["candidates"])
    assert all(c["launch"]["query"]["job_target_id"] == "4" for c in first["candidates"])


def test_explicit_creation_from_a_candidate_keeps_request_key_idempotency(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        await session_for(factory, user_id, target_id, GOOD)
        result = await candidates(factory, user_id, private_request(target_id, question_count=8))
        choice = result["candidates"][0]
        assert choice["launch"]["query"]["mode"] == choice["mode"]
        assert choice["launch"]["query"]["knowledge_id"] == choice["knowledge_id"]
        key = uuid4().hex
        request = private_request(target_id, training_mode=choice["mode"],
            training_knowledge_id=choice["knowledge_id"], training_request_key=key)
        core = core_for(factory)
        first = await core.create(user_id, request)
        again = await core.create(user_id, request)
        assert first["id"] == again["id"]
        assert first["context_snapshot"]["training"]["mode"] == choice["mode"]
    body(database_url, scenario)


def test_harness_off_contract_and_existing_reports_are_untouched(database_url):
    from interview_api.core.config import settings
    from interview_api.modules.candidate_profile.evidence import digest
    from interview_api.modules.interview_report.service import coach_generation_key
    assert settings.interview_coach_harness is False
    args = dict(session_id=321, content_fingerprint="fp", evaluation_fp="ev",
                prompt_version="1.0.3", prompt_hash="ph", request_key="rk")
    assert coach_generation_key(**args) == digest(
        ["interview_coach", 321, "fp", "ev", "1.0.3", "ph", "rk"])

    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        await session_for(factory, user_id, target_id, GOOD)
        async with factory() as db:
            before_reports = await db.scalar(select(func.count()).select_from(FinalInterviewReport))
            before_versions = await db.scalar(select(func.count()).select_from(CapabilityMemoryVersion))
        result = await candidates(factory, user_id, private_request(target_id, question_count=8))
        assert result["candidates"]
        async with factory() as db:
            after_reports = await db.scalar(select(func.count()).select_from(FinalInterviewReport))
            after_versions = await db.scalar(select(func.count()).select_from(CapabilityMemoryVersion))
        assert after_reports == before_reports  # no generation created or displaced
        assert after_versions == before_versions  # no formal memory fact written
    body(database_url, scenario)


def test_public_job_training_path_does_not_regress(database_url):
    async def scenario(factory):
        from v22_support import kafka_seed
        user_id, ctx = await kafka_seed(factory)
        request = CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"],
            interviewer_skill_id="java_backend", adaptive_interview=True, question_count=8)
        board = await TrainingService(factory).workbench(user_id, request)
        assert board["company_id"] == ctx["company_id"] and board["job_id"] == ctx["job_id"]
        assert board["job_target_id"] is None and board["source_label"] is None
        assert board["targets"]
        result = await candidates(factory, user_id, request)
        assert result["projection"] == "TRAINING_CANDIDATES_V1"
        assert result["job_id"] == ctx["job_id"] and result["job_target_id"] is None
        assert result.get("status") != "TARGET_UNAVAILABLE"
        assert result["empty_reason"] != "TARGET_EXPIRED_OR_UNAVAILABLE"
    body(database_url, scenario)


def test_candidates_from_a_session_use_the_frozen_snapshot_and_refuse_a_foreign_session(database_url):
    async def scenario(factory):
        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        created = await session_for(factory, user_id, target_id, GOOD)
        result = await TrainingService(factory).candidates(user_id,
            CreateSessionRequest(), session_id=created["id"])
        assert result["job_target_id"] == target_id
        assert result["target_version"] == 1
        assert result["skill_id"] == "java_backend"
        # Ownership is server-side; another user's id is not an existence oracle.
        foreign = await TrainingService(factory).candidates(user_id + 999_999,
            CreateSessionRequest(), session_id=created["id"])
        assert foreign["status"] == "TARGET_UNAVAILABLE"
        assert foreign["empty_reason"] == "TARGET_EXPIRED_OR_UNAVAILABLE"
        assert foreign["candidates"] == []
    body(database_url, scenario)
