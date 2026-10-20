import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, func

from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.modules.auth.service import AuthService
from interview_api.modules.public_career.library import PublicCareerLibrary
from interview_api.modules.public_career.models import Source, SourceSnapshot, JobDescriptionVersion, ClaimEvidence, PublicDataRefreshTask
from interview_api.modules.public_career.tool_models import ToolInvocation
from interview_api.modules.public_career.web_collection import WebCollectionService
from interview_api.modules.public_career.web_audit import WebHTTPAudit
from interview_api.modules.public_career.tools.errors import ToolExecutionError
from interview_api.modules.public_career.review import PublicCareerReviewService
from test_m4_interview import run_with_database
from v14_support import FixtureWeb, FixtureResearchAgent


@pytest.fixture(autouse=True)
def offline_caps(monkeypatch):
    # Isolated REPLAY tests may be rerun; no live request is admitted here.
    monkeypatch.setattr(settings, 'deepseek_web_daily_search_limit', 10000)
    monkeypatch.setattr(settings, 'deepseek_web_daily_fetch_limit', 10000)
    monkeypatch.setattr(settings, 'deepseek_web_min_interval_seconds', .1)


def parameters():
    return dict(company='DEMO', keywords='岗位', need_content=True, count=5, request_key=str(uuid4()))


def test_search_body_no_double_fetch_dedupe_and_updated_version(database_url):
    async def scenario(factory):
        web = FixtureWeb(uuid4().hex)
        service = WebCollectionService(factory, web.gateway)
        try:
            request = parameters()
            search = await service.search(request)
            assert search['provider_mode'] == 'REPLAY'
            assert (await service.search(request))['id'] == search['id']
            first = await service.select(search['id'], web.jd_url, 'job_jd', 'community')
            again = await service.select(search['id'], web.jd_url, 'job_jd', 'community')
            assert first['id'] == again['id'] and len(web.requests) == 1
            result = await FixtureResearchAgent(factory).run(first['id'])
            assert result['status'] == 'COMPLETED', result
            assert result['publication_status'] == 'WAITING_REVIEW'
            web.bodies[web.jd_url] += '\n公开正文更新。'
            next_search = await service.search(parameters())
            changed = await service.select(next_search['id'], web.jd_url, 'job_jd', 'community')
            updated = await FixtureResearchAgent(factory).run(changed['id'])
            assert updated['status'] == 'COMPLETED', updated
            assert updated['jd_version_id'] != result['jd_version_id']
            async with factory() as db:
                source = await db.scalar(select(Source).where(Source.canonical_url == web.jd_url))
                assert await db.scalar(select(func.count()).select_from(SourceSnapshot).where(SourceSnapshot.source_id == source.id)) == 2
                assert (await db.get(JobDescriptionVersion, updated['jd_version_id'])).version_number > (await db.get(JobDescriptionVersion, result['jd_version_id'])).version_number
                assert await db.scalar(select(func.count()).select_from(ClaimEvidence).where(ClaimEvidence.source_snapshot_id == first['manifest']['source_snapshot_id'])) > 0
        finally: await web.http.aclose()
    asyncio.run(run_with_database(database_url, scenario))


