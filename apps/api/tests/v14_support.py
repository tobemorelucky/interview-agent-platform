"""Explicit local fixture Web transport and Replay model, never LIVE data."""
import json

import httpx

from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, EXTRACT_ROUTE, QUALITY_ROUTE
from interview_api.modules.public_career.tools.contracts import ProviderMode
from interview_api.modules.public_career.tools.deepseek_web import DeepSeekWebClient, DeepSeekSearchProvider, DeepSeekFetchProvider
from interview_api.modules.public_career.tools.factory import build_tool_gateway
from interview_api.modules.public_career.web_audit import WebHTTPAudit
from interview_api.modules.public_career.web_collection import WebCollectionService
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
from interview_api.modules.public_career.research.evidence import EvidenceCatalog
from interview_api.modules.public_career.models import SourceSnapshot
from test_m2b_research_agent import interview_responses

COMPANY = 'DEMO 公开资料验收公司'
TITLE = 'Backend Engineer'
JD_BODY = f'{COMPANY} {TITLE} 职位编号 DEMO-001。\n岗位职责：开发后端服务。\n任职要求：Python experience。\n工作地点：北京。\n' + '此为本地构造的 DEMO fixture，不是真实招聘数据。工程实践与数据库事务。' * 10
EXP_BODY = f'{COMPANY} {TITLE} 技术一面。请解释缓存穿透的处理方案？数据库索引为什么使用B加树？\n' + '此为本地构造 DEMO 面经 fixture，不是真实用户分享。面试官围绕缓存和数据库设计进行提问。' * 10


class FixtureWeb:
    def __init__(self, suffix='demo'):
        self.jd_url = f'https://fixture.example/{suffix}/jd'
        self.exp_url = f'https://community.fixture.example/{suffix}/interview'
        self.company = COMPANY + ' ' + suffix
        self.bodies = {self.jd_url: JD_BODY.replace(COMPANY, self.company), self.exp_url: EXP_BODY.replace(COMPANY, self.company)}
        self.requests = []
        self.need_fetch = False
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))

    def respond(self, request):
        self.requests.append((request.url.path, json.loads(request.content)))
        body = json.loads(request.content)
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json={'results': [dict(title='DEMO JD' if url == self.jd_url else 'DEMO 面经',
                url=url, snippet='DEMO 搜索摘要，不是正文', content=text if body.get('need_content') and not self.need_fetch else '',
                summary='DEMO provider 摘要，不可作为 Evidence', authority=4, rerank_score=3) for url, text in self.bodies.items()]})
        return httpx.Response(200, json={'title': 'DEMO fetched page', 'content': self.bodies[body['url']]})

    def gateway(self, factory, task_id):
        client = DeepSeekWebClient(api_key='DEMO-only-not-a-secret', http=self.http, mode=ProviderMode.REPLAY,
                                  audit=WebHTTPAudit(factory, task_id, mode='REPLAY'))
        return build_tool_gateway(factory, search_provider=DeepSeekSearchProvider(client), fetch_provider=DeepSeekFetchProvider(client))


class FixtureResearchAgent:
    def __init__(self, factory, *, fail=False, unsupported=False):
        self.factory, self.fail, self.unsupported = factory, fail, unsupported

    async def run(self, task_id, *, force=False):
        task = await WebCollectionService(self.factory).detail(task_id)
        async with self.factory() as db:
            snapshot = await db.get(SourceSnapshot, task['manifest']['source_snapshot_id'])
            catalog = EvidenceCatalog([snapshot])
            company = snapshot.content_text.split(' ' + TITLE)[0]
        ids = [next(iter(catalog.spans))]
        if task['target_type'] == 'JOB_JD':
            responses = [dict(company_name=company, job_title=TITLE, identity_evidence_ids=ids, jd_evidence_ids=ids,
                requirements=[dict(statement='UNSUPPORTED fabricated requirement' if self.unsupported else 'Python experience',
                    evidence_ids=ids, requirement_type='SKILL', importance='REQUIRED')],
                attributes=[dict(field='LOCATION', statement='北京', evidence_ids=ids)]), dict(suitable=True, risk_flags=[])]
        else:
            responses = interview_responses(company, ids)
        if self.fail:
            responses = [{}, {}]
        provider = ReplayModelProvider(responses)
        router = ModelRouter({EXTRACT_ROUTE: provider, QUALITY_ROUTE: provider})
        return await PublicDataResearchAgent(self.factory, router).run(task_id, force=force)
