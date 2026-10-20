"""Skill Engine: deterministic policy + Replay; never contacts a paid model."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.skill_engine import SkillRegistry, ROOT, agent_payload, evaluation_contract
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.context import question_policy
from interview_api.modules.interview.controller import SessionController
from interview_api.modules.interview.agents import configured_prompt_registry
from test_interview_topic_policy import context, row


def configured(skill="ai_agent_application", round_id="technical_screen", style="standard"):
    value = context()
    value["public_questions"] = [dict(id=1, question="RAG 检索如何融合？"),
        dict(id=2, question="Java 线程如何协作？"), dict(id=3, question="数据权限如何控制？")]
    value["candidate_facts"] += [dict(id=3, field_path="projects.1", statement="RAG 检索实现", evidence_ids=["e1"])]
    value["interviewer_config"] = SkillRegistry().freeze(skill, round_id, style)
    return value


def test_catalog_and_demand_loading():
    reads = []
    original = Path.read_text
    def read(path, *args, **kwargs):
        reads.append(str(path))
        return original(path, *args, **kwargs)
    with patch.object(Path, "read_text", read):
        assert len(SkillRegistry().options()["skills"]) == 3
        assert not any(path.endswith("SKILL.md") for path in reads)
        value = SkillRegistry().freeze("java_backend", "technical_screen", "standard")
    assert value["skill"]["id"] == "java_backend"
    assert sum(path.endswith("SKILL.md") for path in reads) == 1
    assert not any("ai_agent_application/1.0.0/skill.json" in path.replace("\\", "/") for path in reads)


@pytest.mark.parametrize("skill,expected", [("ai_agent_application", "rag_retrieval"), ("java_backend", "java"), ("bank_it", "data_security")])
def test_same_profile_distinct_skill_source(skill, expected):
    policy = question_policy(configured(skill), [])
    assert policy["target_topic"] == expected


def test_round_changes_opening_category_not_caps():
    screen = configured()
    deep = configured(round_id="project_deep_dive")
    assert question_policy(screen, [])["target_category"] == "FOUNDATION"
    assert question_policy(deep, [])["target_category"] == "PROJECT"
    assert screen["budgets"] == deep["budgets"]


@pytest.mark.parametrize("style,expected,mode,hint", [
    ("gentle", "FOLLOW_UP", "CLARIFY_WITH_PROCESS_HINT", True),
    ("standard", "FOLLOW_UP", "MECHANISM_THEN_TRADEOFF", False),
    ("strict", "DEEP_DIVE", "VERIFY_BOUNDARY_OR_COUNTEREXAMPLE", False),
])
def test_style_changes_actual_action_and_guidance(style, expected, mode, hint):
    value = configured(style=style)
    questions = [row(0, "RAG 检索如何融合？")]
    action = SessionController.choose_action(value, questions, questions[0], "FOLLOW_UP",
        rounds=1, tokens=0, started_at=None, evaluation={"missing_points": ["融合机制"]})
    assert action == expected
    policy = question_policy(value, questions, action)
    assert policy["interaction"]["probe_mode"] == mode
    assert bool(policy["interaction"]["guidance"]) is hint
    assert SessionController.adjusted_difficulty(SimpleNamespace(difficulty="MEDIUM"),
        dict.fromkeys(("technical_correctness", "coverage", "depth", "relevance", "communication"), 3)) == "MEDIUM"


def test_evaluation_payload_and_prompt_are_style_blind():
    results, prompts = [], []
    for style in ("gentle", "standard", "strict"):
        payload = dict(snapshot=configured(style=style), current_question=dict(question="同一个问题", evidence={
            "interviewer_guidance": "一个表达提示" if style == "gentle" else None}), answer="同一个回答")
        frozen = deepcopy(payload)
        result = agent_payload(payload, "answer_evaluation")
        results.append(result)
        prompts.append(configured_prompt_registry(result).get("answer_evaluation"))
        assert payload == frozen
        assert "style" not in json.dumps(result, ensure_ascii=False)
    assert results[0] == results[1] == results[2]
    assert prompts[0] == prompts[1] == prompts[2]


def test_all_styles_share_deterministic_difficulty():
    from interview_api.modules.interview.core_service import MockInterviewService
    for style in ("gentle", "standard", "strict"):
        value = configured(style=style)
        assert MockInterviewService.payload(value, [], None, "NEXT", {})["desired_difficulty"] == "MEDIUM"
        active = SimpleNamespace(difficulty="EASY")
        # Check the policy independently of question serialization.
        from interview_api.modules.interview.controller import SessionController
        assert SessionController.adjusted_difficulty(active, dict.fromkeys(
            ("technical_correctness", "coverage", "depth", "relevance", "communication"), 3)) == "EASY"


@pytest.mark.parametrize("style", ["gentle", "standard", "strict"])
def test_style_cannot_break_budget_followup_or_cooldown(style):
    value = configured(style=style)
    questions = [row(i, "RAG 检索原理", parent=1 if i else None) for i in range(3)]
    assert SessionController.choose_action(value, questions, questions[-1], "FOLLOW_UP", rounds=3,
        tokens=0, started_at=None, evaluation={"missing_points": ["细节"]}) == "NEXT"
    value["budgets"]["question_count"] = 3
    assert SessionController.choose_action(value, questions, questions[-1], "FOLLOW_UP", rounds=3,
        tokens=0, started_at=None, evaluation={"missing_points": ["细节"]}) == "COMPLETE"


def test_snapshot_contains_exact_config_and_generic_fallback(tmp_path):
    import shutil
    shutil.copytree(ROOT, tmp_path / "skills")
    registry = SkillRegistry(tmp_path / "skills")
    first = registry.freeze("java_backend", "technical_screen", "strict")
    pinned = deepcopy(first)
    assert first["company_background"]["mode"] == "GENERIC_SKILL_FALLBACK"
    # A real catalog upgrade in a temporary asset tree, never the repository.
    import shutil
    directory = tmp_path / "skills" / "java_backend" / "1.0.1"
    shutil.copytree(directory.with_name("1.0.0"), directory)
    definition = json.loads((directory / "skill.json").read_text(encoding="utf-8"))
    definition["version"] = "1.0.1"
    definition["priority_topics"] = ["system_design"]
    (directory / "skill.json").write_text(json.dumps(definition), encoding="utf-8")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "1.0.1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    from interview_api.modules.interview.skill_registry import discover
    discover(tmp_path / "skills", refresh=True)
    second = registry.freeze("java_backend", "technical_screen", "strict")
    assert second["skill"]["version"] == "1.0.1"
    assert first["skill"]["version"] == "1.0.0"
    assert first == pinned and first["content_fingerprint"] != second["content_fingerprint"]
    sourced = registry.freeze("java_backend", "technical_screen", "standard",
        requirements=[dict(id=7)], jd_source=dict(source_type="COMMUNITY", snapshot_id=4))
    assert sourced["company_background"]["source"]["source_type"] == "COMMUNITY"
    assert sourced["company_background"]["requirement_ids"] == [7]


@pytest.mark.parametrize("field,value", [("interviewer_skill_id", "../../oops"), ("interviewer_style", "hostile"), ("interview_round", "hr")])
def test_unknown_client_configuration_rejected(field, value):
    with pytest.raises(ValidationError):
        CreateSessionRequest.model_validate(dict(company_id=1, job_id=1, **{field: value}))


def test_legacy_has_no_new_policy_and_prompt_versions_remain_available():
    payload = dict(snapshot=context())
    assert agent_payload(payload, "answer_evaluation") == payload
    assert configured_prompt_registry(payload).get("interview_main").version == "1.0.13"
    assert configured_prompt_registry(dict(snapshot=configured())).get("interview_main").version == "1.1.0"
    assert configured_prompt_registry(dict(snapshot=configured())).get("interview_main", "1.0.13").version == "1.0.13"


def test_replay_session_persists_config_retries_and_completes(database_url):
    import asyncio
    from uuid import uuid4
    from sqlalchemy import select, func
    from test_m4_interview import seed, queue_start, question, evaluation, run_with_database
    from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation, InterviewSessionQuestion, InterviewContextSnapshot

    async def scenario(factory):
        core, main, evaluator, uid, old = await seed(factory, budget=3)
        assert "interviewer_config" not in old["context_snapshot"]
        ctx = old["context_snapshot"]
        new = await core.create(uid, CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"],
            question_count=3, interviewer_skill_id="java_backend", interview_round="project_deep_dive", interviewer_style="strict"))
        sid = new["id"]
        pinned = deepcopy(new["context_snapshot"])
        assert pinned["interviewer_config"]["company_background"]["mode"] == "GENERIC_SKILL_FALLBACK"
        with patch.object(SkillRegistry, "freeze", side_effect=AssertionError("Must not reload running config")):
            await queue_start(core, main, uid, sid)
            for index in range(3):
                session, context_value, questions, active, tokens = await core.read_state(sid, uid)
                ev = evaluation("FOLLOW_UP" if index == 0 else "NEXT", score=4)
                action = SessionController.choose_action(context_value, questions, active, ev["next_action_recommendation"],
                    rounds=session.turn_count + 1, tokens=tokens, started_at=session.started_at, evaluation=ev)
                if index == 0:
                    assert action == "DEEP_DIVE"
                evaluator.responses.append(ev)
                if action != "COMPLETE":
                    main.responses.append(question(context_value, question_policy(context_value, questions, action), action))
                key = uuid4().hex
                args = (sid, uid, active.id, key, "Replay: 解释事务与幂等处理")
                events = await core.answer(*args)
                calls = main.calls + evaluator.calls
                assert events == await core.answer(*args)
                assert main.calls + evaluator.calls == calls
            final = await core.detail(sid, uid)
        assert final["status"] == "COMPLETED"
        assert final["context_snapshot"] == pinned
        assert final["completion_json"]["evaluated_count"] == 3
        async with factory() as db:
            assert await db.scalar(select(func.count(InterviewAnswer.id)).where(InterviewAnswer.session_id == sid)) == 3
            scores = (await db.scalars(select(InterviewEvaluation.technical_correctness).where(InterviewEvaluation.session_id == sid))).all()
            assert scores == [4, 4, 4]  # strict never subtracts points
            rows = (await db.scalars(select(InterviewSessionQuestion).where(InterviewSessionQuestion.session_id == sid))).all()
            assert all(q.status == "COMPLETED" for q in rows)
            assert (await db.get(InterviewContextSnapshot, final["context_snapshot_id"])).context_json == pinned
        with pytest.raises(AppError) as denied:
            await core.detail(sid, uid + 10000000)
        assert denied.value.status_code == 404
    asyncio.run(run_with_database(database_url, scenario))


def test_skill_options_api_authentication_and_actual_freeze(database_url):
    import asyncio
    import httpx
    from test_m4_interview import seed, run_with_database
    from interview_api.main import app
    from interview_api.api.deps import get_current_user
    from interview_api.modules.interview import router as routes

    async def scenario(factory):
        core, _, _, uid, old = await seed(factory)
        ctx = old["context_snapshot"]
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                assert (await client.get("/api/v1/interview/interviewer-options")).status_code == 401
                app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid, role="USER")
                with patch.object(routes, "build_core", return_value=core):
                    response = await client.get("/api/v1/interview/interviewer-options")
                    assert response.status_code == 200
                    assert len(response.json()["data"]["skills"]) == 3
                    response = await client.post("/api/v1/interview/sessions", json=dict(company_id=ctx["company_id"],
                        job_id=ctx["job_id"], interviewer_skill_id="bank_it", interview_round="technical_screen", interviewer_style="gentle"))
                    assert response.status_code == 201
                    frozen = response.json()["data"]["context_snapshot"]["interviewer_config"]
                    assert frozen["skill"]["id"] == "bank_it" and frozen["style"]["id"] == "gentle"
        finally:
            app.dependency_overrides.clear()
    asyncio.run(run_with_database(database_url, scenario))
