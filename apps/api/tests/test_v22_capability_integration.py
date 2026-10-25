"""Real PostgreSQL transactions and service APIs, fixed Replay only."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.memory.capability_service import CapabilityMemoryService
from interview_api.modules.memory.capability_models import CapabilityMemoryVersion
from interview_api.core.exceptions import AppError
from v22_support import MemoryReplayProvider, kafka_seed


def test_three_session_memory_replay_persistence_recompute_and_privacy(database_url):
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = MemoryReplayProvider()
            core = MockInterviewService(factory, ModelRouter({r: provider for r in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE)}))
            async def session(answers, *, round_id="technical_screen"):
                detail = await core.create(uid, CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"],
                    interviewer_skill_id="java_backend", adaptive_interview=True, question_count=len(answers), interview_round=round_id))
                pinned = deepcopy(detail["context_snapshot"])
                await core.start(detail["id"], uid)
                for text in answers:
                    q = (await core.detail(detail["id"], uid))["current_question"]
                    key = uuid4().hex
                    events = await core.answer(detail["id"], uid, q["question_id"], key, text)
                    assert events == await core.answer(detail["id"], uid, q["question_id"], key, text)
                final = await core.detail(detail["id"], uid)
                assert final["status"] == "COMPLETED" and final["context_snapshot"] == pinned
                return final
            async def memory():
                async with factory() as db:
                    return next(r for r in await CapabilityMemoryService(db).list(uid) if r["topic"] == "kafka_reliability")
            first = await session(["Kafka 成功后 ACK，失败重试"])
            assert (await memory())["state"] == "GAP_SUSPECTED"
            second = await session(["Kafka 成功后 ACK，失败重试", "业务唯一键去重与事务提交后 ACK，处理失败恢复"])
            root = await memory()
            assert root["state"] == "PRACTICING" and root["independent_sessions"] == 0
            assert second["context_snapshot"]["memory_guided_plan"]["items"]
            third = await session(["Kafka 业务唯一键和事务原子提交后 ACK，重试保证幂等"], round_id="project_deep_dive")
            assert third["questions"][0]["evidence"]["adaptive_decision"]["target_knowledge_id"].endswith(":kafka_reliability")
            root = await memory()
            assert root["state"] == "PRACTICING" and root["independent_sessions"] == 1
            await session(["Kafka 事务与唯一键处理重复消费，落库后 ACK"])
            root = await memory()
            assert root["state"] == "VERIFIED" and root["independent_sessions"] == 2
            fresh = await core.create(uid, CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"], question_count=1,
                interviewer_skill_id="java_backend", adaptive_interview=True))
            assert fresh["context_snapshot"]["adaptive_blueprint"]["targets"][0]["topic"] != "kafka_reliability"
            await core.start(fresh["id"], uid)
            fresh_question = (await core.detail(fresh["id"], uid))["questions"][0]
            assert not fresh_question["evidence"]["adaptive_decision"]["target_knowledge_id"].endswith(":kafka_reliability")
            # Rebuild, concurrency and report generation do not accumulate another observation.
            async def refresh():
                async with factory() as db:
                    await CapabilityMemoryService(db).refresh(uid)
                    await db.commit()
            await asyncio.gather(refresh(), refresh())
            assert (await memory())["revision"] == root["revision"]
            from interview_api.modules.interview_report.service import InterviewReportService
            reports = InterviewReportService(factory, core.model_router) if hasattr(core, "model_router") else InterviewReportService(factory,
                ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
            await reports.generate(third["id"], uid)
            await reports.generate(third["id"], uid, regenerate=True, request_key="v22-regenerate-" + uuid4().hex)
            await refresh()
            assert (await memory())["revision"] == root["revision"]
            async with factory() as db:
                clock = lambda: datetime.now(timezone.utc) + timedelta(days=91)
                assert next(r for r in await CapabilityMemoryService(db, clock=clock).list(uid) if r["topic"] == root["topic"])["state"] == "RECHECK_DUE"
                assert await CapabilityMemoryService(db).list(uid + 999999) == []
                with pytest.raises(AppError) as wrong:
                    await CapabilityMemoryService(db).correct(uid + 999999, root["id"], expected_revision=root["revision"], excluded=[], note="not mine")
                assert wrong.value.status_code == 404
                await db.rollback()
            async with factory() as db:
                await CapabilityMemoryService(db).correct(uid, root["id"], expected_revision=root["revision"], excluded=[], note="撤销系统推断", revoke=True)
                await db.commit()
            await refresh()
            revoked = await memory()
            assert revoked["revoked"] and revoked["state"] == "UNKNOWN"
            async with factory() as db:
                plan = await CapabilityMemoryService(db).plan(uid, first["context_snapshot"]["adaptive_blueprint"])
                assert not plan["items"]
                assert (await core.detail(first["id"], uid))["context_snapshot"] == first["context_snapshot"]
                # A failed transaction leaves no published version or suppressed state.
                count = await db.scalar(select(func.count()).select_from(CapabilityMemoryVersion))
                await db.rollback()
                await CapabilityMemoryService(db).correct(uid, root["id"], expected_revision=revoked["revision"], excluded=[], note="will rollback")
                await db.rollback()
                assert await db.scalar(select(func.count()).select_from(CapabilityMemoryVersion)) == count
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_memory_is_planning_only_and_prompt_compatibility():
    from interview_api.modules.interview.skill_engine import agent_payload
    from interview_api.modules.interview.adaptive_prompts import freeze_prompts
    payload = dict(snapshot=dict(memory_guided_plan=dict(items=[dict(state="CONFLICTED")]),
        approved_memory_context=dict(items=["old weakness"])), answer="当前答案")
    assert "memory_guided_plan" not in agent_payload(payload, "answer_evaluation")["snapshot"]
    assert "memory_guided_plan" in agent_payload(payload, "interview_main")["snapshot"]
    payload["snapshot"]["recent_performance_observations"] = dict(items=["revoked historical weakness"])
    assert "recent_performance_observations" not in agent_payload(payload, "interview_main")["snapshot"]
    assert freeze_prompts(contract="TARGET_SOURCE_PAIR_V1")["interview_main"]["version"] == "2.1.1"
    assert freeze_prompts(contract="TARGET_SOURCE_PAIR_V1", memory_guided=True)["interview_main"]["version"] == "2.2.0"


def test_replay_memory_actually_changes_choice_but_cannot_grant_source_authority():
    import json
    from test_v211_director_contract import packet
    from interview_api.modules.interview.adaptive import AdaptiveSelection, resolve_selection, validate_decision
    payload = packet()
    kafka = next(c for c in payload["adaptive"]["candidates"] if c["target_knowledge_id"].endswith(":kafka_reliability"))
    other = next(c for c in payload["adaptive"]["candidates"] if not c["target_knowledge_id"].endswith(":kafka_reliability"))
    payload["adaptive"]["candidates"] = [other, kafka]
    async def choose(value):
        model_input = deepcopy(value)
        model_input["adaptive"].pop("choices")  # Like InterviewAgent's actual model-facing projection.
        result = await MemoryReplayProvider().generate([dict(role="user", content=json.dumps(dict(input=model_input)))],
            schema=AdaptiveSelection.model_json_schema())
        return resolve_selection(AdaptiveSelection.model_validate_json(result.text), value)
    plain = asyncio.run(choose(payload))
    assert plain.target_knowledge_id == other["target_knowledge_id"]
    payload["snapshot"]["memory_guided_plan"] = dict(items=[dict(knowledge_id=kafka["target_knowledge_id"])])
    remembered = asyncio.run(choose(payload))
    assert remembered.target_knowledge_id == kafka["target_knowledge_id"]
    assert "支付回调" in remembered.question.question
    validate_decision(remembered, payload)
    assert remembered.question_source_ref == kafka["source"]["ref"]


def test_capability_api_correction_exclusion_ownership_and_concurrent_version(database_url):
    async def run():
        import httpx
        from types import SimpleNamespace
        from interview_api.main import app
        from interview_api.api.deps import get_current_user
        from interview_api.infrastructure.db.session import get_db
        from interview_api.core.rate_limit import memory_write_limit
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        uid, ctx = await kafka_seed(factory)
        provider = MemoryReplayProvider()
        core = MockInterviewService(factory, ModelRouter({r: provider for r in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))
        created = await core.create(uid, CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"], question_count=1,
            interviewer_skill_id="java_backend", adaptive_interview=True))
        q = await core.start(created["id"], uid)
        await core.answer(created["id"], uid, q["question_id"], uuid4().hex, "Kafka 失败重试")
        actor = uid
        async def user(): return SimpleNamespace(id=actor, role="USER")
        async def database():
            async with factory() as db: yield db
        async def no_limit(): return None
        previous = dict(app.dependency_overrides)
        app.dependency_overrides.update({get_current_user: user, get_db: database, memory_write_limit: no_limit})
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                root = (await client.get("/api/v1/memory/capabilities")).json()["data"]["items"][0]
                assert root["state"] == "GAP_SUSPECTED"
                body = dict(expected_revision=root["revision"], excluded_evaluation_ids=[root["observations"][0]["evaluation_id"]],
                    note="本次录入回答不完整，请排除该条观察", revoke=False)
                url = f"/api/v1/memory/capabilities/{root['id']}"
                a, b = await asyncio.gather(client.patch(url, json=body), client.patch(url, json=body))
                assert sorted([a.status_code, b.status_code]) == [200, 409]
                async with factory() as db:
                    await CapabilityMemoryService(db).refresh(uid)
                    await db.commit()
                after = (await client.get("/api/v1/memory/capabilities")).json()["data"]["items"][0]
                assert after["state"] == "UNKNOWN" and not after["observations"]
                actor = uid + 999999
                assert (await client.get("/api/v1/memory/capabilities")).json()["data"]["items"] == []
                assert (await client.patch(url, json={**body, "expected_revision": after["revision"]})).status_code == 404
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
            await engine.dispose()
    asyncio.run(run())


def test_skip_completion_still_integrates_valid_evaluations_and_failed_write_rolls_back(database_url):
    async def run():
        from unittest.mock import patch
        from interview_api.modules.interview.models import InterviewEvaluation, InterviewSession
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            p = MemoryReplayProvider()
            core = MockInterviewService(factory, ModelRouter({r: p for r in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))
            first = await core.create(uid, CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"], question_count=2,
                interviewer_skill_id="java_backend", adaptive_interview=True))
            q = await core.start(first["id"], uid)
            await core.answer(first["id"], uid, q["question_id"], uuid4().hex, "Kafka 重试后 ACK")
            active = (await core.detail(first["id"], uid))["current_question"]
            await core.skip(first["id"], uid, active["question_id"])
            async with factory() as db:
                assert (await CapabilityMemoryService(db).list(uid))[0]["state"] == "GAP_SUSPECTED"
            last = await core.create(uid, CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"], question_count=1,
                interviewer_skill_id="java_backend", adaptive_interview=True))
            q = await core.start(last["id"], uid)
            key = uuid4().hex
            async def fail(*a, **k): raise RuntimeError("DEMO capability projection write failed")
            with patch.object(CapabilityMemoryService, "refresh", fail):
                with pytest.raises(Exception): await core.answer(last["id"], uid, q["question_id"], key, "Kafka 事务与唯一键提交后 ACK")
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(InterviewEvaluation).where(InterviewEvaluation.session_id == last["id"])) == 0
                assert (await db.get(InterviewSession, last["id"])).status == "IN_PROGRESS"
            await core.answer(last["id"], uid, q["question_id"], key, "Kafka 事务与唯一键提交后 ACK")
            assert (await core.detail(last["id"], uid))["status"] == "COMPLETED"
        finally:
            await engine.dispose()
    asyncio.run(run())