def test_real_api_admin_review_two_users_shared_and_private_denied(database_url, monkeypatch):
    async def scenario(factory):
        from interview_api.main import app
        from interview_api.infrastructure.db.session import get_db
        from interview_api.modules.public_career import web_router
        from interview_api.core.security import create_access_token
        web = FixtureWeb(uuid4().hex)
        service = WebCollectionService(factory, web.gateway)
        async with factory() as db:
            ids = []
            for role in ('ADMIN', 'USER', 'USER'):
                tag = uuid4().hex
                ids.append(await AuthService(db).register(f'{tag}@example.com', tag, 'DEMO-fixture-only', role))
            await db.commit()
        headers = [{'Authorization': 'Bearer ' + create_access_token(user_id=uid, role=role)}
                   for uid, role in zip(ids, ('ADMIN', 'USER', 'USER'))]
        async def get_test_db():
            async with factory() as db: yield db
        app.dependency_overrides[get_db] = get_test_db
        app.dependency_overrides[web_router.get_web_service] = lambda: service
        monkeypatch.setattr(web_router, 'PublicDataResearchAgent', FixtureResearchAgent)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
                path = '/api/v1/admin/public-career'
                assert (await http.get(path + '/status')).status_code == 401
                assert (await http.post(path + '/search', json=parameters(), headers=headers[1])).status_code == 403
                response = await http.post(path + '/search', json=parameters(), headers=headers[0])
                assert response.status_code == 200, response.text
                search = response.json()['data']
                selection = await http.post(path + f"/tasks/{search['id']}/select", headers=headers[0],
                    json=dict(source_url=web.jd_url, document_type='job_jd', source_type='community'))
                assert selection.status_code == 200, selection.text
                task_id = selection.json()['data']['id']
                response = await http.post(path + f'/tasks/{task_id}/extract', json={}, headers=headers[0])
                assert response.status_code == 200, response.text
                result = response.json()['data']['research_result']
                assert result['status'] == 'COMPLETED', response.text
                jid = result['jd_version_id']
                job_id = result['job_id']
                assert (await http.get(f'/api/v1/public-career/jobs/{job_id}', headers=headers[1])).status_code == 404
                rejected = await http.post(path + f'/jd/{jid}/review', headers=headers[0], json=dict(action='reject', reason='DEMO 审核拒绝'))
                assert rejected.status_code == 200 and rejected.json()['data']['publication_status'] == 'REJECTED'
                blocked = await http.post(path + f'/jd/{jid}/review', headers=headers[0], json=dict(action='publish', reason='不得绕过拒绝'))
                assert blocked.status_code == 409
                correction = await http.post(path + f'/jd/{jid}/review', headers=headers[0], json=dict(action='request_correction', reason='DEMO 重新待核实'))
                assert correction.status_code == 200 and correction.json()['data']['publication_status'] == 'WAITING_REVIEW'
                published = await http.post(path + f'/jd/{jid}/review', headers=headers[0], json=dict(action='publish', reason='DEMO 来源与精确证据已核实'))
                assert published.status_code == 200, published.text
                assert (await http.post(path + f'/jd/{jid}/review', headers=headers[0], json=dict(action='reject', reason='不能覆盖已发布版本'))).status_code == 409
                a = await http.get(f'/api/v1/public-career/jobs/{job_id}', headers=headers[1])
                b = await http.get(f'/api/v1/public-career/jobs/{job_id}', headers=headers[2])
                assert a.status_code == b.status_code == 200 and a.json()['data'] == b.json()['data']
                assert a.json()['data']['source_type'] == 'community'
                assert len(a.json()['data']['description']) <= 500
                assert (await http.get(path + f'/tasks/{task_id}', headers=headers[1])).status_code == 403
                # No direct paid/private payload input is accepted.
                assert (await http.post(path + '/search', json={**parameters(), 'resume_id': 38}, headers=headers[0])).status_code == 422
                exp_selection = await http.post(path + f"/tasks/{search['id']}/select", headers=headers[0],
                    json=dict(source_url=web.exp_url, document_type='interview_experience', source_type='community'))
                exp_task = exp_selection.json()['data']['id']
                extracted = await http.post(path + f'/tasks/{exp_task}/extract', json={}, headers=headers[0])
                assert extracted.status_code == 200, extracted.text
                exp = extracted.json()['data']['research_result']
                assert exp['status'] == 'COMPLETED', extracted.text
                assert exp['review_status'] == 'WAITING_REVIEW'
                assert (await http.get(f"/api/v1/public-career/experiences/{exp['experience_id']}", headers=headers[1])).status_code == 404
                approved = await http.post(f"/api/v1/admin/experience/review/items/{exp['experience_id']}/approve", headers=headers[0])
                assert approved.status_code == 200, approved.text
                for user in headers[1:]:
                    shared = await http.get(f"/api/v1/public-career/experiences/{exp['experience_id']}", headers=user)
                    assert shared.status_code == 200 and len(shared.json()['data']['questions']) == 2
                    assert shared.json()['data']['source_type'] == 'community'
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(web_router.get_web_service, None)
            await web.http.aclose()
    asyncio.run(run_with_database(database_url, scenario))


def test_snippet_requires_fetch_concurrent_selection_shares_body(database_url):
    async def scenario(factory):
        web = FixtureWeb(uuid4().hex)
        web.need_fetch = True
        service = WebCollectionService(factory, web.gateway)
        try:
            search = await service.search(parameters())
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(SourceSnapshot).join(Source).where(Source.canonical_url == web.jd_url)) == 0
            async def choose():
                try: return await service.select(search['id'], web.jd_url, 'job_jd', 'community')
                except AppError as exc: return exc
            results = await asyncio.gather(choose(), choose())
            assert any(isinstance(r, dict) for r in results)
            assert all(isinstance(r, dict) or r.status_code == 409 for r in results)
            again = await choose()
            assert isinstance(again, dict)
            assert sum(path.endswith('/fetch') for path, _ in web.requests) == 1
            with pytest.raises(AppError): await service.select(search['id'], 'https://not-selected.example/', 'job_jd', 'official')
        finally: await web.http.aclose()
    asyncio.run(run_with_database(database_url, scenario))


@pytest.mark.parametrize('unsupported', [False, True])
def test_model_failure_or_unsupported_requirement_keeps_unpublished(database_url, unsupported):
    async def scenario(factory):
        web = FixtureWeb(uuid4().hex)
        service = WebCollectionService(factory, web.gateway)
        try:
            search = await service.search(parameters())
            task = await service.select(search['id'], web.jd_url, 'job_jd', 'community')
            result = await FixtureResearchAgent(factory, fail=not unsupported, unsupported=unsupported).run(task['id'])
            assert result['status'] == 'FAILED', result
            assert (await service.detail(task['id']))['research_result'] is None
            async with factory() as db:
                assert (await db.get(PublicDataRefreshTask, task['id'])).error_message
                assert await db.scalar(select(func.count()).select_from(JobDescriptionVersion).where(
                    JobDescriptionVersion.source_snapshot_id == task['manifest']['source_snapshot_id'])) == 0
        finally: await web.http.aclose()
    asyncio.run(run_with_database(database_url, scenario))


