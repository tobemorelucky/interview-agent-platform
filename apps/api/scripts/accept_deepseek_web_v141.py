"""One bounded LIVE extraction attempt over already audited real Search bodies.

No discovery retries, fixture model, production writes or source relabeling.
"""
import argparse
import asyncio
from contextvars import ContextVar
import json
import msvcrt
from pathlib import Path
import sys
import time

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
sys.path.insert(0, str(API / 'src'))
import httpx
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from interview_api.core.config import settings
from interview_api.infrastructure.models.provider import ModelProvider
from interview_api.infrastructure.models.router import ModelRouter, build_model_router
from interview_api.modules.public_career.web_collection import WebCollectionService
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
import interview_api.modules.models

MODEL = 'deepseek-v4-pro-ga-260813'
CURRENT = ContextVar('v141_model_call', default={})


class BudgetStop(RuntimeError): pass


class Budget:
    def __init__(self, path):
        self.path, self.started = path, time.monotonic()
        self.logical, self.tokens = 0, 0
        self.http, self.results = [], []
        self.stop = None

    def save(self, status='RUNNING'):
        self.path.write_text(json.dumps(dict(status=status, model=MODEL, model_mode='LIVE',
            data_mode='LIVE_DEEPSEEK_SEARCH_BODY', limits=dict(logical=5, http=10, tokens=100000, seconds=600),
            logical=self.logical, http=self.http, charged_tokens=self.tokens, results=self.results,
            stop_reason=self.stop, elapsed_seconds=round(time.monotonic()-self.started, 3)),
            ensure_ascii=False, indent=2), encoding='utf-8')

    def check(self):
        if self.stop: raise BudgetStop(self.stop)
        if time.monotonic()-self.started >= 600: raise BudgetStop('DEADLINE')

    async def request(self, request):
        self.check()
        body = json.loads(request.content)
        estimate = len(request.content) + body['max_tokens'] + 1024
        if body['model'] != MODEL or len(self.http) >= 10 or self.tokens + estimate > 100000:
            raise BudgetStop('MODEL_OR_HTTP_OR_TOKEN_LIMIT')
        self.tokens += estimate
        row = dict(number=len(self.http)+1, **CURRENT.get(), model=MODEL,
            estimate=estimate, accounting='UNKNOWN_CONSERVATIVE', http_status=None,
            thinking=body.get('thinking'))
        self.http.append(row)
        request.extensions['v141'] = (row, time.monotonic())
        self.save()

    async def response(self, response):
        row, started = response.request.extensions['v141']
        await response.aread()
        try: data = response.json()
        except ValueError: data = {}
        usage = data.get('usage') or {}
        actual = usage.get('total_tokens')
        if type(actual) is int and actual >= 0:
            self.tokens += actual - row['estimate']
            row['accounting'] = 'ACTUAL_USAGE'
        row.update(http_status=response.status_code, latency_ms=round((time.monotonic()-started)*1000),
            input_tokens=usage.get('prompt_tokens'), output_tokens=usage.get('completion_tokens'), total_tokens=actual,
            reasoning_tokens=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens'),
            cached_tokens=(usage.get('prompt_tokens_details') or {}).get('cached_tokens', usage.get('prompt_cache_hit_tokens')),
            finish_reason=((data.get('choices') or [{}])[0]).get('finish_reason'))
        if response.status_code >= 400: self.stop = 'PROVIDER_HTTP_' + str(response.status_code)
        self.save()


class Meter(ModelProvider):
    def __init__(self, inner, budget, route, client):
        self.inner, self.budget, self.route = inner, budget, route
        inner.client = client
        for name in ('provider_name', 'model_name', 'base_url', 'timeout', 'max_output_tokens', 'thinking_type', 'governed'):
            setattr(self, name, getattr(inner, name))

    async def generate(self, messages, *, schema):
        self.budget.check()
        repair = messages[0]['content'].startswith('Repair JSON syntax/structural fields only.')
        if not repair:
            if self.budget.logical >= 5: raise BudgetStop('LOGICAL_LIMIT')
            self.budget.logical += 1
        token = CURRENT.set(dict(route=self.route, schema=schema.get('title'), repair=repair, logical=self.budget.logical))
        try: return await self.inner.generate(messages, schema=schema)
        finally: CURRENT.reset(token)


async def run(budget, database):
    if not database.startswith('interview_agent_v14_acceptance_live_') or database == make_url(settings.database_url).database:
        raise BudgetStop('ISOLATED_DATABASE_REQUIRED')
    engine = create_async_engine(make_url(settings.database_url).set(database=database))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    def no_more_web(*args): raise BudgetStop('BODY_INSUFFICIENT_NO_UNPLANNED_FETCH')
    service = WebCollectionService(factory, no_more_web)
    try:
        original = build_model_router()
        if original.fallback_routes or any(p.model_name != MODEL for p in original.routes.values()):
            raise BudgetStop('ROUTE_MISMATCH')
        async with httpx.AsyncClient(event_hooks={'request':[budget.request], 'response':[budget.response]}) as client:
            router = ModelRouter({r:Meter(p,budget,r,client) for r,p in original.routes.items()}, concurrency=original.concurrency)
            documents = [(1,'https://m.job.ncss.cn/student/m/jobs/5j515rAMiAM3eT4g7c7UzK/detail.html?type=null','job_jd'),
                (2,'https://www.nowcoder.com/discuss/427769028658184192?toCommentId=15083088','interview_experience')]
            for search_id,url,kind in documents:
                # Both are third-party/community sources. Neither is promoted
                # to an official employer source just to pass acceptance.
                task = await service.select(search_id,url,kind,'community')
                result = await PublicDataResearchAgent(factory,router).run(task['id'])
                budget.results.append(dict(document_type=kind, source_url=url, **result))
                budget.save()
                print(json.dumps(budget.results[-1],ensure_ascii=False),flush=True)
                if result['status'] != 'COMPLETED': raise BudgetStop('BUSINESS_VALIDATION_FAILED')
        budget.save('EXTRACTED_PENDING_HUMAN_REVIEW')
    finally: await engine.dispose()


async def bounded(budget, database):
    try:
        async with asyncio.timeout(600): await run(budget,database)
    except BaseException as exc:
        budget.stop = budget.stop or getattr(exc,'code',type(exc).__name__) + ':' + str(exc)[:160]
        budget.save('STOPPED')
        print('STOPPED: '+budget.stop,flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True)
    args=parser.parse_args()
    output=ROOT/'.runtime/acceptance/deepseek_web_v141_20261020_attempt1.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as handle: handle.write('{}')
    with (output.parent/'deepseek_web_v141.lock').open('a+b') as lock:
        if lock.tell()==0: lock.write(b'0'); lock.flush()
        lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        try: asyncio.run(bounded(Budget(output),args.database))
        finally:
            lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
