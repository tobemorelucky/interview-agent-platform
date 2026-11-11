"""Real PostgreSQL and real HTTP auth, offline model only. No paid requests."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch
import httpx
import pytest
from sqlalchemy import select, func, update

from interview_api.core.errors import AppError
from interview_api.infrastructure.models.governance import ModelGovernance, model_scope, governed_call, provider_guard, run_owner
from interview_api.infrastructure.models.governance_models import ModelAdmission
from interview_api.infrastructure.models.provider import CompatibleModelProvider
from interview_api.modules.public_career.library import PublicCareerLibrary
from interview_api.modules.public_career.models import PublicDataRefreshTask, JobDescriptionVersion
from interview_api.modules.interview.schemas import CreateSessionRequest
from test_m4_interview import seed, run_with_database


def test_shared_jd_private_sessions_and_version_pinning(database_url):
    async def scenario(factory):
        core, _, _, a, first = await seed(factory)
        _, _, _, b, _ = await seed(factory)
        shared = first["context_snapshot"]
        request = CreateSessionRequest(company_id=shared["company_id"], job_id=shared["job_id"], question_count=3)
        second = await core.create(b, request)
        assert second["context_snapshot"]["job_description_version_id"] == shared["job_description_version_id"]
        before = deepcopy(second["context_snapshot"])
        from interview_api.core.exceptions import AppError as DomainError
        with pytest.raises(DomainError) as exc:
            await core.detail(second["id"], a)
        assert exc.value.status_code == 404
        async with factory() as db:
            db.add(JobDescriptionVersion(job_id=shared["job_id"], version_number=2,
                raw_text="DEMO public JD revised", publication_status="PUBLISHED"))
            await db.commit()
            jd = await PublicCareerLibrary(db).job(shared["job_id"])
            assert jd["version"] == 2
        assert (await core.detail(second["id"], b))["context_snapshot"] == before
        assert (await core.detail(first["id"], a))["context_snapshot"] == shared
    asyncio.run(run_with_database(database_url, scenario))


def test_concurrent_source_requests_do_not_fetch_or_duplicate(database_url):
    async def scenario(factory):
        url = "https://example.com/public/" + uuid4().hex
        async def request(raw):
            async with factory() as db:
                return await PublicCareerLibrary(db).request_source(raw, "job_jd")
        a, b = await asyncio.gather(request(url), request(url + "?utm_source=DEMO"))
        assert a["task_id"] == b["task_id"]
        async with factory() as db:
            task = await db.get(PublicDataRefreshTask, a["task_id"])
            assert task.status == "PENDING" and task.target_type == "SOURCE_REQUEST"
            assert "ADMIN_REQUIRED" in task.reason
            from interview_api.modules.public_career.tool_models import ToolInvocation
            assert await db.scalar(select(func.count()).select_from(ToolInvocation).where(ToolInvocation.refresh_task_id == task.id)) == 0
        async with factory() as db:
            with pytest.raises(Exception) as exc:
                await PublicCareerLibrary(db).request_source("http://127.0.0.1/private", "job_jd")
            assert exc.value.status_code == 422
    asyncio.run(run_with_database(database_url, scenario))


def test_unpublished_and_withdrawn_public_data_hidden(database_url):
    async def scenario(factory):
        _, _, _, _, detail = await seed(factory)
        snap = detail["context_snapshot"]
        from interview_api.modules.experience.models import InterviewExperience
        async with factory() as db:
            db.add(JobDescriptionVersion(job_id=snap["job_id"], version_number=2,
                raw_text="UNREVIEWED private draft", publication_status="WAITING_REVIEW"))
            bad = InterviewExperience(company="UNREVIEWED", summary="SECRET", review_status="PENDING")
            db.add(bad)
            await db.commit()
            assert (await PublicCareerLibrary(db).job(snap["job_id"]))["version"] == 1
            with pytest.raises(Exception) as exc:
                await PublicCareerLibrary(db).experiences(experience_id=bad.id)
            assert exc.value.status_code == 404
            await db.execute(update(JobDescriptionVersion).where(JobDescriptionVersion.id == snap["job_description_version_id"]).values(publication_status="WITHDRAWN"))
            await db.commit()
            with pytest.raises(Exception) as exc:
                await PublicCareerLibrary(db).job(snap["job_id"])
            assert exc.value.status_code == 404
    asyncio.run(run_with_database(database_url, scenario))


def test_quota_concurrent_admission_and_exactly_once_settlement(database_url):
    async def scenario(factory):
        governance = ModelGovernance(factory, calls=5, tokens=500, concurrency=1)
        owner = "test:" + uuid4().hex
        async def reserve(key):
            try:
                return await governance.reserve(owner, route="test", model="offline", tokens=200, timeout=10, call_key=key)
            except AppError as exc:
                return exc
        results = await asyncio.gather(reserve(uuid4().hex), reserve(uuid4().hex))
        assert sum(isinstance(x, str) for x in results) == 1
        rejected = next(x for x in results if isinstance(x, AppError))
        assert rejected.status_code == 429 and rejected.code == "MODEL_USER_CONCURRENCY"
        key = next(x for x in results if isinstance(x, str))
        await governance.settle(key, {"total_tokens": 30})
        await governance.settle(key, {"total_tokens": 1})
        async with factory() as db:
            row = await db.get(ModelAdmission, key)
            assert row.charged_tokens == 30 and row.status == "SETTLED"
        duplicate = await reserve(key)
        assert duplicate.status_code == 409
        second = await reserve(uuid4().hex)
        await governance.settle(second)  # Timeout: no provider usage, retain 200.
        with pytest.raises(AppError) as exc:
            await governance.reserve(owner, route="test", model="offline", tokens=300, timeout=1)
        assert exc.value.code == "MODEL_USER_QUOTA"
    asyncio.run(run_with_database(database_url, scenario))


def test_expired_reservation_and_late_usage_fenced(database_url):
    async def scenario(factory):
        g = ModelGovernance(factory, calls=2, tokens=1000, concurrency=1)
        owner = "test:" + uuid4().hex
        key = await g.reserve(owner, route="test", model="offline", tokens=200, timeout=1)
        async with factory() as db:
            await db.execute(update(ModelAdmission).where(ModelAdmission.call_key == key).values(expires_at=func.now() - timedelta(seconds=1)))
            await db.commit()
        next_key = await g.reserve(owner, route="test", model="offline", tokens=200, timeout=1)
        await g.settle(key, {"total_tokens": 1})
        async with factory() as db:
            row = await db.get(ModelAdmission, key)
            assert row.status == "UNKNOWN" and row.charged_tokens == 200
        await g.settle(next_key, {"total_tokens": 1})
        with pytest.raises(AppError) as exc:
            await g.reserve(owner, route="test", model="offline", tokens=1, timeout=1)
        assert exc.value.code == "MODEL_USER_QUOTA"  # daily call cap across requests
    asyncio.run(run_with_database(database_url, scenario))


def test_actual_http_calls_go_through_quota_without_network(database_url):
    async def scenario(factory):
        owner = "test:" + uuid4().hex
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = CompatibleModelProvider(base_url="https://offline.invalid", api_key="TEST", model="offline",
                timeout_seconds=2, max_output_tokens=10, client=client, governed=True)
            token = model_scope.set((factory, owner, None, "test"))
            try:
                await p.generate([dict(role="user", content="test")], schema={})
            finally:
                model_scope.reset(token)
        async with factory() as db:
            row = await db.scalar(select(ModelAdmission).where(ModelAdmission.owner_key == owner))
            assert row.status == "SETTLED" and row.actual_tokens == 10 and len(calls) == 1
    asyncio.run(run_with_database(database_url, scenario))


@pytest.mark.parametrize("phase", ["before_send", "after_send", "response_before_parse", "settlement"])
def test_interrupted_http_budget_recovers_without_resending(database_url, phase):
    async def scenario(factory):
        owner = "test:" + uuid4().hex
        outbound = []
        parsed = asyncio.Event()
        db_unavailable = False

        def available_factory():
            if db_unavailable:
                raise ConnectionError("injected database outage during settlement")
            return factory()

        def handler(request):
            outbound.append(request)
            if phase == "after_send":
                raise httpx.ReadError("injected lost response", request=request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})

        async def response_hook(response):
            nonlocal db_unavailable
            if phase == "response_before_parse":
                raise httpx.ReadError("injected interruption after HTTP headers", request=response.request)
            if phase == "settlement":
                db_unavailable = True

        class BeforeSend:
            async def __aenter__(self):
                parsed.set()
                await asyncio.Event().wait()

            async def __aexit__(self, *exc):
                pass

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     event_hooks={"response": [response_hook]}) as client:
            provider = CompatibleModelProvider(base_url="https://offline.invalid", api_key="TEST", model="offline",
                timeout_seconds=5, max_output_tokens=10, client=client, governed=True)
            scope = model_scope.set((available_factory, owner, None, "test"))
            guard = provider_guard.set(BeforeSend() if phase == "before_send" else None)
            try:
                messages = [dict(role="user", content="test")]
                if phase == "before_send":
                    task = asyncio.create_task(provider.generate(messages, schema={}))
                    await asyncio.wait_for(parsed.wait(), 5)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises((ConnectionError, RuntimeError)) as failure:
                        await provider.generate(messages, schema={})
                    assert type(failure.value) is ConnectionError if phase == "settlement" else failure.value.request_sent
            finally:
                provider_guard.reset(guard)
                model_scope.reset(scope)

        db_unavailable = False
        async with factory() as db:
            row = (await db.scalars(select(ModelAdmission).where(ModelAdmission.owner_key == owner))).one()
        assert row.status == {"before_send": "SETTLED", "settlement": "RESERVED"}.get(phase, "UNKNOWN")
        if phase == "settlement":
            # Recovery knows the ledger key but has no durable usage receipt.
            await ModelGovernance(factory).settle(row.call_key)
        async with factory() as db:
            recovered = (await db.scalars(select(ModelAdmission).where(ModelAdmission.owner_key == owner))).one()
        if phase == "before_send":
            assert recovered.actual_tokens == recovered.charged_tokens == 0
            assert not outbound
        else:
            assert recovered.status == "UNKNOWN" and recovered.actual_tokens is None
            assert recovered.charged_tokens == recovered.reserved_tokens
            assert len(outbound) == 1
    asyncio.run(run_with_database(database_url, scenario))


def test_real_auth_shared_read_private_boundaries_and_429(database_url):
    async def scenario(factory):
        from interview_api.main import app
        from interview_api.infrastructure.db.session import get_db
        from interview_api.core.security import create_access_token
        from interview_api.modules.interview import router as routes
        from interview_api.modules.interview_report import router as reports
        from interview_api.modules.interview_report.service import InterviewReportService
        from interview_api.modules.memory.models import UserMemoryItem
        core, _, _, a, first = await seed(factory)
        from test_v12_system_design import setup, PLANS
        core_b, _, b, sid_b = await setup(factory)
        for plan in PLANS:
            state = await core_b.read_state(sid_b, b)
            await core_b.answer(sid_b, b, state[3].id, uuid4().hex, "DEMO private B design", design_plan=plan)
        other = await core_b.detail(sid_b, b)
        report_service = InterviewReportService(factory, core_b.router)
        report = await report_service.generate(sid_b, b)
        assert report["generation_status"] == "COMPLETED"
        async with factory() as db:
            memory = UserMemoryItem(user_id=b, memory_type="PREFERENCE", content="DEMO B private", status="ACTIVE")
            db.add(memory)
            await db.commit()
            memory_id = memory.id
        async def test_db():
            async with factory() as db:
                yield db
        app.dependency_overrides[get_db] = test_db
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/api/v1/public-career/jobs")).status_code == 401
                auth_a = {"Authorization": "Bearer " + create_access_token(a, "USER")}
                auth_b = {"Authorization": "Bearer " + create_access_token(b, "USER")}
                job_id = first["context_snapshot"]["job_id"]
                ra = await client.get(f"/api/v1/public-career/jobs/{job_id}", headers=auth_a)
                rb = await client.get(f"/api/v1/public-career/jobs/{job_id}", headers=auth_b)
                assert ra.status_code == rb.status_code == 200
                assert ra.json()["data"]["jd_version_id"] == rb.json()["data"]["jd_version_id"]
                assert (await client.post("/api/v1/admin/experience/tasks", headers=auth_a, json={})).status_code == 403
                resume_id = other["context_snapshot"]["resume_id"] if "resume_id" in other["context_snapshot"] else None
                from interview_api.modules.resume.models import Resume
                async with factory() as db:
                    resume_id = await db.scalar(select(Resume.id).where(Resume.user_id == b))
                assert (await client.get(f"/api/v1/resumes/{resume_id}", headers=auth_a)).status_code == 404
                assert (await client.get(f"/api/v1/candidate-profile/versions/{other['context_snapshot']['candidate_profile_version_id']}", headers=auth_a)).status_code == 404
                with patch.object(routes, "build_core", return_value=core), patch.object(reports, "build_core", return_value=report_service):
                    for path in (f"/api/v1/interview/sessions/{other['id']}", f"/api/v1/interview/sessions/{other['id']}/questions",
                                 f"/api/v1/interview-reports/sessions/{other['id']}", f"/api/v1/interview-reports/{report['id']}"):
                        response = await client.get(path, headers=auth_a)
                        assert response.status_code == 404, (path, response.text)
                response = await client.patch(f"/api/v1/memory/items/{memory_id}", headers=auth_a, json={"content": "not allowed"})
                assert response.status_code == 404, response.text
                with patch("interview_api.core.rate_limit.check_rate_limit", return_value=False):
                    assert (await client.get("/api/v1/public-career/jobs", headers=auth_a)).status_code == 429
                    assert (await client.post("/api/v1/auth/login", json={"email": "demo@example.com", "password": "DEMO"})).status_code == 429
                with patch("interview_api.modules.job_matching.router.build_core") as matching:
                    from unittest.mock import AsyncMock
                    matching.return_value.generate = AsyncMock(return_value={"error": "MODEL_USER_QUOTA", "status": "FAILED"})
                    response = await client.post("/api/v1/job-matches/1/generate", headers=auth_a)
                    assert response.status_code == 429
        finally:
            app.dependency_overrides.pop(get_db, None)
    asyncio.run(run_with_database(database_url, scenario))


def test_cancelled_model_reservation_is_unknown_not_refunded(database_url):
    async def scenario(factory):
        from types import SimpleNamespace
        owner = "test:" + uuid4().hex
        started = asyncio.Event()
        async def operation():
            started.set()
            await asyncio.sleep(2)
        async def invoke():
            token = model_scope.set((factory, owner, None, "test"))
            try:
                await governed_call(SimpleNamespace(model_name="offline", max_output_tokens=20, timeout=3), [], operation)
            finally:
                model_scope.reset(token)
        task = asyncio.create_task(invoke())
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with factory() as db:
            row = await db.scalar(select(ModelAdmission).where(ModelAdmission.owner_key == owner))
            assert row.status == "UNKNOWN" and row.charged_tokens == row.reserved_tokens
    asyncio.run(run_with_database(database_url, scenario))


def test_user_admission_precedes_provider_queue_and_repair_is_charged(database_url):
    async def scenario(factory):
        from pydantic import BaseModel
        from interview_api.infrastructure.models.router import ModelRouter
        from interview_api.infrastructure.models.structured import generate_structured
        from interview_api.core.config import settings
        class Output(BaseModel):
            ok: bool
        owner = "test:" + uuid4().hex
        started, release = asyncio.Event(), asyncio.Event()
        async def handler(request):
            started.set()
            await release.wait()
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 10}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = CompatibleModelProvider(base_url="https://offline.invalid", api_key="TEST", model="offline",
                timeout_seconds=3, max_output_tokens=20, client=client, governed=True)
            router = ModelRouter({"test": provider}, concurrency=1)
            async def invoke():
                token = model_scope.set((factory, owner, None, "test"))
                try:
                    return await generate_structured(router, "test", system="DEMO", payload={}, response_model=Output, timeout_seconds=3)
                finally:
                    model_scope.reset(token)
            with patch.object(settings, "model_user_concurrency", 1):
                first = asyncio.create_task(invoke())
                await asyncio.wait_for(started.wait(), 2)
                with pytest.raises(AppError) as exc:
                    await asyncio.wait_for(invoke(), 1)
                assert exc.value.status_code == 429
                release.set()
                await first
            replies = iter(['{}', '{"ok":true}'])
            def repaired(request):
                return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}, "finish_reason": "stop"}],
                    "usage": {"total_tokens": 11}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(repaired)) as repair_client:
                provider.client = repair_client
                result = await invoke()
                assert result.structured_retry == 1
        async with factory() as db:
            rows = (await db.scalars(select(ModelAdmission).where(ModelAdmission.owner_key == owner))).all()
            assert len(rows) == 3 and sum(r.charged_tokens for r in rows) == 32
    asyncio.run(run_with_database(database_url, scenario))
