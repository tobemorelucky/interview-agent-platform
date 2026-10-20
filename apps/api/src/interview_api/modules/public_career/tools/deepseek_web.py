"""Official DeepSeek Web API adapters; independent of model routing/credentials.

Only the HTTPS service receives requests. Target redirects are handled by that
service, not our local fetcher; its 403/access policy is never bypassed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import re
from time import perf_counter

import httpx

from interview_api.core.config import settings
from interview_api.core.url_safety import canonicalize_public_url, URLSafetyError
from .contracts import ProviderMode
from .errors import ToolExecutionError
from .providers import FetchProvider, FetchResult, SearchProvider, SearchResult

BASE_URL = "https://api.deepseek.com/v1/tools"
ERRORS = {400: 'invalid_request', 401: 'authentication_failed', 402: 'insufficient_balance',
          403: 'access_denied', 404: 'content_unavailable', 408: 'request_timeout',
          413: 'response_too_large', 429: 'rate_limited'}


def search_query(query, *, sites=(), published_after=None, published_before=None):
    query = query.strip()
    if sites:
        if any(not re.fullmatch(r'[A-Za-z0-9.-]+', site) or '.' not in site for site in sites):
            raise ToolExecutionError('invalid_search_site', '搜索来源必须为公开域名')
        query += ' (' + ' OR '.join('site:' + site.lower() for site in sorted(set(sites))) + ')'
    for name, value in [('after', published_after), ('before', published_before)]:
        if value:
            try:
                datetime.strptime(value, '%Y-%m-%d')
            except ValueError as exc:
                raise ToolExecutionError('invalid_search_date', '日期须为 YYYY-MM-DD') from exc
            query += f' {name}:{value}'
    if not 1 <= len(query) <= 128:
        raise ToolExecutionError('invalid_search_query', '包含筛选条件的搜索词不得超过128字')
    return query


class DeepSeekWebClient:
    def __init__(self, *, api_key=None, http=None, audit=None, retries=None, mode=ProviderMode.LIVE):
        self.api_key = settings.deepseek_web_api_key if api_key is None else api_key
        self.http = http
        self.audit = audit
        self.mode = mode
        self.retries = settings.deepseek_web_retries if retries is None else retries

    async def request(self, kind, payload):
        if self.mode == ProviderMode.REPLAY and (self.http is None or not isinstance(getattr(self.http, '_transport', None), httpx.MockTransport)):
            raise ToolExecutionError('replay_network_forbidden', 'Replay Web Provider 必须使用无外网 MockTransport')
        if not self.api_key:
            raise ToolExecutionError('deepseek_web_key_missing', '未配置独立的 DeepSeek 官方 Web API Key')
        if kind not in {'search', 'fetch'}:
            raise ValueError('Unsupported Web operation')
        history = []
        for attempt in range(self.retries + 1):
            ticket = await self.audit.reserve(kind, payload) if self.audit else None
            started = perf_counter()
            status = None
            error = None
            retry_after = 0
            data = None
            try:
                if self.http is not None:
                    data, status, retry_after = await self._send(self.http, kind, payload)
                else:
                    async with httpx.AsyncClient(timeout=settings.deepseek_web_timeout_seconds,
                                                 follow_redirects=False, trust_env=False) as http:
                        data, status, retry_after = await self._send(http, kind, payload)
                if status != 200:
                    error = ToolExecutionError('deepseek_web_' + ERRORS.get(status, 'provider_error'),
                                               f'DeepSeek {kind} 返回 HTTP {status}', details={'http_status': status})
                elif not isinstance(data, dict):
                    error = ToolExecutionError('deepseek_web_invalid_response', 'Web API 返回无效结构')
            except asyncio.CancelledError:
                if self.audit:
                    await asyncio.shield(self.audit.finish(ticket, 'UNKNOWN', dict(error_code='cancelled')))
                raise
            except ValueError:
                error = ToolExecutionError('deepseek_web_invalid_response', 'Web API 返回无效 JSON；不自动重复付费请求')
            except httpx.HTTPError as exc:
                error = ToolExecutionError('deepseek_web_timeout' if isinstance(exc, httpx.TimeoutException)
                                           else 'deepseek_web_transport_error', 'Web API 未返回可用响应')
            item = dict(attempt=attempt + 1, http_status=status,
                        latency_ms=round((perf_counter() - started) * 1000),
                        error_code=error.code if error else None)
            history.append(item)
            if self.audit:
                await self.audit.finish(ticket, 'FAILED' if error else 'SUCCEEDED', item)
            if not error:
                return data, dict(provider='deepseek_web', acquisition_mode='LIVE_SEARCH' if self.mode == ProviderMode.LIVE else 'REPLAY',
                                  collected_at=datetime.now(timezone.utc).isoformat(), http_attempts=history)
            retryable = status in {408, 429, 500, 502, 503, 504} or error.code in {
                'deepseek_web_timeout', 'deepseek_web_transport_error'}
            # Never retry billable-invalid 2xx or permanent/permission/balance errors.
            if not retryable or attempt == self.retries:
                error.details['http_attempts'] = history
                raise error
            # Large Retry-After is a stop condition, not a reason to hammer/long-wait.
            if retry_after > 5:
                error.details['http_attempts'] = history
                raise error
            await asyncio.sleep(max(retry_after, 0.5 * 2 ** attempt))

    async def _send(self, http, kind, payload):
        async with http.stream('POST', f'{BASE_URL}/{kind}', json=payload,
                               headers={'Authorization': f'Bearer {self.api_key}',
                                        'User-Agent': 'InterviewAgentPlatform-CareerCollector/1.4'},
                               timeout=settings.deepseek_web_timeout_seconds,
                               follow_redirects=False) as response:
            try:
                retry_after = max(0, float(response.headers.get('retry-after', '0')))
            except ValueError:
                retry_after = 6
            if response.status_code != 200:
                # Do not log raw provider error bodies (they can echo credentials).
                return None, response.status_code, retry_after
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 4 * 1024 * 1024:
                    return None, 413, 0
            try:
                return json.loads(body), 200, 0
            except ValueError:
                return None, 200, 0


class DeepSeekSearchProvider(SearchProvider):
    provider_name = 'deepseek_web'

    @property
    def provider_mode(self):
        return self.client.mode

    def __init__(self, client=None):
        self.client = client or DeepSeekWebClient()

    async def search(self, query, *, time_window_hours, max_results):
        # Compatibility contract: explicit dates supersede legacy time windows;
        # do not invent a remote publication timestamp.
        return await self.search_with_options(query, max_results=max_results, need_content=False)

    async def search_with_options(self, query, *, max_results, need_content=False, sites=(),
                                  published_after=None, published_before=None):
        query = search_query(query, sites=sites, published_after=published_after, published_before=published_before)
        if not 1 <= max_results <= 50:
            raise ToolExecutionError('invalid_search_count', 'DeepSeek 搜索数量范围为1–50')
        body, audit = await self.client.request('search', dict(query=query, count=max_results, need_content=need_content))
        if not isinstance(body.get('results'), list):
            raise ToolExecutionError('deepseek_web_invalid_response', '缺少搜索结果列表')
        results = []
        seen = set()
        for item in body['results'][:max_results]:
            if not isinstance(item, dict):
                continue
            try:
                url = canonicalize_public_url(item.get('url', ''))
            except (URLSafetyError, TypeError):
                continue
            if url in seen:
                continue
            seen.add(url)
            content = item.get('content') if need_content else None
            if not isinstance(content, str) or len(content) > settings.deepseek_web_max_content_chars:
                content = None
            results.append(SearchResult(title=str(item.get('title') or '')[:1000], url=url,
                snippet=str(item.get('snippet') or '')[:4000], engine='deepseek_web',
                published_at=item.get('date') if isinstance(item.get('date'), str) else None,
                content=content or None, metadata={**audit, 'query': query, 'original_url': item['url'],
                    **{k: item.get(k) for k in ('summary', 'authority', 'rerank_score', 'site_name', 'date')},
                    'trust_note': 'ranking_signals_not_truth'}))
        return results


class DeepSeekFetchProvider(FetchProvider):
    provider_name = 'deepseek_web'

    @property
    def provider_mode(self):
        return self.client.mode

    def __init__(self, client=None):
        self.client = client or DeepSeekWebClient()

    async def fetch(self, url):
        url = canonicalize_public_url(url)
        body, audit = await self.client.request('fetch', {'url': url})
        text = body.get('content')
        if not isinstance(text, str) or not text.strip():
            raise ToolExecutionError('deepseek_web_empty_body', '目标网页没有可用公开正文')
        if len(text) > settings.deepseek_web_max_content_chars:
            raise ToolExecutionError('deepseek_web_body_too_large', '正文超过本项目采集上限')
        return FetchResult(url=url, final_url=url, status_code=200, content_type='text/markdown',
            title=str(body.get('title') or '')[:1000], raw_text=text, fetch_method='deepseek_web',
            metadata={**audit, 'url_semantics': 'submitted_url_provider_does_not_return_redirect_chain'})
