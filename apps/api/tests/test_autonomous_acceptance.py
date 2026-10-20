"""Failure recovery probes: local fault injection, never claimed as live calls."""
import asyncio
import json
from copy import deepcopy
from unittest.mock import patch

import pytest
from sqlalchemy import select, func

from interview_api.infrastructure.models.provider import ModelProviderError
from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation, InterviewAgentCall
from interview_api.modules.experience.models import ExperienceAgentRun
from test_m4_interview import seed, queue_start, queue_answer, run_with_database


def test_model_context_deduplicates_representations_without_losing_grounding():
    from interview_api.modules.interview.context import model_context
    fact = dict(id=1, field_path="projects.0", statement="实际项目摘录", evidence_ids=["real-key"])
    context = dict(profile={"projects": [dict(label="项目名称", statement=fact["statement"], evidence_ids=fact["evidence_ids"])]},
        normalized={"projects": [dict(id=8, fact_id=1, label="项目名称", statement=fact["statement"])]},
        candidate_facts=[fact], candidate_evidence=[dict(id=3, key="real-key", kind="RESUME", section="projects",
            excerpt="实际项目摘录", start_offset=10, end_offset=16, content_hash="server-hash")],
        requirements=[dict(id=4, statement="岗位原文")], public_questions=[dict(id=5, question="原题", fingerprint="pinned")],
        approved_memory_context={"items": [{"id": 6, "content": "本人确认"}], "fingerprint": "memory"},
        recent_performance_observations={"items": [{"id": 7, "content": "仅本场观察"}], "fingerprint": "observation"})
    pinned = deepcopy(context)
    payload = model_context(context)
    assert "profile" not in payload and "normalized" not in payload
    assert payload["candidate_facts"] == [{k: v for k, v in {**fact, "label": "项目名称"}.items() if k != "statement"}]
    assert payload["candidate_evidence"] == [dict(key="real-key", kind="RESUME", section="projects", excerpt="实际项目摘录")]
    for key in ("requirements", "public_questions"):
        assert payload[key] == pinned[key]
    for key in ("approved_memory_context", "recent_performance_observations"):
        assert payload[key]["items"] == pinned[key]["items"]
        assert "NOT_STABLE_FACTS" in payload[key]["semantics"] or "NOT_SKILL_PROOF" in payload[key]["semantics"]
    payload["candidate_evidence"][0]["excerpt"] = "mutation of detached DTO"
    assert context == pinned
    context["candidate_evidence"][0]["kind"] = "USER_CONFIRMED"
    assert model_context(context)["candidate_evidence"][0]["kind"] == "USER_CONFIRMED"


def test_follow_up_budget_display_counts_only_the_current_root_question():
    from types import SimpleNamespace
    from interview_api.modules.interview.controller import SessionController
    def q(id, kind="NEXT", parent=None):
        return SimpleNamespace(id=id, question_kind=kind, parent_question_id=parent)
    questions = [q(1), q(2, "FOLLOW_UP", 1), q(3), q(4, "FOLLOW_UP", 3), q(5, "DEEP_DIVE", 4)]
    assert SessionController.follow_up_consumption(questions, questions[0]) == 1
    assert SessionController.follow_up_consumption(questions, questions[3]) == 2


def test_model_projection_preserves_user_confirmed_skill_state_without_promotion():
    from interview_api.modules.interview.context import model_context
    context = dict(profile={"skills": [dict(label="Redis", evidence_state="MENTIONED")]}, normalized={},
        candidate_facts=[dict(id=1, field_path="skills.0", statement="Redis", evidence_ids=["confirmed-key"])],
        candidate_evidence=[dict(key="confirmed-key", kind="USER_CONFIRMED", excerpt="Redis")])
    result = model_context(context)
    assert result["candidate_facts"][0]["label"] == "Redis"
    assert result["candidate_facts"][0]["evidence_state"] == "MENTIONED"
    assert result["candidate_evidence"][0]["kind"] == "USER_CONFIRMED"


def test_duplicate_snapshot_representations_do_not_strand_six_question_budget(database_url):
    """Audited offline fixture: default-length run; no real network/secret/data."""
    from interview_api.infrastructure.models.provider import ReplayModelProvider, ModelResponse
    from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
    async def scenario(factory):
        core, _, _, uid, detail = await seed(factory, budget=6)
        sid = detail["id"]
        class AuditedFixture(ReplayModelProvider):
            provider_name = "local_accounting_fixture"
            model_name = "DEMO_NO_NETWORK"
            async def generate(self, messages, *, schema):
                response = await super().generate(messages, schema=schema)
                return ModelResponse(response.text, dict(input_tokens=10000, output_tokens=1000, total_tokens=11000))
        main, evaluator = AuditedFixture([]), AuditedFixture([])
        core._router = ModelRouter({INTERVIEW_MAIN_ROUTE: main, ANSWER_EVALUATION_ROUTE: evaluator})
        read = core.read_state
        async def duplicated_snapshot(*args):
            session, context, questions, active, tokens = await read(*args)
            # Redundant legacy representations, not new facts or altered Evidence.
            context["profile"]["legacy_duplicate"] = "重复表达" * 5000
            context["normalized"]["legacy_duplicate"] = context["profile"]["legacy_duplicate"]
            return session, context, questions, active, tokens
        core.read_state = duplicated_snapshot
        await queue_start(core, main, uid, sid)
        for index in range(6):
            request = await queue_answer(core, main, evaluator, uid, sid, "NEXT")
            events = await core.answer(sid, uid, request.question_id, request.idempotency_key, request.content)
            assert events[0][0] == "evaluation"
            assert events[1][0] == ("complete" if index == 5 else "question")
        final = await core.detail(sid, uid)
        assert final["status"] == "COMPLETED" and final["completion_json"]["evaluated_count"] == 6
        assert final["snapshot_fingerprint"] == detail["snapshot_fingerprint"]
        assert final["context_snapshot"] == detail["context_snapshot"]
        assert all(q["status"] == "COMPLETED" for q in final["questions"])
    asyncio.run(run_with_database(database_url, scenario))


