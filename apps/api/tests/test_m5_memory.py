"""Controlled Memory provenance, atomic decisions and pinned read-only inputs."""
import asyncio
from copy import deepcopy
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.modules.memory.models import UserMemoryEvent, UserMemoryItem, UserSkillProfile
from interview_api.modules.memory.proposal_models import MemoryProposal, MemoryProposalEvaluation
from interview_api.modules.memory.proposal_service import MemoryProposalService, validate_observation_content
from interview_api.modules.memory.repository import UserMemoryRepository
from interview_api.modules.memory.snapshot import _bounded, build_memory_snapshot
from interview_api.modules.memory.interview_writer import InterviewMemoryWriter
from test_m4_interview import run_with_database


def test_memory_snapshot_counts_full_serialization_and_exact_clipping():
    items = [dict(id=i, content="真实中文 evidence " * 200, updated_at="2026-10-02",
                  provenance={"actual_evaluation_ids": list(range(40))}) for i in range(5)]
    context = _bounded(items, limit=3, budget=1800, total=5, semantics="User confirmed",
                       epistemic_status="USER_CONFIRMED")
    actual = len(json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    assert context["stored_context_bytes"] == context["estimated_tokens"] == actual <= 1800
    assert context["items"][0]["content_truncated"]
    assert items[0]["content"].startswith(context["items"][0]["content"])
    assert context["omitted_count"] == 5 - context["selected_count"]
    tiny = _bounded(items, limit=3, budget=0, total=5, semantics="User confirmed", epistemic_status="USER_CONFIRMED")
    assert tiny["items"] == [] and tiny["omitted_count"] == 5
    assert tiny["budget_exceeded_by_fixed_overhead"]
    assert tiny["stored_context_bytes"] == len(json.dumps(tiny, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


@pytest.mark.parametrize("text", ["用户精通 Kafka", "用户不会 RAG", "候选人不具备系统设计能力", "User cannot use Redis"])
def test_absolute_stable_skill_proposal_rejected(text):
    with pytest.raises(AppError) as error:
        validate_observation_content(text)
    assert error.value.code == "ABSOLUTE_SKILL_MEMORY_REJECTED"
    assert validate_observation_content("本场 RAG 回答 coverage 较低，建议准备评测示例")


def test_legacy_consolidation_cannot_bypass_proposals():
    async def run():
        writer = InterviewMemoryWriter(None)
        async def session(*_):
            from types import SimpleNamespace
            return SimpleNamespace(workflow_mode="m4")
        writer._get_session_for_user = session
        with pytest.raises(AppError) as error:
            await writer.consolidate_interview_session(1, 1)
        assert error.value.code == "CONTROLLED_MEMORY_REQUIRED" and error.value.status_code == 409
    asyncio.run(run())


async def _seed(factory):
    from test_m5_report import seeded_report
    _, user_id, session_id, report, _ = await seeded_report(factory)
    async with factory() as db:
        proposals = (await db.scalars(select(MemoryProposal).where(MemoryProposal.report_id == report["id"]).order_by(MemoryProposal.id))).all()
        assert len(proposals) >= 3
        assert all(p.status == "PENDING" and p.memory_item_id is None for p in proposals)
        assert not await db.scalar(select(func.count(UserMemoryItem.id)).where(UserMemoryItem.user_id == user_id))
        for proposal in proposals:
            assert list((await db.scalars(select(MemoryProposalEvaluation.evaluation_id).where(MemoryProposalEvaluation.proposal_id == proposal.id).order_by(MemoryProposalEvaluation.evaluation_id))).all()) == proposal.supporting_evaluation_ids
    return user_id, session_id, report, [p.id for p in proposals]


def test_memory_decisions_provenance_and_no_auto_commit(database_url):
    async def run(factory):
        user_id, _, report, ids = await _seed(factory)
        results = []
        for id_, decision in zip(ids, ("APPROVED", "CORRECTED", "REJECTED")):
            async with factory() as db:
                service = MemoryProposalService(db)
                result = await service.decide(user_id, id_, decision,
                    content="本場缓存题需要补充并发失效例子" if decision == "CORRECTED" else None,
                    reason="用户逐条确认" if decision == "CORRECTED" else None)
                await db.commit()
                assert result == await service.decide(user_id, id_, decision,
                    content=result["corrected_content"], reason=result["confirmation_reason"])
                results.append(result)
        assert results[0]["status"] == "APPROVED" and results[1]["status"] == "CORRECTED"
        assert results[1]["original_content"] != results[1]["corrected_content"]
        assert results[1]["changed_by"] == "user" and results[1]["confirmed_by"] == user_id
        assert results[1]["corrected_at"] and results[1]["confirmed_at"]
        assert results[2]["status"] == "REJECTED" and results[2]["memory_item_id"] is None
        async with factory() as db:
            items = (await db.scalars(select(UserMemoryItem).where(UserMemoryItem.user_id == user_id))).all()
            assert len(items) == 2 and all(m.source_type == "MEMORY_PROPOSAL" for m in items)
            assert all(m.metadata_json["report_id"] == report["id"] and m.metadata_json["user_confirmed"] for m in items)
            events = (await db.scalars(select(UserMemoryEvent).where(UserMemoryEvent.user_id == user_id))).all()
            assert len(events) == 2 and all(e.actor_type == "USER" and e.actor_id == user_id for e in events)
            assert all(e.before_json["original_content"] and e.after_json["provenance"]["supporting_evaluation_ids"] for e in events)
            assert not await db.scalar(select(func.count(UserSkillProfile.id)).where(UserSkillProfile.user_id == user_id))
            with pytest.raises(AppError) as conflict:
                await MemoryProposalService(db).decide(user_id, ids[0], "REJECTED")
            assert conflict.value.status_code == 409
            with pytest.raises(AppError) as missing:
                await MemoryProposalService(db).decide(user_id + 100000, ids[0], "APPROVED")
            assert missing.value.status_code == 404
    asyncio.run(run_with_database(database_url, run))


@pytest.mark.parametrize("direction", ["memory_then_proposal_failure", "proposal_then_memory_failure"])
def test_proposal_memory_atomic_fault_injection(database_url, direction):
    async def run(factory):
        user_id, _, _, ids = await _seed(factory)
        if direction == "proposal_then_memory_failure":
            # Prove the database itself forbids a terminal approval without its
            # Memory link, rather than weakening constraints to test reverse order.
            async with factory() as db:
                proposal = await db.get(MemoryProposal, ids[0])
                proposal.status = "APPROVED"
                proposal.confirmed_by = user_id
                proposal.confirmed_at = datetime.now(timezone.utc)
                with pytest.raises(DBAPIError) as invariant:
                    await db.flush()
                assert "ck_memory_proposal_approved_memory" in str(invariant.value)
                await db.rollback()
        async with factory() as db:
            if direction == "memory_then_proposal_failure":
                original_flush = db.flush
                injected = []
                async def fail_final_proposal_flush(*args, **kwargs):
                    final_proposal = next((p for p in db.dirty if isinstance(p, MemoryProposal)
                        and p.id == ids[0] and p.status == "APPROVED" and p.memory_item_id is not None), None)
                    if final_proposal is not None:
                        # Verify BOTH prior writes really reached PostgreSQL in
                        # this transaction, not merely Session.add() buffers.
                        with db.no_autoflush:
                            assert await db.scalar(select(func.count(UserMemoryItem.id)).where(UserMemoryItem.user_id == user_id)) == 1
                            assert await db.scalar(select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == user_id)) == 1
                        injected.append(final_proposal.id)
                        raise RuntimeError("fault at final APPROVED Proposal flush after item and event persisted")
                    return await original_flush(*args, **kwargs)
                with patch.object(db, "flush", fail_final_proposal_flush):
                    with pytest.raises(RuntimeError, match="final APPROVED Proposal flush"):
                        await MemoryProposalService(db).decide(user_id, ids[0], "APPROVED")
                assert injected == [ids[0]]
            else:
                # Deliberately reorder transactional primitives for the opposite fault.
                # There is no production option that changes the normal decision order.
                proposal = await db.scalar(select(MemoryProposal).where(MemoryProposal.id == ids[0]).with_for_update())
                # The DB intentionally disallows terminal APPROVED without Memory;
                # tentative confirmation changes still must roll back on a later fault.
                proposal.confirmed_by = user_id
                proposal.confirmed_at = datetime.now(timezone.utc)
                proposal.changed_by = "user"
                await db.flush()
                async def fail(*_args, **_kwargs):
                    raise RuntimeError("fault after Proposal flush, before Memory write")
                with patch.object(UserMemoryRepository, "create_memory_item", fail):
                    with pytest.raises(RuntimeError):
                        await UserMemoryRepository(db).create_memory_item(UserMemoryItem(user_id=user_id,
                            memory_type="EPISODIC", content="本场表现", key="fault"))
            await db.rollback()
        async with factory() as db:
            proposal = await db.get(MemoryProposal, ids[0])
            assert proposal.status == "PENDING" and proposal.memory_item_id is None and proposal.confirmed_by is None
            assert not await db.scalar(select(func.count(UserMemoryItem.id)).where(UserMemoryItem.user_id == user_id))
            assert not await db.scalar(select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == user_id))
    asyncio.run(run_with_database(database_url, run))


def test_concurrent_approval_has_one_memory_and_one_confirmation(database_url):
    async def run(factory):
        user_id, _, _, ids = await _seed(factory)
        async def approve():
            async with factory() as db:
                result = await MemoryProposalService(db).decide(user_id, ids[0], "APPROVED")
                await db.commit()
                return result
        a, b = await asyncio.gather(approve(), approve())
        assert a == b
        async with factory() as db:
            assert await db.scalar(select(func.count(UserMemoryItem.id)).where(UserMemoryItem.user_id == user_id)) == 1
            assert await db.scalar(select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == user_id)) == 1
    asyncio.run(run_with_database(database_url, run))


