from __future__ import annotations

import asyncio

import httpx

from interview_api.core.url_safety import URLSafetyPolicy
from interview_api.modules.experience.fetchers.httpx_fetcher import HttpxContentFetcher


async def _public_resolver(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


def test_private_ip_is_rejected_before_network() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async def run() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        )
        try:
            result = await HttpxContentFetcher(client=client).fetch(
                "http://127.0.0.1/private"
            )
            assert result.error_code == "private_network_url"
            assert result.error_message == "ssrf_blocked"
            assert not called
        finally:
            await client.aclose()

    asyncio.run(run())


def test_redirect_target_is_revalidated_before_following() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://10.0.0.1/secret"})

    async def run() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        )
        try:
            fetcher = HttpxContentFetcher(
                client=client, safety_policy=URLSafetyPolicy(_public_resolver)
            )
            result = await fetcher.fetch("https://public.example/start")
            assert result.error_code == "private_network_url"
            assert len(requests) == 1
        finally:
            await client.aclose()

    asyncio.run(run())


def test_timeout_and_oversized_response_are_structured() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    def oversized_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "9999"},
            content=b"ignored",
        )

    async def run() -> None:
        policy = URLSafetyPolicy(_public_resolver)
        timeout_client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
        oversized_client = httpx.AsyncClient(
            transport=httpx.MockTransport(oversized_handler)
        )
        try:
            timed_out = await HttpxContentFetcher(
                client=timeout_client, safety_policy=policy
            ).fetch("https://public.example/timeout")
            oversized = await HttpxContentFetcher(
                client=oversized_client,
                safety_policy=policy,
                max_response_bytes=100,
            ).fetch("https://public.example/large")
            assert timed_out.error_code == "fetch_timeout"
            assert oversized.error_code == "response_too_large"
        finally:
            await timeout_client.aclose()
            await oversized_client.aclose()

    asyncio.run(run())


def test_safe_fetch_accepts_public_html_and_extracts_body() -> None:
    body = "Public career information. " * 20

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=f"<html><head><title>Career Page</title></head><body><main>{body}</main></body></html>",
        )

    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await HttpxContentFetcher(
                client=client,
                safety_policy=URLSafetyPolicy(_public_resolver),
            ).fetch("https://public.example/career")
            assert result.ok
            assert result.title == "Career Page"
            assert result.raw_text and "Public career information" in result.raw_text
        finally:
            await client.aclose()

    asyncio.run(run())
