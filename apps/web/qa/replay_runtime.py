"""F3 browser acceptance only: isolated DEMO DB, real APIs/transactions, offline models.

Run from apps/api via uv. No production provider or workflow is modified.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch
from sqlalchemy.engine import make_url
root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root/'apps/api/tests'))
sys.path.insert(0, str(root/'apps/api/scripts'))
from interview_api.core.config import settings
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_COACH_ROUTE, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, JOB_MATCHING_ROUTE
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview_report.service import InterviewReportService
from interview_api.modules.job_matching.service import JobMatchingService
from m6_demo_runtime import seed_demo, DemoMatchProvider, DemoMainProvider
from test_m4_interview import evaluation

class DemoEvaluationProvider(ModelProvider):
    provider_name, model_name = 'replay', 'f3_demo_evaluation_fixture'
    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]['content'])['input']
        assert payload['snapshot']['company_name'].startswith('M6 DEMO REPLAY')
        result = evaluation('NEXT', 4)
        result['evaluation'] = 'DEMO：本轮回答说明了缓存使用方式，建议进一步解释并发更新边界。'
        return ModelResponse(json.dumps(result, ensure_ascii=False), {'total_tokens': 0})

class DemoCoachProvider(ModelProvider):
    provider_name, model_name = 'replay', 'f3_demo_coach_fixture'
    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]['content'])['input']
        assert payload['snapshot']['company_name'].startswith('M6 DEMO REPLAY')
        refs = [e['evidence_ref'] for e in payload['evaluations']]
        assert refs
        finding = lambda text, ids: dict(statement=text, supporting_evaluation_ids=ids)
        result = dict(overall_summary='DEMO：本场回答展示了缓存项目的实践过程，可以继续练习并发边界的表达。',
            strengths=[finding('本场回答说明了缓存的实际使用场景。', refs[:1])],
            weaknesses=[finding('本场回答对并发更新边界说明尚不完整。', refs[:1])],
            recurring_issues=[finding('多轮回答中并发更新边界说明仍不充分。', refs)] if len(refs)>=2 else [],
            job_alignment=[finding('本场回答与岗位缓存要求存在证据交集。', refs[:1])],
            recommended_actions=['复盘缓存失效与并发更新边界。'], practice_plan=['用技术原理、实现步骤、边界条件三个部分练习回答。'],
            memory_proposals=[dict(proposal_type='PREPARATION_FOCUS', content='我希望下一场重点练习缓存并发更新边界。', supporting_evaluation_ids=refs[:1])])
        return ModelResponse(json.dumps(result, ensure_ascii=False), {'total_tokens':0})

if __name__ == '__main__':
    if make_url(settings.database_url).database != 'interview_agent_m6_f3_ux_demo':
        raise SystemExit('Only the explicitly isolated F3 DEMO database is permitted')
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['seed','api'])
    args = parser.parse_args()
    with patch('httpx.AsyncClient.request', side_effect=AssertionError('F3 DEMO outbound HTTP prohibited')):
        if args.action == 'seed': asyncio.run(seed_demo())
        else:
            import uvicorn
            from interview_api.modules.interview import router as interview_router
            from interview_api.modules.interview_report import router as report_router
            from interview_api.modules.job_matching import router as match_router
            interview_router.build_core = lambda: MockInterviewService(async_session_factory, ModelRouter({INTERVIEW_MAIN_ROUTE:DemoMainProvider(), ANSWER_EVALUATION_ROUTE:DemoEvaluationProvider()}))
            report_router.build_core = lambda: InterviewReportService(async_session_factory, ModelRouter({INTERVIEW_COACH_ROUTE:DemoCoachProvider()}))
            match_router.build_core = lambda: JobMatchingService(async_session_factory, ModelRouter({JOB_MATCHING_ROUTE:DemoMatchProvider()}))
            print('F3 DEMO / Replay fixture API; external HTTP blocked; NOT Live model acceptance')
            uvicorn.run('interview_api.main:app', host='127.0.0.1', port=8013)