def test_http_budget_is_durable_and_counts_failures(database_url, monkeypatch):
    async def scenario(factory):
        web = FixtureWeb(uuid4().hex)
        service = WebCollectionService(factory, web.gateway)
        try:
            search = await service.search(parameters())
            audit = WebHTTPAudit(factory, search['id'], mode='REPLAY')
            from datetime import datetime, timezone
            async with factory() as db:
                current = await db.scalar(select(func.count()).select_from(ToolInvocation).where(
                    ToolInvocation.tool_name == 'deepseek.web.search.http', ToolInvocation.provider_mode == 'REPLAY',
                    ToolInvocation.created_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)))
            monkeypatch.setattr(settings, 'deepseek_web_daily_search_limit', current + 1)
            first = await audit.reserve('search', {'query': 'DEMO no HTTP sent'})
            await audit.finish(first, 'FAILED', {'error_code': 'test-no-network'})
            with pytest.raises(ToolExecutionError) as exc: await audit.reserve('search', {'query': 'DEMO'})
            assert exc.value.code == 'deepseek_web_budget_exhausted'
        finally: await web.http.aclose()
    asyncio.run(run_with_database(database_url, scenario))


def test_cross_source_repost_does_not_create_independent_support_or_second_jd(database_url):
    async def scenario(factory):
        from interview_api.modules.public_career.dedupe import group_independent_content
        web = FixtureWeb(uuid4().hex)
        web.bodies[web.exp_url] = web.bodies[web.jd_url]
        service = WebCollectionService(factory, web.gateway)
        try:
            discovery = await service.search(parameters())
            results = []
            for source in (web.jd_url, web.exp_url):
                task = await service.select(discovery['id'], source, 'job_jd', 'community')
                result = await FixtureResearchAgent(factory).run(task['id'])
                assert result['status'] == 'COMPLETED', result
                results.append((task, result))
            assert results[0][1]['jd_version_id'] == results[1][1]['jd_version_id']
            async with factory() as db:
                snapshots = list((await db.scalars(select(SourceSnapshot).where(SourceSnapshot.id.in_(
                    [task['manifest']['source_snapshot_id'] for task, _ in results])))).all())
                grouped = group_independent_content(snapshots)
                assert grouped.independent_content_count == 1 and grouped.occurrence_count == 2 and not grouped.multi_source
        finally: await web.http.aclose()
    asyncio.run(run_with_database(database_url, scenario))


def test_stale_acquisition_is_failed_on_refresh_not_permanently_pending(database_url):
    async def scenario(factory):
        from datetime import datetime, timedelta, timezone
        from hashlib import sha256
        import json
        task = PublicDataRefreshTask(target_type='WEB_DISCOVERY', status='SEARCHING', reason=json.dumps({}),
            idempotency_key='web-search:' + uuid4().hex, started_at=datetime.now(timezone.utc) - timedelta(minutes=6))
        async with factory() as db:
            db.add(task)
            await db.commit()
            task_id = task.id
        result = await WebCollectionService(factory).detail(task_id)
        assert result['status'] == 'FAILED' and 'Interrupted' in result['error']
    asyncio.run(run_with_database(database_url, scenario))


def test_web_question_literal_failure_stops_before_routing(database_url):
    async def scenario(factory):
        from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
        from interview_api.modules.public_career.research.evidence import EvidenceCatalog
        from interview_api.infrastructure.models.provider import ReplayModelProvider
        from interview_api.infrastructure.models.router import ModelRouter, EXTRACT_ROUTE, QUALITY_ROUTE
        from test_m2b_research_agent import interview_responses
        web = FixtureWeb(uuid4().hex)
        service = WebCollectionService(factory, web.gateway)
        try:
            search = await service.search(parameters())
            task = await service.select(search['id'], web.exp_url, 'interview_experience', 'community')
            async with factory() as db:
                snapshot = await db.get(SourceSnapshot, task['manifest']['source_snapshot_id'])
                catalog = EvidenceCatalog([snapshot])
            replies = interview_responses(web.company, [next(iter(catalog.spans))])
            replies[0]['questions'][0]['question'] = '这是原文没有提供支持的新问题吗？'
            provider = ReplayModelProvider(replies)
            result = await PublicDataResearchAgent(factory, ModelRouter({EXTRACT_ROUTE:provider, QUALITY_ROUTE:provider})).run(task['id'])
            assert result['status'] == 'FAILED'
            assert 'questions[0].question' in result['error']
            assert 'literal_elsewhere=False' in result['error']
            assert provider.calls == 1  # No wasted routing/reliability requests.
        finally: await web.http.aclose()
    asyncio.run(run_with_database(database_url, scenario))