def test_memory_snapshot_read_only_confirmation_filter_and_pin(database_url):
    async def run(factory):
        user_id, _, report, ids = await _seed(factory)
        async with factory() as db:
            await MemoryProposalService(db).decide(user_id, ids[0], "APPROVED")
            # READ by USER does not approve automatically generated content.
            for marker, actor, expired in (("READ_ONLY_SYSTEM", "SYSTEM", False), ("EXPIRED_USER", "USER", True), ("MANUAL_USER", "USER", False)):
                item = UserMemoryItem(user_id=user_id, memory_type="PREFERENCE", content=marker,
                    scope="INTERVIEW", expires_at=datetime.now(timezone.utc) - timedelta(days=1) if expired else None)
                db.add(item)
                await db.flush()
                db.add(UserMemoryEvent(user_id=user_id, memory_item_id=item.id, event_type="CREATED", actor_type=actor,
                    actor_id=user_id if actor == "USER" else None))
                db.add(UserMemoryEvent(user_id=user_id, memory_item_id=item.id, event_type="READ", actor_type="USER", actor_id=user_id))
            await db.commit()
        with patch.object(settings, "interview_approved_memory_token_budget", 20000), patch.object(settings, "interview_observation_token_budget", 20000):
            async with factory() as db:
                before = await db.scalar(select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == user_id))
                accesses = (await db.execute(select(UserMemoryItem.id, UserMemoryItem.last_accessed_at).where(UserMemoryItem.user_id == user_id))).all()
                context = await build_memory_snapshot(db, user_id)
                assert context == await build_memory_snapshot(db, user_id)
                assert before == await db.scalar(select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == user_id))
                assert accesses == (await db.execute(select(UserMemoryItem.id, UserMemoryItem.last_accessed_at).where(UserMemoryItem.user_id == user_id))).all()
                approved = context["approved_memory_context"]
                texts = [m["content"] for m in approved["items"]]
                assert "MANUAL_USER" in texts and "READ_ONLY_SYSTEM" not in texts and "EXPIRED_USER" not in texts
                assert all(m["provenance"]["confirmed_by"] == user_id and m["provenance"]["confirmation_event_id"] for m in approved["items"])
                observations = context["recent_performance_observations"]
                assert observations["items"] and all(o["report_id"] == report["id"] for o in observations["items"])
                assert not observations["is_stable_fact"] and "NOT_USER_CONFIRMED" in observations["epistemic_status"]
                for value in context.values():
                    assert value["stored_context_bytes"] == len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) <= value["budgets"]["token_budget"]
            # Integrate actual second InterviewContextSnapshot, not just copying dicts.
            from interview_api.modules.interview.core_service import MockInterviewService
            from interview_api.modules.interview.schemas import CreateSessionRequest
            from interview_api.modules.interview.models import InterviewContextSnapshot, InterviewSession
            async with factory() as db:
                first = await db.get(InterviewSession, report["session_id"])
                snapshot = await db.get(InterviewContextSnapshot, first.context_snapshot_id)
                request = CreateSessionRequest(company_id=snapshot.company_id, job_id=snapshot.job_id, question_count=2)
            core = MockInterviewService(factory)
            second = await core.create(user_id, request)
            pinned = deepcopy(second["context_snapshot"])
            assert pinned["approved_memory_context"]["items"]
            async with factory() as db:
                await db.execute(update(UserMemoryItem).where(UserMemoryItem.user_id == user_id).values(content="修改发生在第二场创建后"))
                await db.commit()
            assert (await core.detail(second["id"], user_id))["context_snapshot"] == pinned
    asyncio.run(run_with_database(database_url, run))


