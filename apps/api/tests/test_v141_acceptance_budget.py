import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location('v141_batch', Path(__file__).parents[1] / 'scripts/accept_deepseek_web_v141.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def request(model=None):
    return httpx.Request('POST', 'https://offline.invalid/chat/completions',
        json=dict(model=model or module.MODEL, max_tokens=100, messages=[]))


@pytest.mark.parametrize('limit', ['http', 'tokens', 'deadline', 'wrong_model'])
def test_v141_refuses_before_sending(tmp_path, limit):
    b = module.Budget(tmp_path / 'offline.json')
    if limit == 'http': b.http = [{}] * 10
    elif limit == 'tokens': b.tokens = 99999
    elif limit == 'deadline': b.started -= 601
    with pytest.raises(module.BudgetStop):
        asyncio.run(b.request(request('not-authorized' if limit == 'wrong_model' else None)))


def test_missing_usage_keeps_conservative_reservation(tmp_path):
    async def run():
        b = module.Budget(tmp_path / 'offline.json')
        req = request()
        await b.request(req)
        estimate = b.tokens
        await b.response(httpx.Response(200,request=req,json={'choices':[{'finish_reason':'stop'}]}))
        assert b.tokens == estimate > 100
        assert b.http[0]['accounting'] == 'UNKNOWN_CONSERVATIVE'
    asyncio.run(run())
