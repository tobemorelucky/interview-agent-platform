"""The report payload must say which job a session belonged to.

The report page links to the training centre with the advised mode and knowledge
point. Without a job id in this payload the link silently dropped the position,
so the user landed on a page asking them to pick a job they had just come from —
found by operating the page, not by reading the type.
"""
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.modules.interview.models import InterviewSession
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview_report.service import InterviewReportService

from test_coach_harness import adaptive_completed
from test_job_target import create_target, seed_user


def run(database_url, scenario):
    async def main():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await scenario(factory)
        finally:
            await engine.dispose()
    asyncio.run(main())


async def session_payload(factory, session_id):
    service = InterviewReportService(factory, router=None)
    async with factory() as db:
        session = await db.get(InterviewSession, session_id)
        return (await service._session_detail(db, session))["session"]


def test_a_public_session_reports_its_job(database_url):
    async def scenario(factory):
        user_id, session_id, _ = await adaptive_completed(factory)
        session = await session_payload(factory, session_id)
        assert session["job_id"], "a public session must expose its job"
        assert session["company_id"]
        assert session["job_target_id"] is None
    run(database_url, scenario)


def test_a_private_session_reports_its_target_and_no_public_job(database_url):
    """A private JD has no job row, so the link must fall back to the target."""
    async def scenario(factory):
        from interview_api.infrastructure.models.router import (
            ANSWER_EVALUATION_ROUTE, INTERVIEW_MAIN_ROUTE, ModelRouter)
        from interview_api.modules.interview.core_service import MockInterviewService
        from v22_support import MemoryReplayProvider

        user_id = await seed_user(factory)
        target_id = await create_target(factory, user_id)
        provider = MemoryReplayProvider()
        core = MockInterviewService(factory, ModelRouter(
            {route: provider for route in (INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE)}))
        # `request()` builds a training request; the session itself is what matters.
        created = await core.create(user_id, CreateSessionRequest(
            job_target_id=target_id, interviewer_skill_id="java_backend",
            adaptive_interview=False, question_count=2))
        session = await session_payload(factory, created["id"])
        assert session["job_target_id"] == target_id
        assert session["job_target_version"] == 1
        assert session["job_id"] is None and session["company_id"] is None
    run(database_url, scenario)
