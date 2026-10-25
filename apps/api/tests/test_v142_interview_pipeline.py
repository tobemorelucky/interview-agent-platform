"""PostgreSQL Research -> provenance -> review compatibility at real seams."""
import asyncio
from uuid import uuid4
import pytest

from sqlalchemy import select

from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, EXTRACT_ROUTE, QUALITY_ROUTE
from interview_api.modules.experience.models import InterviewQuestion
from interview_api.modules.experience.review_service import ExperienceReviewService
from interview_api.modules.public_career.models import SourceSnapshot, Claim, ClaimEvidence
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
from interview_api.modules.public_career.research.fragments import InterviewSourceFragments
from interview_api.modules.public_career.web_collection import WebCollectionService
from test_m4_interview import run_with_database
from test_m2b_research_agent import interview_responses
from v14_support import FixtureWeb, TITLE


@pytest.mark.parametrize('bad_identity', [False, True, 'rejected_quality'])
def test_fragment_claim_is_original_and_normalized_display_requires_review(database_url, monkeypatch, bad_identity):
    from interview_api.core.config import settings
    # REPLAY-only DB fixtures may be rerun; live limits are not changed.
    monkeypatch.setattr(settings, 'deepseek_web_daily_search_limit', 10000)
    monkeypatch.setattr(settings, 'deepseek_web_daily_fetch_limit', 10000)
    async def scenario(factory):
        web = FixtureWeb(uuid4().hex)
        body = (f'{web.company} {TITLE} 技术面经\n\n'
                '-怎么构建一个PyTorch网络，优化器有哪些\n\n-又问项目论文\n\n' +
                '这是明确标记 DEMO 的离线回归材料，不是真实面经。\n' * 12 +
                '\n全部评论\n这条评论中的问题是什么？')
        web.bodies[web.exp_url] = body
        service = WebCollectionService(factory, web.gateway)
        try:
            discovery = await service.search(dict(keywords='DEMO', count=5, need_content=True, request_key=uuid4().hex))
            task = await service.select(discovery['id'], web.exp_url, 'interview_experience', 'community')
            async with factory() as db:
                snapshot = await db.get(SourceSnapshot, task['manifest']['source_snapshot_id'])
                catalog = InterviewSourceFragments([snapshot])
            identity = next(iter(catalog.spans))
            question = next(key for key, kind in catalog.kinds.items() if kind == 'QUESTION')
            topic = next(key for key, kind in catalog.kinds.items() if kind == 'REPORTED_TOPIC')
            replies = interview_responses(web.company, [identity])
            if bad_identity is True:
                replies[0]['company'] = '原文未支持的公司'
                replies[1]['company'] = '原文未支持的公司'
            elif bad_identity == 'rejected_quality':
                replies[-1]['reliability_score'] = .1
            replies[0]['questions'] = [dict(fragment_id=question, kind='QUESTION',
                normalized_question='请说明如何用 PyTorch 构建一个网络？', confidence=.9),
                dict(fragment_id=question, kind='QUESTION', normalized_question='如何选择优化器？', confidence=.9),
                dict(fragment_id=topic, kind='REPORTED_TOPIC', normalized_question=None, confidence=.8)]
            provider = ReplayModelProvider(replies)
            result = await PublicDataResearchAgent(factory, ModelRouter({EXTRACT_ROUTE:provider, QUALITY_ROUTE:provider})).run(task['id'])
            if bad_identity is True:
                assert result['status'] == 'FAILED'
                assert 'Company/job identity absent' in result['error']
                assert provider.calls == 1  # reject before routing/reliability
                return
            assert result['status'] == 'COMPLETED', result
            assert result['review_status'] == ('REJECTED' if bad_identity == 'rejected_quality' else 'WAITING_REVIEW')
            assert (await service.select(discovery['id'], web.exp_url, 'interview_experience', 'community'))['id'] == task['id']
            async with factory() as db:
                detail = await ExperienceReviewService(db).get_detail(result['experience_id'])
                assert len(detail['questions']) == 2
                anchors = detail['experience']['extraction_json']['source_anchors']
                assert len(anchors['reported_topics']) == 1
                assert anchors['excluded_fragments'][0]['reason'] == 'comment_or_recommendation_boundary'
                for q in detail['questions']:
                    assert q['source_anchor']['normalization_status'] == 'DERIVED_REQUIRES_REVIEW'
                    persisted = await db.get(InterviewQuestion, q['question_id'])
                    claim = await db.get(Claim, persisted.claim_id)
                    assert claim.statement == '-怎么构建一个PyTorch网络，优化器有哪些'
                    evidence = list((await db.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == claim.id))).all())
                    assert len(evidence) == 1
                    span = evidence[0]
                    assert body[span.source_location_json['start_char']:span.source_location_json['end_char']] == span.excerpt
                with pytest.raises(ValueError, match='source_anchor_evidence_immutable'):
                    await ExperienceReviewService(db).update_question(detail['questions'][0]['question_id'], {'evidence':'伪造原文'})
        finally:
            await web.http.aclose()
    asyncio.run(run_with_database(database_url, scenario))


def test_scoped_out_results_have_audit_but_no_sources_or_body_snapshots(database_url, monkeypatch):
    from interview_api.core.config import settings
    from interview_api.core.exceptions import AppError
    monkeypatch.setattr(settings, 'deepseek_web_daily_search_limit', 10000)
    async def scenario(factory):
        web = FixtureWeb(uuid4().hex)
        service = WebCollectionService(factory, web.gateway)
        try:
            discovery = await service.search(dict(keywords='DEMO', sites=['allowed.example'], count=5,
                need_content=True, request_key=uuid4().hex))
            call = next(c for c in discovery['calls'] if c['tool'] == 'public.search')
            assert call['result']['data']['results'] == []
            assert call['result']['data']['source_filter']['rejected_count'] == 2
            assert call['result']['source_refs'] == call['result']['source_snapshot_refs'] == []
            with pytest.raises(AppError) as exc:
                await service.select(discovery['id'], web.exp_url, 'interview_experience', 'community')
            assert exc.value.code == 'WEB_SOURCE_OUT_OF_SCOPE'
        finally:
            await web.http.aclose()
    asyncio.run(run_with_database(database_url, scenario))
