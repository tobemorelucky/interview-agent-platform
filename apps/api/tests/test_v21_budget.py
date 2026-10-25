"""No network: exact authorized V2.1 batch limits, including repair HTTP attempts."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import httpx
import pytest

spec = importlib.util.spec_from_file_location("v21_live_budget", Path(__file__).parents[1] / "scripts/accept_deepseek_v21.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


@pytest.mark.parametrize("boundary", ["http", "tokens", "deadline"])
def test_v21_budget_rejects_before_network(tmp_path, boundary):
    batch = module.V21Batch(tmp_path / "attempt.json")
    if boundary == "http": batch.records = [{}] * 24
    if boundary == "tokens": batch.charged = 99999
    if boundary == "deadline": batch.start -= 721
    request = httpx.Request("POST", "https://offline.invalid/chat/completions", json={
        "model": module.TARGET, "messages": [], "max_tokens": 128})
    with pytest.raises(module.BudgetStop): asyncio.run(batch.on_request(request))
    assert batch.reserved == 0


def test_v21_logical_limit_is_twelve_not_old_fourteen(tmp_path):
    batch = module.V21Batch(tmp_path / "attempt.json")
    batch.logical = 12
    async def forbidden(*a, **k): raise AssertionError("No network allowed")
    inner = SimpleNamespace(provider_name="offline", model_name=module.TARGET, base_url="https://offline.invalid",
        timeout=1, max_output_tokens=128, thinking_type="disabled", governed=False, generate=forbidden)
    provider = module.V21Provider(inner, batch, "test", None)
    with pytest.raises(module.BudgetStop):
        asyncio.run(provider.generate([dict(role="system", content="Test")], schema={}))
    assert batch.logical == 12
