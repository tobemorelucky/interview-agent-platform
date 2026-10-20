"""Acceptance-runner safeguards only; no database/network/model requests."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import httpx
import pytest

spec = importlib.util.spec_from_file_location("acceptance_budget_script", Path(__file__).parents[1] / "scripts/accept_deepseek_v12_v13.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def request():
    return httpx.Request("POST", "https://offline.invalid/chat/completions", json={
        "model": module.TARGET, "messages": [{"role": "user", "content": "TEST"}], "max_tokens": 128})


@pytest.mark.parametrize("limit", ["http", "token", "deadline"])
def test_budget_refuses_before_send(tmp_path, limit):
    batch = module.Batch(tmp_path / "attempt.json")
    if limit == "http": batch.records = [{}] * 28
    elif limit == "token": batch.charged = 299999
    else: batch.start -= 901
    before = len(batch.records)
    with pytest.raises(module.BudgetStop):
        asyncio.run(batch.on_request(request()))
    assert len(batch.records) == before and batch.reserved == 0


def test_missing_usage_is_not_zero(tmp_path):
    async def run():
        batch = module.Batch(tmp_path / "attempt.json")
        batch.logical = 1
        token = module.CALL.set(dict(logical=1, route="test", schema="Probe", repair=False))
        try:
            req = request()
            await batch.on_request(req)
            estimate = batch.reserved
            await batch.on_response(httpx.Response(200, request=req, json={"choices": [{"finish_reason": "stop"}]}))
            assert batch.charged == estimate > 128 and batch.reserved == 0
            assert batch.records[0]["accounting"] == "UNKNOWN_CONSERVATIVE"
        finally:
            module.CALL.reset(token)
    asyncio.run(run())


def test_logical_limit_refuses_provider_call(tmp_path):
    batch = module.Batch(tmp_path / "attempt.json")
    batch.logical = 14
    async def forbidden(*args, **kwargs):
        raise AssertionError("Must not call provider after logical limit")
    inner = SimpleNamespace(provider_name="offline", model_name=module.TARGET, base_url="https://offline.invalid",
        timeout=1, max_output_tokens=1, thinking_type="disabled", governed=False, generate=forbidden)
    provider = module.MeteredProvider(inner, batch, "test", None)
    with pytest.raises(module.BudgetStop):
        asyncio.run(provider.generate([dict(role="system", content="TEST")], schema={}))
    assert batch.logical == 14
