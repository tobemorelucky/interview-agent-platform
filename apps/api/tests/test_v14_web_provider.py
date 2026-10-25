import asyncio
import json

import httpx
import pytest

from interview_api.modules.public_career.tools.deepseek_web import DeepSeekWebClient, DeepSeekSearchProvider, DeepSeekFetchProvider, search_query
from interview_api.modules.public_career.tools.contracts import ProviderMode
from interview_api.modules.public_career.tools.errors import ToolExecutionError


def test_search_normalizes_only_real_content_and_keeps_reference_signals():
    async def run():
        def respond(request):
            body = json.loads(request.content)
            assert body['need_content'] is True
            assert 'site:example.com' in body['query']
            return httpx.Response(200, json={'results': [dict(title='JD', url='https://example.com/jd?utm_source=x',
                snippet='short', summary='not body', content='actual page body', authority=3, rerank_score=2.1,
                date='2026-10-01T00:00:00Z')]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            provider = DeepSeekSearchProvider(DeepSeekWebClient(api_key='test-only', http=http, mode=ProviderMode.REPLAY))
            result = await provider.search_with_options('工程师', max_results=2, sites=['example.com'], need_content=True)
            assert result[0].url == 'https://example.com/jd'
            assert result[0].content == 'actual page body'
            assert result[0].metadata['authority'] == 3
            assert result[0].metadata['summary'] == 'not body'
    asyncio.run(run())


@pytest.mark.parametrize('status,code', [(401, 'authentication_failed'), (402, 'insufficient_balance'),
    (403, 'access_denied'), (404, 'content_unavailable'), (413, 'response_too_large')])
def test_permanent_errors_are_never_retried_or_echoed(status, code):
    async def run():
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(status, json={'error': 'SENSITIVE_PROVIDER_RESPONSE'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            with pytest.raises(ToolExecutionError) as exc:
                await DeepSeekWebClient(api_key='test-only', http=http).request('fetch', {'url': 'https://example.com/'})
            assert exc.value.code == 'deepseek_web_' + code
            assert 'SENSITIVE' not in str(exc.value)
            assert len(calls) == 1
    asyncio.run(run())


def test_429_has_finite_retry_and_records_each_attempt():
    async def run():
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, json={'title': 'JD', 'content': 'real body'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            result = await DeepSeekFetchProvider(DeepSeekWebClient(api_key='test-only', http=http)).fetch('https://example.com/jd')
            assert result.ok and len(calls) == 2
            assert [a['http_status'] for a in result.metadata['http_attempts']] == [429, 200]
    asyncio.run(run())


def test_replay_cannot_create_real_network_and_missing_key_does_not_send():
    async def run():
        with pytest.raises(ToolExecutionError, match='MockTransport'):
            await DeepSeekWebClient(api_key='test-only', mode=ProviderMode.REPLAY).request('search', {})
        with pytest.raises(ToolExecutionError) as exc:
            await DeepSeekWebClient(api_key='').request('search', {})
        assert exc.value.code == 'deepseek_web_key_missing'
    asyncio.run(run())


def test_snippet_or_summary_is_not_body_and_private_results_are_rejected():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'results': [
            {'url': 'http://127.0.0.1/secret', 'content': 'private'},
            {'url': 'https://example.com/a', 'snippet': 'snippet', 'summary': 'summary'}]}))) as http:
            provider = DeepSeekSearchProvider(DeepSeekWebClient(api_key='test-only', http=http))
            results = await provider.search_with_options('JD', max_results=2, need_content=True)
            assert len(results) == 1 and results[0].content is None
    asyncio.run(run())


@pytest.mark.parametrize('response', [httpx.Response(200, content=b'not json'), httpx.Response(200, json={})])
def test_invalid_200_is_not_retried(response):
    async def run():
        calls = []
        def respond(request):
            calls.append(request)
            return response
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            with pytest.raises(ToolExecutionError):
                await DeepSeekSearchProvider(DeepSeekWebClient(api_key='test-only', http=http)).search_with_options('JD', max_results=1)
            assert len(calls) == 1
    asyncio.run(run())


def test_search_filters_are_official_query_syntax_not_invented_body_fields():
    from interview_api.modules.public_career.research.evidence import EvidenceCatalog
    from interview_api.modules.public_career.models import SourceSnapshot
    catalog = EvidenceCatalog([SourceSnapshot(id=100, source_id=1, content_text='abcdefghij')], span_chars=5)
    ids = list(catalog.spans)
    assert catalog.supports_literal(ids, 'defgh')
    assert not catalog.supports_literal(ids[:1], 'defgh')
    separate = EvidenceCatalog([SourceSnapshot(id=1, source_id=1, content_text='abc'),
                                SourceSnapshot(id=2, source_id=2, content_text='def')], span_chars=5)
    assert not separate.supports_literal(list(separate.spans), 'cde')
    from interview_api.modules.public_career.research.prompts import WebSourcePromptRegistry
    prompts = WebSourcePromptRegistry()
    assert prompts.get('quality').version == '1.1.0'
    assert 'Extract public interview questions' not in prompts.get('quality').system
    assert prompts.get('interview_extract').version == '1.2.0'
    assert 'Select and classify existing source_fragments' in prompts.get('interview_extract').system
    assert 'Extract public interview questions' in prompts.get('interview_extract', '1.1.0').system
    assert search_query('Python', sites=['example.com'], published_after='2026-01-01') == 'Python (site:example.com) after:2026-01-01'
    with pytest.raises(ToolExecutionError): search_query('a' * 129)
    with pytest.raises(ToolExecutionError): search_query('JD', sites=['example.com bad'])
    with pytest.raises(ToolExecutionError): search_query('JD', published_after='tomorrow')


def test_timeout_retries_are_bounded_and_fetch_does_not_follow_private_targets():
    async def run():
        calls = []
        def respond(request):
            calls.append(request)
            raise httpx.ReadTimeout('bounded', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            provider = DeepSeekFetchProvider(DeepSeekWebClient(api_key='test-only', http=http))
            with pytest.raises(ValueError): await provider.fetch('http://10.0.0.1/private')
            assert len(calls) == 0
            with pytest.raises(ToolExecutionError) as exc: await provider.fetch('https://example.com/')
            assert exc.value.code == 'deepseek_web_timeout' and len(calls) == 2
    asyncio.run(run())
