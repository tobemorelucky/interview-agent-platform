"""The cross-process fence: an UNKNOWN answer attempt is never auto-resent.

Lease expiry proves that a process vanished, not that its request never left.
The API therefore refuses to replay the same answer until the user explicitly
accepts one resend; the conservative hold stays in place until then.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from interview_api.core.exceptions import AppError
from interview_api.modules.interview.controller import SessionController
from interview_api.modules.interview.models import InterviewSession

from test_m4_interview import queue_start, run_with_database, seed


async def opened_answer(factory, core, main, uid, sid, question_id):
    key = uuid4().hex
    async with factory() as db:
        answer, _, reused = await SessionController(db).prepare_answer(sid, uid, question_id, key, "本地回答")
        await db.commit()
        assert reused is False
        return answer.id, key


async def expire_lease(factory, sid, uid, answer_id, *, extra=None):
    async with factory() as db:
        session = await SessionController(db).owned(sid, uid, lock=True)
        plan = dict(session.interview_plan_json or {})
        plan["answer_processing"] = {**(plan.get("answer_processing") or {}), "answer_id": answer_id,
            "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()}
        if extra:
            plan.update(extra)
        session.interview_plan_json = plan
        await db.commit()


def test_an_interrupted_answer_with_an_inflight_call_refuses_a_resend(database_url):
    async def scenario(factory):
        core, main, _, uid, detail = await seed(factory)
        sid = detail["id"]
        started = await queue_start(core, main, uid, sid)
        answer_id, key = await opened_answer(factory, core, main, uid, sid, started["question_id"])
        await expire_lease(factory, sid, uid, answer_id, extra={
            "model_call_reservations": {"vanished": 4321}, "reserved_model_tokens": 4321})
        async with factory() as db:
            with pytest.raises(AppError) as failure:
                await SessionController(db).prepare_answer(sid, uid, started["question_id"], key, "本地回答")
        assert failure.value.code == "INTERVIEW_OUTCOME_UNKNOWN" and failure.value.status_code == 409
        async with factory() as db:
            session = await db.get(InterviewSession, sid)
        # The hold survives: nothing was refunded and no resend was admitted.
        assert session.interview_plan_json["model_call_reservations"] == {"vanished": 4321}
    asyncio.run(run_with_database(database_url, scenario))


def test_a_settled_but_unreceipted_call_blocks_the_same_answer(database_url):
    async def scenario(factory):
        core, main, _, uid, detail = await seed(factory)
        sid = detail["id"]
        started = await queue_start(core, main, uid, sid)
        answer_id, key = await opened_answer(factory, core, main, uid, sid, started["question_id"])
        async with factory() as db:
            controller = SessionController(db)
            session = await controller.owned(sid, uid, lock=True)
            await controller.reserve_model_call(sid, uid, session.state_version, 9000, "lost-call")
            await controller.settle_model_call(sid, uid, "lost-call", False, answer_id=answer_id)
            await db.commit()
            plan = await db.scalar(select(InterviewSession.interview_plan_json).where(InterviewSession.id == sid))
        assert plan["unknown_model_calls"]["lost-call"]["answer_id"] == answer_id
        await expire_lease(factory, sid, uid, answer_id)
        async with factory() as db:
            with pytest.raises(AppError) as failure:
                await SessionController(db).prepare_answer(sid, uid, started["question_id"], key, "本地回答")
        assert failure.value.code == "INTERVIEW_OUTCOME_UNKNOWN"
    asyncio.run(run_with_database(database_url, scenario))


def test_a_receipted_failure_still_allows_the_answer_to_be_retried(database_url):
    async def scenario(factory):
        core, main, _, uid, detail = await seed(factory)
        sid = detail["id"]
        started = await queue_start(core, main, uid, sid)
        answer_id, key = await opened_answer(factory, core, main, uid, sid, started["question_id"])
        async with factory() as db:
            controller = SessionController(db)
            session = await controller.owned(sid, uid, lock=True)
            await controller.reserve_model_call(sid, uid, session.state_version, 9000, "known-call")
            await controller.settle_model_call(sid, uid, "known-call", True, known_tokens=100,
                                               request_sent=True, answer_id=answer_id)
            await db.commit()
        await expire_lease(factory, sid, uid, answer_id)
        async with factory() as db:
            controller = SessionController(db)
            answer, _, reused = await controller.prepare_answer(sid, uid, started["question_id"], key, "本地回答")
            await db.commit()
        assert reused is False and answer.status == "RUNNING"
        async with factory() as db:
            session = await db.get(InterviewSession, sid)
        assert session.interview_plan_json["unknown_model_calls"] == {}
        assert session.interview_plan_json["model_call_reservations"] == {}
    asyncio.run(run_with_database(database_url, scenario))
