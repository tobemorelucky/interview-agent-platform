import asyncio
import httpx

from interview_api.modules.public_career.tools.deepseek_web import DeepSeekWebClient, DeepSeekSearchProvider


def test_hostname_scope_is_enforced_after_provider_response():
    async def scenario():
        urls = ['https://nowcoder.com/a', 'https://www.nowcoder.com/b',
                'https://nowcoder.com.evil.example/a', 'https://evilnowcoder.com/a',
                'https://nowcoder.com@evil.example/a', 'http://127.0.0.1/a',
                'https://www.nowcoder.com/redirect?url=https://evil.example/a']
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
                json={'results': [dict(url=u, title='DEMO', content='actual') for u in urls]}))) as http:
            provider = DeepSeekSearchProvider(DeepSeekWebClient(api_key='test-only', http=http))
            result = await provider.search_with_options('面经', max_results=10, sites=['nowcoder.com'], need_content=True)
            assert [item.url for item in result] == urls[:2]
            assert provider.last_search_diagnostics['rejected_count'] == 5
            assert all(item['reason'] for item in provider.last_search_diagnostics['rejected'])
    asyncio.run(scenario())


def test_unscoped_search_still_accepts_different_public_domains():
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
                json={'results': [dict(url='https://one.example/a'), dict(url='https://two.example/b')]}))) as http:
            provider = DeepSeekSearchProvider(DeepSeekWebClient(api_key='test-only', http=http))
            result = await provider.search_with_options('DEMO', max_results=2)
            assert len(result) == 2 and provider.last_search_diagnostics['rejected_count'] == 0
    asyncio.run(scenario())


def test_empty_or_denied_fetch_body_is_not_a_successful_snapshot():
    from interview_api.modules.public_career.tools.deepseek_web import DeepSeekFetchProvider
    from interview_api.modules.public_career.tools.errors import ToolExecutionError
    import pytest
    async def scenario():
        for content in ('', '# Access denied\nPlease complete captcha'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
                    json={'title':'Denied', 'content':content}))) as http:
                with pytest.raises(ToolExecutionError):
                    await DeepSeekFetchProvider(DeepSeekWebClient(api_key='test-only', http=http)).fetch('https://example.com/article')
    asyncio.run(scenario())
