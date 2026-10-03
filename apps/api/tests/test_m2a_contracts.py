from __future__ import annotations

import asyncio

import httpx

from interview_api.modules.experience.search.searxng import SearxngSearchProvider
from interview_api.modules.public_career.tools.contracts import ProviderMode
from interview_api.modules.public_career.tools.providers import (
    FetchProvider,
    SearchProvider,
)
from interview_api.modules.public_career.tools.replay import (
    ReplayFetchProvider,
    ReplaySearchProvider,
)


def test_replay_providers_implement_shared_contract_without_network(monkeypatch) -> None:
    async def forbidden_send(*_args, **_kwargs):
        raise AssertionError("Replay attempted network access")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_send)

    async def run() -> None:
        search = ReplaySearchProvider()
        fetch = ReplayFetchProvider()
        assert isinstance(search, SearchProvider)
        assert isinstance(fetch, FetchProvider)
        assert search.provider_mode == ProviderMode.REPLAY
        assert fetch.provider_mode == ProviderMode.REPLAY
        results = await search.search("graduate", time_window_hours=24, max_results=5)
        assert results
        fetched = await fetch.fetch(results[0].url)
        assert fetched.ok
        missing = await fetch.fetch("https://example.com/not-in-fixture")
        assert missing.error_code == "replay_fixture_not_found"

    asyncio.run(run())


def test_searxng_adapter_implements_shared_search_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Backend Interview",
                        "url": "https://example.com/a?utm_source=test",
                        "content": "backend interview experience",
                        "engine": "fixture",
                    },
                    {
                        "title": "Duplicate",
                        "url": "https://example.com/a?utm_campaign=other",
                    },
                ]
            },
        )

    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            provider = SearxngSearchProvider(
                base_url="https://searx.example", client=client
            )
            assert isinstance(provider, SearchProvider)
            assert provider.provider_mode == ProviderMode.LIVE
            results = await provider.search(
                "backend", time_window_hours=24, max_results=10
            )
            assert len(results) == 1
            assert results[0].url == "https://example.com/a"
        finally:
            await client.aclose()

    asyncio.run(run())
