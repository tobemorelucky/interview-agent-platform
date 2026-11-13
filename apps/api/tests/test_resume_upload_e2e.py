"""Round 17: resume upload → MinIO → task body → persisted profile, for real.

Round 9 proved this chain with a real model; rounds 11–16 used a pre-seeded
profile. This file keeps the real app entry (`POST /api/v1/resumes/upload`), the
real object storage, the real database and the real task body
(`process_resume_async`, the function the Celery task calls). Only the model
boundary is replayed — and the replay reads the *uploaded* text, so every profile
fact still comes from persisted evidence.

The broker hop itself is stubbed here (no worker process inside pytest); the real
Celery dispatch → consume → persist run is done in the acceptance stack and
recorded in docs/96.
"""
import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.config import settings
from interview_api.core.security import create_access_token, hash_password
from interview_api.infrastructure.models.router import ModelRouter, RESUME_PROFILE_ROUTE
from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
from interview_api.modules.candidate_profile.models import (
    CandidateFactEvidence, CandidateProfile, CandidateProfileVersion, ResumeEvidence)
from interview_api.modules.candidate_profile.service import CandidateProfileService
from interview_api.modules.resume.models import Resume, ResumeReport
from interview_api.modules.users.models import User

LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "serve_offline_acceptance.py"
BUCKET = "interview-agent-r17-test"

# Clearly labelled synthetic fixture: no real person's data enters logs or MinIO.
RAW = """DEMO 合成候选人（测试用，非真实个人信息）
技能
Python、Redis、Kafka
项目经历
DEMO 订单系统：使用 Python 和 Redis 实现缓存与失败重试
工作经历
DEMO 示例公司 Python 开发
教育背景
DEMO 示例大学 计算机科学 本科
"""


