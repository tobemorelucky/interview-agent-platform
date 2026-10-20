"""Admin-only staged Web acquisition using existing tasks, Gateway and Research.

Search creates discovery tasks; selecting one source creates one immutable,
content-addressed extraction task. No public final entity is written here.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.core.url_safety import canonicalize_public_url
from .models import PublicDataRefreshTask, Source, SourceSnapshot
from .tool_models import ToolInvocation
from .tools.contracts import ToolContext
from .tools.deepseek_web import DeepSeekWebClient, DeepSeekSearchProvider, DeepSeekFetchProvider
from .tools.errors import ToolExecutionError
from .tools.factory import build_tool_gateway
from .web_audit import WebHTTPAudit

MODE = 'DEEPSEEK_WEB_SEARCH'


def build_web_gateway(factory, task_id):
    client = DeepSeekWebClient(audit=WebHTTPAudit(factory, task_id))
    return build_tool_gateway(factory, search_provider=DeepSeekSearchProvider(client), fetch_provider=DeepSeekFetchProvider(client))


def web_manifest(task):
    if not (task.target_ref or '').startswith(MODE + ':'):
        return None
    try:
        data = json.loads(task.reason)
        assert data['acquisition_mode'] == MODE and data['source_type'] in {'official', 'community'}
        assert task.target_ref == MODE + ':' + str(data['source_snapshot_id'])
        return data
    except (ValueError, KeyError, AssertionError, TypeError) as exc:
        raise ValueError('Invalid Web source manifest') from exc


async def resolve_web_snapshot(db, task):
    manifest = web_manifest(task)
    if not manifest:
        return None
    snapshot = await db.get(SourceSnapshot, manifest['source_snapshot_id'])
    source = await db.get(Source, snapshot.source_id) if snapshot else None
    call = await db.get(ToolInvocation, manifest['body_invocation_id'])
    if (not snapshot or not source or not call or call.status != 'SUCCEEDED'
        or call.tool_name not in {'public.search', 'public.fetch'}
        or call.provider_name != 'deepseek_web'
        or source.canonical_url != manifest['source_url']
        or snapshot.content_hash != manifest['content_hash']
        or sha256(snapshot.content_text.encode()).hexdigest() != snapshot.content_hash
        or not any(ref['source_snapshot_id'] == snapshot.id for ref in call.response_json.get('source_snapshot_refs', []))):
        raise ValueError('Web snapshot lacks successful audited body provenance')
    return [snapshot]


class WebCollectionService:
    def __init__(self, factory, gateway_builder=build_web_gateway):
        self.factory, self.gateway_builder = factory, gateway_builder

    async def _create(self, key, target, reason):
        async with self.factory() as db:
            await db.execute(insert(PublicDataRefreshTask).values(idempotency_key=key, target_type=target,
                reason=json.dumps(reason, ensure_ascii=False), status='PENDING').on_conflict_do_nothing(
                    constraint='uq_public_data_refresh_tasks_idempotency_key'))
            task = await db.scalar(select(PublicDataRefreshTask).where(PublicDataRefreshTask.idempotency_key == key))
            await db.commit()
            return task.id

    async def _claim(self, task_id, state, *, refresh=False):
        async with self.factory() as db:
            task = await db.scalar(select(PublicDataRefreshTask).where(PublicDataRefreshTask.id == task_id).with_for_update())
            if task.status == 'COMPLETED' and not refresh:
                return False
            if task.status == 'COMPLETED' and refresh:
                task.status = 'PENDING'
            if task.status not in {'PENDING', 'FAILED'}:
                # An interrupted request is never silently resent; operator can
                # mark a bounded stale lease failed, then explicitly retry.
                if task.started_at and task.started_at < datetime.now(timezone.utc) - timedelta(minutes=5):
                    task.status, task.error_message = 'FAILED', 'Interrupted acquisition; safe explicit retry required'
                    await db.commit()
                raise AppError('WEB_TASK_BUSY', '采集正在执行或已中断，请刷新状态后显式重试', 409)
            task.status, task.started_at = state, datetime.now(timezone.utc)
            task.error_message, task.completed_at = None, None
            await db.commit()
            return task.started_at.isoformat()

    async def _finish(self, task_id, status, error=None, *, lease=None):
        async with self.factory() as db:
            task = await db.scalar(select(PublicDataRefreshTask).where(PublicDataRefreshTask.id == task_id).with_for_update())
            if lease is not None and task.started_at.isoformat() != lease:
                raise AppError('WEB_TASK_CHANGED', '任务已被其他请求恢复，请刷新后查看当前状态', 409)
            task.status, task.error_message, task.completed_at = status, error, datetime.now(timezone.utc)
            await db.commit()

    async def search(self, parameters):
        # API input consists only of explicit public keywords; no Resume/Memory
        # or user profile is loaded by this service.
        query = ' '.join(filter(None, [parameters.get('company'), parameters.get('job_title'), parameters.get('keywords')]))
        request = dict(query=query, time_window_hours=8760, max_results=parameters.get('count', 5),
            need_content=parameters.get('need_content', True), sites=parameters.get('sites', []),
            published_after=parameters.get('published_after'), published_before=parameters.get('published_before'))
        identity = json.dumps([request, parameters['request_key']], sort_keys=True)
        task_id = await self._create('web-search:' + sha256(identity.encode()).hexdigest(), 'WEB_DISCOVERY',
            dict(acquisition_mode=MODE, parameters=request))
        lease = await self._claim(task_id, 'SEARCHING')
        if lease:
            try:
                await self.gateway_builder(self.factory, task_id).invoke('public.search', request, ToolContext(refresh_task_id=task_id))
                await self._finish(task_id, 'COMPLETED', lease=lease)
            except BaseException as exc:
                await self._finish(task_id, 'FAILED', getattr(exc, 'code', type(exc).__name__), lease=lease)
                raise
        return await self.detail(task_id)

    async def select(self, search_task_id, url, document_type, source_type, *, refresh_body=False):
        url = canonicalize_public_url(url)
        async with self.factory() as db:
            search = await db.get(PublicDataRefreshTask, search_task_id)
            if not search or search.target_type != 'WEB_DISCOVERY' or search.status != 'COMPLETED':
                raise AppError('WEB_SEARCH_REQUIRED', '请先完成搜索并选择实际返回的网页', 409)
            calls = list((await db.scalars(select(ToolInvocation).where(
                ToolInvocation.refresh_task_id == search_task_id, ToolInvocation.tool_name == 'public.search',
                ToolInvocation.status == 'SUCCEEDED').order_by(ToolInvocation.id.desc()))).all())
            selected = next((r for call in calls for r in call.response_json.get('data', {}).get('results', [])
                             if canonicalize_public_url(r['url']) == url), None)
            if selected is None:
                raise AppError('WEB_SELECTION_INVALID', '只能采集本次搜索真实返回的 URL', 422)
            source = await db.scalar(select(Source).where(Source.canonical_url == url))
            snapshot = None
            body_call_id = None
            if source:
                ids = [ref['source_snapshot_id'] for call in calls for ref in call.response_json.get('source_snapshot_refs', [])]
                snapshot = await db.scalar(select(SourceSnapshot).where(SourceSnapshot.source_id == source.id,
                    SourceSnapshot.id.in_(ids),
                    # Insufficient body is not replaced by search snippet/summary.
                    ).order_by(SourceSnapshot.id.desc()).limit(1))
                if snapshot and len(snapshot.content_text.strip()) < settings.deepseek_web_min_body_chars:
                    snapshot = None
                if snapshot:
                    body_call_id = next(call.id for call in calls if any(
                        ref['source_snapshot_id'] == snapshot.id for ref in call.response_json.get('source_snapshot_refs', [])))
                elif not refresh_body:
                    # Reuse actual Web API bodies, never manual/fixture content
                    # while claiming a new LIVE acquisition.
                    prior = (await db.execute(select(SourceSnapshot, ToolInvocation).join(ToolInvocation,
                        SourceSnapshot.created_by_tool_invocation_id == ToolInvocation.id).where(
                            SourceSnapshot.source_id == source.id, ToolInvocation.provider_name == 'deepseek_web',
                            ToolInvocation.status == 'SUCCEEDED').order_by(SourceSnapshot.id.desc()).limit(1))).first()
                    if prior and len(prior[0].content_text.strip()) >= settings.deepseek_web_min_body_chars:
                        snapshot, body_call_id = prior[0], prior[1].id
            snapshot_id = snapshot.id if snapshot else None
        if snapshot_id is None:
            # Same URL and discovery batch share one Fetch claim across users
            # and processes; long network work never holds the row lock.
            fetch_id = await self._create('web-fetch:' + sha256(url.encode()).hexdigest(),
                                         'WEB_ACQUIRE', dict(source_url=url, acquisition_mode=MODE))
            lease = await self._claim(fetch_id, 'FETCHING', refresh=refresh_body)
            if lease:
                try:
                    await self.gateway_builder(self.factory, fetch_id).invoke('public.fetch', {'url': url}, ToolContext(refresh_task_id=fetch_id))
                    await self._finish(fetch_id, 'COMPLETED', lease=lease)
                except BaseException as exc:
                    await self._finish(fetch_id, 'FAILED', getattr(exc, 'code', type(exc).__name__), lease=lease)
                    raise
            async with self.factory() as db:
                call = await db.scalar(select(ToolInvocation).where(ToolInvocation.refresh_task_id == fetch_id,
                    ToolInvocation.tool_name == 'public.fetch', ToolInvocation.status == 'SUCCEEDED').order_by(ToolInvocation.id.desc()).limit(1))
                snapshot_id = call.response_json['source_snapshot_refs'][0]['source_snapshot_id']
                body_call_id = call.id
        async with self.factory() as db:
            snapshot = await db.get(SourceSnapshot, snapshot_id)
            manifest = dict(acquisition_mode=MODE, source_url=url, source_type=source_type,
                source_snapshot_id=snapshot_id, content_hash=snapshot.content_hash, body_invocation_id=body_call_id,
                document_type=document_type, search_task_id=search_task_id)
            # URL + body + document kind is the public extraction identity, not
            # the requesting admin or search batch. Relabeling cannot duplicate it.
            key = 'web-extract:' + sha256(f'{url}:{snapshot.content_hash}:{document_type}'.encode()).hexdigest()
            await db.execute(insert(PublicDataRefreshTask).values(target_type='JOB_JD' if document_type == 'job_jd' else 'INTERVIEW_DATA',
                target_ref=MODE + ':' + str(snapshot_id), reason=json.dumps(manifest), status='READY_FOR_EXTRACTION',
                idempotency_key=key).on_conflict_do_nothing(constraint='uq_public_data_refresh_tasks_idempotency_key'))
            task = await db.scalar(select(PublicDataRefreshTask).where(PublicDataRefreshTask.idempotency_key == key))
            if json.loads(task.reason)['source_type'] != source_type:
                raise AppError('SOURCE_IDENTITY_CONFLICT', '相同正文已有来源身份，不能通过重新标注绕过审核', 409)
            await db.commit()
            task_id = task.id
        return await self.detail(task_id)

    async def detail(self, task_id):
        from interview_api.modules.experience.models import ExperienceAgentRun
        async with self.factory() as db:
            task = await db.get(PublicDataRefreshTask, task_id)
            if not task or not (task.idempotency_key or '').startswith('web-'):
                raise AppError('WEB_TASK_NOT_FOUND', '采集任务不存在', 404)
            calls = list((await db.scalars(select(ToolInvocation).where(ToolInvocation.refresh_task_id == task_id)
                .order_by(ToolInvocation.id))).all())
            manifest = web_manifest(task)
            snapshot = await db.get(SourceSnapshot, manifest['source_snapshot_id']) if manifest else None
            run = await db.scalar(select(ExperienceAgentRun).where(ExperienceAgentRun.refresh_task_id == task_id)
                                  .order_by(ExperienceAgentRun.id.desc()).limit(1))
            body_call = await db.get(ToolInvocation, manifest['body_invocation_id']) if manifest else None
            if (task.status in {'SEARCHING', 'FETCHING'} and task.started_at
                and task.started_at < datetime.now(timezone.utc) - timedelta(minutes=5)):
                # These are short, bounded acquisitions (Gateway <=90s). A
                # crashed process cannot leave the UI pending indefinitely.
                task.status, task.error_message = 'FAILED', 'Interrupted acquisition; explicit retry required'
                task.completed_at = datetime.now(timezone.utc)
                await db.commit()
            return dict(id=task.id, status=task.status, target_type=task.target_type, error=task.error_message,
                acquisition_mode=MODE, manifest=manifest, body_preview=(snapshot.content_text or '')[:12000] if snapshot else None,
                provider_mode=body_call.provider_mode if body_call else calls[0].provider_mode if calls else None,
                research_result=run.output_json if run else None,
                calls=[dict(id=c.id, tool=c.tool_name, status=c.status, mode=c.provider_mode,
                    error_code=c.error_code, result=c.response_json) for c in calls])
