"""One authorized, bounded V1.4.2 batch; reuse real captured source, never Replay.

Preparation clones the old isolated acceptance DB (including failed history).
No official business DB writes; no search is required for the known source.
"""
import argparse
import asyncio
import json
import msvcrt
from pathlib import Path
import sys
import time

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
sys.path.insert(0, str(API / 'src'))

import httpx
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from interview_api.core.config import settings
from interview_api.infrastructure.models.router import build_model_router, ModelRouter
from interview_api.modules.public_career.models import SourceSnapshot, PublicDataRefreshTask
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
from interview_api.modules.public_career.research.fragments import InterviewSourceFragments
from interview_api.modules.public_career.tools.deepseek_web import DeepSeekWebClient, DeepSeekSearchProvider, DeepSeekFetchProvider
from interview_api.modules.public_career.tools.factory import build_tool_gateway
from interview_api.modules.public_career.tools.contracts import ToolContext
from interview_api.modules.public_career.tools.errors import ToolExecutionError
from interview_api.modules.public_career.web_audit import WebHTTPAudit
from accept_deepseek_web_v141 import Budget as PreviousBudget, BudgetStop, Meter as PreviousMeter, CURRENT, MODEL
import interview_api.modules.models  # noqa

ORIGINAL = 'interview_agent_v14_acceptance_live_20261020_attempt1'
DATABASE = 'interview_agent_v14_acceptance_live_20261021_v142_attempt1'
SOURCE = 'https://www.nowcoder.com/discuss/427769028658184192?toCommentId=15083088'


class Budget(PreviousBudget):
    def __init__(self, path):
        super().__init__(path)
        self.web = []

    def save(self, status='RUNNING'):
        self.path.write_text(json.dumps(dict(status=status, model=MODEL, model_mode='LIVE',
            data_mode='RECORDED_LIVE_SEARCH_SNAPSHOT_AND_INDEPENDENT_LIVE_FETCH',
            limits=dict(logical=4, http=8, tokens=40000, seconds=600, search=2, fetch=2),
            logical=self.logical, http=self.http, charged_tokens=self.tokens, web=self.web,
            results=self.results, stop_reason=self.stop, elapsed_seconds=round(time.monotonic()-self.started, 3)),
            ensure_ascii=False, indent=2), encoding='utf-8')

    async def request(self, request):
        self.check()
        body = json.loads(request.content)
        estimate = len(request.content) + body['max_tokens'] + 1024
        if body['model'] != MODEL or len(self.http) >= 8 or self.tokens + estimate > 40000:
            raise BudgetStop('MODEL_OR_HTTP_OR_TOKEN_LIMIT')
        self.tokens += estimate
        row = dict(number=len(self.http)+1, **CURRENT.get(), model=MODEL, estimate=estimate,
                   accounting='UNKNOWN_CONSERVATIVE', http_status=None, thinking=body.get('thinking'))
        self.http.append(row)
        request.extensions['v141'] = (row, time.monotonic())
        self.save()


class Meter(PreviousMeter):
    async def generate(self, messages, *, schema):
        repair = messages[0]['content'].startswith('Repair JSON syntax/structural fields only.')
        if not repair and self.budget.logical >= 4:
            raise BudgetStop('LOGICAL_LIMIT')
        return await super().generate(messages, schema=schema)


class WebMeter(WebHTTPAudit):
    def __init__(self, factory, task_id, budget):
        super().__init__(factory, task_id)
        self.budget, self.rows = budget, {}

    async def reserve(self, kind, payload):
        self.budget.check()
        if sum(row['kind'] == kind for row in self.budget.web) >= 2:
            raise BudgetStop('WEB_REQUEST_LIMIT')
        ticket = await super().reserve(kind, payload)
        row = dict(kind=kind, ticket=ticket, status='RESERVED')
        self.rows[ticket] = row
        self.budget.web.append(row)
        self.budget.save()
        return ticket

    async def finish(self, ticket, status, details):
        await super().finish(ticket, status, details)
        self.rows[ticket].update(status=status, **details)
        if details.get('http_status') in {401, 402}:
            self.budget.stop = 'WEB_AUTH_OR_BALANCE_ERROR'
        self.budget.save()


async def prepare():
    import asyncpg
    url = make_url(settings.database_url).set(drivername='postgresql', database='postgres')
    connection = await asyncpg.connect(url.render_as_string(hide_password=False), timeout=5)
    try:
        if await connection.fetchval('SELECT 1 FROM pg_database WHERE datname=$1', DATABASE):
            raise BudgetStop('DATABASE_ALREADY_EXISTS_NO_OVERWRITE')
        await connection.execute(f'CREATE DATABASE "{DATABASE}" TEMPLATE "{ORIGINAL}"')
    finally:
        await connection.close()
    print('Cloned prior isolated LIVE records, unchanged original: ' + DATABASE)


