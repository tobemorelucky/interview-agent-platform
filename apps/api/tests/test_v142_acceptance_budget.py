import asyncio
import importlib.util
from pathlib import Path
import sys

import httpx
import pytest

scripts = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location('v142_acceptance', scripts / 'accept_deepseek_web_v142.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('limit', ['http', 'tokens', 'deadline', 'wrong_model'])
def test_v142_budget_refuses_before_network(tmp_path, limit):
    budget = module.Budget(tmp_path / 'offline.json')
    if limit == 'http': budget.http = [{}] * 8
    elif limit == 'tokens': budget.tokens = 39999
    elif limit == 'deadline': budget.started -= 601
    request = httpx.Request('POST', 'https://offline.invalid/chat/completions',
        json=dict(model='not-authorized' if limit == 'wrong_model' else module.MODEL, max_tokens=100))
    with pytest.raises(module.BudgetStop):
        asyncio.run(budget.request(request))


def test_v142_unknown_usage_is_not_zero(tmp_path):
    async def scenario():
        budget = module.Budget(tmp_path / 'offline.json')
        request = httpx.Request('POST', 'https://offline.invalid/chat/completions', json=dict(model=module.MODEL, max_tokens=100))
        await budget.request(request)
        reserved = budget.tokens
        await budget.response(httpx.Response(200, request=request, json={'choices':[{'finish_reason':'stop'}]}))
        assert budget.tokens == reserved > 0
    asyncio.run(scenario())