def test_proposal_api_ownership_and_decisions(database_url):
    async def run(factory):
        import httpx
        from types import SimpleNamespace
        from interview_api.main import app
        from interview_api.api.deps import get_current_user
        from interview_api.infrastructure.db.session import get_db
        from interview_api.core.rate_limit import memory_write_limit
        user_id, session_id, report, ids = await _seed(factory)
        actor = user_id
        async def user_override():
            return SimpleNamespace(id=actor, role="USER")
        async def db_override():
            async with factory() as db:
                try:
                    yield db
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
        async def no_limit():
            return None
        app.dependency_overrides[get_current_user] = user_override
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[memory_write_limit] = no_limit
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                listed = await client.get("/api/v1/memory-proposals", params={"report_id": report["id"]})
                assert listed.status_code == 200 and listed.json()["data"]["total"] >= 3
                response = await client.post(f"/api/v1/memory-proposals/{ids[0]}/approve", json={})
                assert response.status_code == 200 and response.json()["data"]["memory_item_id"]
                assert response.json()["data"] == (await client.post(f"/api/v1/memory-proposals/{ids[0]}/approve", json={})).json()["data"]
                corrected = await client.post(f"/api/v1/memory-proposals/{ids[1]}/correct", json={"content": "本场缓存回答需要补充失效策略", "reason": "纠正观察"})
                assert corrected.status_code == 200 and corrected.json()["data"]["status"] == "CORRECTED"
                rejected = await client.post(f"/api/v1/memory-proposals/{ids[2]}/reject", json={"reason": "不写入"})
                assert rejected.status_code == 200 and rejected.json()["data"]["memory_item_id"] is None
                assert (await client.post(f"/api/v1/memory-proposals/{ids[0]}/reject", json={})).status_code == 409
                assert (await client.post(f"/api/v1/memory-proposals/{ids[1]}/correct", json={"content": "", "reason": ""})).status_code == 422
                assert (await client.post(f"/api/v1/interview/sessions/{session_id}/memory/consolidate", json={"force": False})).status_code == 409
                actor = user_id + 100000
                assert (await client.get("/api/v1/memory-proposals")).json()["data"]["items"] == []
                assert (await client.post(f"/api/v1/memory-proposals/{ids[0]}/approve", json={})).status_code == 404
        finally:
            app.dependency_overrides.clear()
    asyncio.run(run_with_database(database_url, run))