async def run(budget):
    engine = create_async_engine(make_url(settings.database_url).set(database=DATABASE))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        original = build_model_router()
        if original.fallback_routes or any(p.model_name != MODEL for p in original.routes.values()):
            raise BudgetStop('MODEL_ROUTE_MISMATCH')
        async with factory() as db:
            old = await db.get(SourceSnapshot, 5)
            if old is None:
                raise BudgetStop('CAPTURED_SNAPSHOT_MISSING')
            catalog = InterviewSourceFragments([old])
            budget.results.append(dict(stage='recorded_source', source_url=SOURCE,
                snapshot_id=old.id, raw_chars=len(old.content_text), raw_hash=old.content_hash,
                question_fragments=sum(k == 'QUESTION' for k in catalog.kinds.values()),
                reported_topic_fragments=sum(k == 'REPORTED_TOPIC' for k in catalog.kinds.values()),
                excluded=catalog.excluded))
            task = PublicDataRefreshTask(target_type='WEB_ACQUIRE', target_ref=SOURCE,
                reason='V1.4.2 independent Fetch, not a new Search', idempotency_key='v142:independent-fetch', status='FETCHING')
            db.add(task)
            await db.commit()
            task_id = task.id
        # Independent Fetch remains an actual Gateway call, with a real audit.
        audit = WebMeter(factory, task_id, budget)
        client = DeepSeekWebClient(audit=audit, retries=0)
        gateway = build_tool_gateway(factory, search_provider=DeepSeekSearchProvider(client), fetch_provider=DeepSeekFetchProvider(client))
        try:
            fetched = await gateway.invoke('public.fetch', {'url':SOURCE}, ToolContext(refresh_task_id=task_id))
            refs = fetched.data['source_snapshot_refs']
            body = fetched.data['content']
            async with factory() as db:
                fresh = await db.get(SourceSnapshot, refs[0]['source_snapshot_id'])
                spans = InterviewSourceFragments([fresh])
                budget.results.append(dict(stage='independent_fetch', source_url=SOURCE, status='SUCCEEDED',
                    title=fresh.title, content_chars=len(body), snapshot_id=fresh.id, content_hash=fresh.content_hash,
                    equal_to_search=body == old.content_text, original_snapshot_id=old.id,
                    question_fragments=sum(k == 'QUESTION' for k in spans.kinds.values()),
                    evidence_locatable=all(body[s.start:s.end] == s.excerpt for s in spans.spans.values())))
        except ToolExecutionError as exc:
            budget.results.append(dict(stage='independent_fetch', source_url=SOURCE, status='FAILED',
                error_code=exc.code, details=exc.details))
            if budget.stop:
                raise BudgetStop(budget.stop)
        async with factory() as db:
            task = await db.get(PublicDataRefreshTask, task_id)
            task.status = 'COMPLETED' if budget.results[-1]['status'] == 'SUCCEEDED' else 'FAILED'
            await db.commit()
        budget.save()
        # Same article and same captured Snapshot, not an easier replacement.
        async with httpx.AsyncClient(event_hooks={'request':[budget.request], 'response':[budget.response]}) as http:
            router = ModelRouter({r:Meter(p,budget,r,http) for r,p in original.routes.items()}, concurrency=original.concurrency)
            result = await PublicDataResearchAgent(factory, router).run(4, force=True)
            budget.results.append(dict(stage='research_same_snapshot_5', **result))
            budget.save()
            if result['status'] != 'COMPLETED':
                raise BudgetStop('BUSINESS_VALIDATION_FAILED')
        budget.save('EXTRACTED_PENDING_ADMIN_REVIEW')
    finally:
        await engine.dispose()


async def bounded(budget):
    try:
        async with asyncio.timeout(600):
            await run(budget)
    except BaseException as exc:
        budget.stop = budget.stop or getattr(exc, 'code', type(exc).__name__) + ':' + str(exc)[:160]
        budget.save('STOPPED')
    print(json.dumps(dict(status='STOPPED' if budget.stop else 'EXTRACTED_PENDING_ADMIN_REVIEW',
        logical=budget.logical, http=len(budget.http), tokens=budget.tokens,
        web_attempts=len(budget.web), stop_reason=budget.stop), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--authorized', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        asyncio.run(prepare())
    elif args.authorized:
        path = ROOT / '.runtime/acceptance/deepseek_web_v142_20261021_attempt1.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as handle:
            handle.write('{}')
        with (path.parent / 'deepseek_web_v141.lock').open('a+b') as lock:
            if lock.tell() == 0:
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            budget = Budget(path)
            try:
                asyncio.run(bounded(budget))
            finally:
                lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            if budget.stop:
                raise SystemExit(1)
    else:
        parser.error('Explicit one-batch authorization required')
