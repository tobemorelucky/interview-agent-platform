"""Request safety: separate pre-send, sent and unknown-outcome model calls.

The five classes this file pins down:
1. a local error raised before any byte is written (payload assembly or a
   client-side hook) - proven unsent;
2. a connection that never opened - proven unsent;
3. a request that was sent and whose response arrived;
4. a request that was sent and whose outcome never came back - UNKNOWN, and
   the same business operation must not be replayed automatically;
5. a response that arrived but was rejected by the business schema - known
   usage, retry is still allowed.
"""
import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from interview_api.infrastructure.models.governance_models import ModelAdmission
from interview_api.infrastructure.models.provider import CompatibleModelProvider, ModelProviderError

from test_m4_interview import run_with_database


def offline(client):
    return CompatibleModelProvider(base_url="https://offline.invalid", api_key="TEST", model="offline",
        timeout_seconds=5, max_output_tokens=64, client=client, governed=True)


async def admission(factory, owner):
    async with factory() as db:
        return (await db.scalars(select(ModelAdmission).where(ModelAdmission.owner_key == owner))).one()


async def call_and_capture(factory, owner, client):
    from interview_api.infrastructure.models.scope import model_scope_for
    with model_scope_for(factory, owner=owner, route="test"):
        with pytest.raises(ModelProviderError) as failure:
            await offline(client).generate([{"role": "user", "content": "x"}], schema={})
    return failure.value


def test_a_local_error_before_the_write_is_not_a_provider_network_failure(database_url):
    async def scenario(factory):
        owner, outbound = "test:" + uuid4().hex, []

        def handler(request):
            outbound.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                                             "usage": {"total_tokens": 10}})

        async def broken_guard(request):
            raise KeyError("category")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     event_hooks={"request": [broken_guard]}) as client:
            error = await call_and_capture(factory, owner, client)
        assert error.code == "model_request_not_sent"
        assert error.request_sent is False and error.usage["total_tokens"] == 0
        assert not outbound  # the transport was never reached
        row = await admission(factory, owner)
        # A proven pre-send failure is a definitive zero, not an UNKNOWN charge.
        assert row.status == "SETTLED" and row.actual_tokens == row.charged_tokens == 0
        assert row.reserved_tokens > 0
    asyncio.run(run_with_database(database_url, scenario))


def test_a_connection_that_never_opened_is_proven_unsent(database_url):
    async def scenario(factory):
        owner = "test:" + uuid4().hex

        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            error = await call_and_capture(factory, owner, client)
        assert error.code == "model_request_not_sent" and error.request_sent is False
        assert error.availability_reason == "provider_connect_failed"
        row = await admission(factory, owner)
        assert row.status == "SETTLED" and row.charged_tokens == 0
    asyncio.run(run_with_database(database_url, scenario))


def test_a_lost_response_keeps_the_conservative_hold(database_url):
    async def scenario(factory):
        owner = "test:" + uuid4().hex

        def handler(request):
            raise httpx.ReadTimeout("response lost", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            error = await call_and_capture(factory, owner, client)
        assert error.code == "model_timeout" and error.request_sent is True
        row = await admission(factory, owner)
        assert row.status == "UNKNOWN" and row.actual_tokens is None
        assert row.charged_tokens == row.reserved_tokens > 0
    asyncio.run(run_with_database(database_url, scenario))


def test_a_received_response_with_business_rejection_is_settled_with_its_usage(database_url):
    async def scenario(factory):
        owner = "test:" + uuid4().hex

        def handler(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "{\"a\":1}"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            error = await call_and_capture(factory, owner, client)
        assert error.code == "model_output_truncated" and error.request_sent is True
        row = await admission(factory, owner)
        assert row.status == "SETTLED" and row.actual_tokens == row.charged_tokens == 10
    asyncio.run(run_with_database(database_url, scenario))


def test_an_unusable_envelope_after_a_response_is_not_called_unsent(database_url):
    async def scenario(factory):
        owner = "test:" + uuid4().hex

        def handler(request):
            return httpx.Response(200, json={"choices": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            error = await call_and_capture(factory, owner, client)
        assert error.code == "model_request_failed" and error.request_sent is True
        row = await admission(factory, owner)
        assert row.status == "UNKNOWN" and row.charged_tokens == row.reserved_tokens
    asyncio.run(run_with_database(database_url, scenario))