def launcher():
    """Reuse the acceptance stack's replay providers instead of copying them."""
    spec = importlib.util.spec_from_file_location("r17_acceptance_launcher", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def body(database_url, scenario):
    async def main():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await scenario(factory)
        finally:
            await engine.dispose()
    asyncio.run(main())


async def make_user(factory, email):
    async with factory() as db, db.begin():
        user = User(email=email, password_hash=hash_password("R17-resume-pass"))
        db.add(user)
        await db.flush()
        return dict(id=user.id, email=user.email, headers={
            "Authorization": f"Bearer {create_access_token(user_id=user.id, role='USER')}"})


@pytest.fixture
def isolated_bucket():
    """A dedicated MinIO bucket, so acceptance objects never share production."""
    original = settings.minio_bucket
    settings.minio_bucket = BUCKET
    asyncio.run(MinioObjectStorageProvider().ensure_bucket(BUCKET))
    try:
        yield BUCKET
    finally:
        settings.minio_bucket = original


async def upload(client, user, filename="r17_synthetic_resume.txt"):
    response = await client.post("/api/v1/resumes/upload",
        files={"file": (filename, RAW.encode("utf-8"), "text/plain")},
        headers=user["headers"])
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def run_task(resume_id, task_id, *, provider=None):
    """The real Celery task body, with only the model boundary replaced."""
    from interview_api.modules.resume.processor import process_resume_async
    module = launcher()
    router = lambda: ModelRouter({RESUME_PROFILE_ROUTE: provider or module.ReplayProfile()})
    with patch("interview_api.infrastructure.llm.provider.OpenAICompatibleLLMProvider",
               module.ReplayResumeExtract), \
         patch("interview_api.modules.candidate_profile.agent.build_resume_profile_router", router):
        await process_resume_async(resume_id, task_id)


async def version_count(factory, user_id):
    async with factory() as db:
        return await db.scalar(select(func.count()).select_from(CandidateProfileVersion)
            .join(CandidateProfile, CandidateProfile.id == CandidateProfileVersion.candidate_profile_id)
            .where(CandidateProfile.user_id == user_id))


def test_upload_reaches_minio_and_a_profile_with_resume_evidence(database_url, app_engine, isolated_bucket):
    async def scenario(factory):
        await app_engine.reset()
        from interview_api.main import app
        owner = await make_user(factory, f"r17-resume-{uuid4().hex[:8]}@example.com")
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                with patch("interview_api.infrastructure.tasks.celery_client.dispatch_process_resume",
                           return_value="r17-task-1") as dispatched:
                    uploaded = await upload(client, owner)
                resume_id = uploaded["id"]
                dispatched.assert_called_once_with(resume_id)
                assert uploaded["status"] != "COMPLETED"      # nothing is faked as done

                # The bytes really landed in the isolated bucket under this user.
                async with factory() as db:
                    storage_key = (await db.get(Resume, resume_id)).storage_key
                assert storage_key.startswith(f"resumes/{owner['id']}/")
                stored = MinioObjectStorageProvider()
                assert await stored.download(bucket_name=BUCKET, object_key=storage_key) == RAW.encode()

                await run_task(resume_id, "r17-task-1")

                async with factory() as db:
                    resume = await db.get(Resume, resume_id)
                    report = await db.scalar(select(ResumeReport).where(ResumeReport.resume_id == resume_id))
                assert resume.status == "COMPLETED" and resume.raw_text
                assert report is not None and report.summary_json

                async with factory() as db:
                    profile = await CandidateProfileService(db).serialize(owner["id"])
                assert profile and profile["producer"] == "REPLAY_FIXTURE"
                assert profile["version_number"] == 1
                normalized = profile["normalized"]
                assert normalized["skills"] and normalized["projects"] and normalized["education"]

                # Every fact is backed by a persisted evidence row from *this* resume.
                async with factory() as db:
                    rows = (await db.execute(
                        select(ResumeEvidence.excerpt, ResumeEvidence.resume_id)
                        .join(CandidateFactEvidence,
                              CandidateFactEvidence.resume_evidence_id == ResumeEvidence.id)
                        .where(ResumeEvidence.resume_id == resume_id))).all()
                assert rows, "profile facts must cite resume evidence"
                assert all(row.excerpt and row.excerpt in RAW for row in rows)
        finally:
            await app_engine.reset()
    body(database_url, scenario)


def test_redelivery_and_a_stale_task_do_not_create_a_second_version(database_url, app_engine, isolated_bucket):
    async def scenario(factory):
        await app_engine.reset()
        from interview_api.main import app
        owner = await make_user(factory, f"r17-retry-{uuid4().hex[:8]}@example.com")
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                with patch("interview_api.infrastructure.tasks.celery_client.dispatch_process_resume",
                           return_value="r17-task-a"):
                    resume_id = (await upload(client, owner))["id"]
            # A redelivery of the same task must reuse the finished report/profile.
            await run_task(resume_id, "r17-task-a")
            await run_task(resume_id, "r17-task-a")
            # A task from an older attempt is stale and must not write anything.
            await run_task(resume_id, "r17-task-old")
            async with factory() as db:
                reports = await db.scalar(select(func.count()).select_from(ResumeReport)
                    .where(ResumeReport.resume_id == resume_id))
                evidence = await db.scalar(select(func.count()).select_from(ResumeEvidence)
                    .where(ResumeEvidence.resume_id == resume_id))
            assert reports == 1 and evidence and evidence > 0
            assert await version_count(factory, owner["id"]) == 1
        finally:
            await app_engine.reset()
    body(database_url, scenario)


def test_failure_then_retry_recovers_with_exactly_one_version(database_url, app_engine, isolated_bucket):
    class Boom:
        def __init__(self, *args, **kwargs): pass
        async def chat(self, messages, **kwargs): raise RuntimeError("synthetic extraction failure")

    async def scenario(factory):
        await app_engine.reset()
        from interview_api.main import app
        owner = await make_user(factory, f"r17-fail-{uuid4().hex[:8]}@example.com")
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                with patch("interview_api.infrastructure.tasks.celery_client.dispatch_process_resume",
                           return_value="r17-task-f"):
                    resume_id = (await upload(client, owner))["id"]
            with patch("interview_api.infrastructure.llm.provider.OpenAICompatibleLLMProvider", Boom):
                from interview_api.modules.resume.processor import process_resume_async
                with pytest.raises(RuntimeError):
                    await process_resume_async(resume_id, "r17-task-f")
            async with factory() as db:
                failed = await db.get(Resume, resume_id)
            assert failed.status == "FAILED" and failed.error_message
            assert await version_count(factory, owner["id"]) == 0
            # A real retry of the same task id is allowed to run again and succeeds.
            await run_task(resume_id, "r17-task-f")
            async with factory() as db:
                recovered = await db.get(Resume, resume_id)
            assert recovered.status == "COMPLETED"
            assert await version_count(factory, owner["id"]) == 1
        finally:
            await app_engine.reset()
    body(database_url, scenario)


def test_another_user_cannot_see_the_resume_or_the_profile(database_url, app_engine, isolated_bucket):
    async def scenario(factory):
        await app_engine.reset()
        from interview_api.main import app
        owner = await make_user(factory, f"r17-own-{uuid4().hex[:8]}@example.com")
        other = await make_user(factory, f"r17-out-{uuid4().hex[:8]}@example.com")
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                with patch("interview_api.infrastructure.tasks.celery_client.dispatch_process_resume",
                           return_value="r17-task-b"):
                    uploaded = await upload(client, owner)
                await run_task(uploaded["id"], "r17-task-b")

                denied = await client.get(f"/api/v1/resumes/{uploaded['id']}", headers=other["headers"])
                assert denied.status_code == 404, denied.text
                assert "DEMO 订单系统" not in denied.text
                listing = (await client.get("/api/v1/resumes", headers=other["headers"])).json()["data"]
                assert listing["total"] == 0
                profile = (await client.get("/api/v1/candidate-profile", headers=other["headers"])).json()["data"]
                assert profile["current"] is None
                # The owner still sees their own data.
                mine = await client.get(f"/api/v1/resumes/{uploaded['id']}", headers=owner["headers"])
                assert mine.status_code == 200 and mine.json()["data"]["status"] == "COMPLETED"
        finally:
            await app_engine.reset()
    body(database_url, scenario)