def test_focused_interviewer_prompt_preserves_historical_versions_and_source_contract():
    from interview_api.modules.interview.agents import InterviewPromptRegistry
    from interview_api.infrastructure.models.router import INTERVIEW_MAIN_ROUTE
    registry = InterviewPromptRegistry()
    latest = registry.get(INTERVIEW_MAIN_ROUTE)
    assert latest.version == "1.0.13"
    assert "only ONE focal gap" in latest.system
    assert "trade-off" in latest.system
    assert "matching evidence KEY" in latest.system
    for version in ("1.0.0", "1.0.1", "1.0.2"):
        assert registry.get(INTERVIEW_MAIN_ROUTE, version).version == version


def test_coach_prose_numeric_guard_is_not_weakened_and_has_safe_field_diagnostics():
    from interview_api.core.exceptions import AppError
    from interview_api.modules.interview_report.schemas import CoachArtifact
    from interview_api.modules.interview_report.resolver import resolve_coach_artifact
    from interview_api.modules.interview_report.prompts import CoachPromptRegistry
    from interview_api.infrastructure.models.router import INTERVIEW_COACH_ROUTE
    value = dict(overall_summary="本场三轮回答表现不一致", strengths=[], weaknesses=[], recurring_issues=[],
        job_alignment=[], recommended_actions=[], practice_plan=[], memory_proposals=[])
    with pytest.raises(AppError) as exc:
        resolve_coach_artifact(CoachArtifact.model_validate(value), [])
    assert exc.value.code == "COACH_COMPUTED_NUMBER"
    assert exc.value.diagnostics == dict(field_path="overall_summary", rule="DETERMINISTIC_NUMBERS_ONLY", matched_number="三轮回答")
    assert "本场" not in str(exc.value.diagnostics)
    registry = CoachPromptRegistry()
    assert registry.get(INTERVIEW_COACH_ROUTE).version == "1.0.3"
    assert registry.get(INTERVIEW_COACH_ROUTE, "1.0.0").version == "1.0.0"


@pytest.mark.parametrize("failing_role", ["evaluation", "next_question"])
def test_timeout_refresh_same_answer_retry_has_one_effective_evaluation(database_url, failing_role):
    async def scenario(factory):
        core, main, evaluator, uid, detail = await seed(factory, budget=3)
        sid = detail["id"]
        pinned = detail["snapshot_fingerprint"]
        await queue_start(core, main, uid, sid)
        request = await queue_answer(core, main, evaluator, uid, sid, "NEXT")
        target = evaluator if failing_role == "evaluation" else main

        async def timeout(*args, **kwargs):
            raise ModelProviderError("model_timeout", "Injected acceptance timeout (offline)")

        with patch.object(target, "generate", timeout), pytest.raises(ModelProviderError):
            await core.answer(sid, uid, request.question_id, request.idempotency_key, request.content)
        refreshed = await core.detail(sid, uid)
        assert refreshed["status"] == "IN_PROGRESS"
        assert refreshed["current_question"]["status"] == "ANSWERED"
        assert refreshed["active_question_id"] == request.question_id
        assert refreshed["pending_answer"]["status"] == "FAILED"
        assert refreshed["pending_answer"]["idempotency_key"] == request.idempotency_key
        assert refreshed["completion_json"] is None
        assert refreshed["snapshot_fingerprint"] == pinned
        async with factory() as db:
            assert not await db.scalar(select(func.count(InterviewEvaluation.id)).where(InterviewEvaluation.session_id == sid))
            audit = list((await db.scalars(select(ExperienceAgentRun).join(InterviewAgentCall,
                InterviewAgentCall.agent_run_id == ExperienceAgentRun.id).where(InterviewAgentCall.session_id == sid))).all())
            assert any(r.status == "FAILED" and r.error_message == "model_timeout" for r in audit)
        # A validated evaluation survives a next-question failure and must not be rescored.
        evaluation_calls = evaluator.calls
        events = await core.answer(sid, uid, request.question_id, request.idempotency_key, request.content)
        if failing_role == "next_question":
            assert evaluator.calls == evaluation_calls
        assert [event for event, _ in events] == ["evaluation", "question", "done"]
        assert await core.answer(sid, uid, request.question_id, request.idempotency_key, request.content) == events
        async with factory() as db:
            assert await db.scalar(select(func.count(InterviewAnswer.id)).where(InterviewAnswer.session_id == sid)) == 1
            assert await db.scalar(select(func.count(InterviewEvaluation.id)).where(InterviewEvaluation.session_id == sid)) == 1
        assert (await core.detail(sid, uid))["snapshot_fingerprint"] == pinned
    asyncio.run(run_with_database(database_url, scenario))
