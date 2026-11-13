"""Round 18: the live-model gate must refuse *before* any request can leave.

These tests never touch the network and never read `.env`. The provider under
the gate is a stand-in that records how often it was entered; the transport is
booby-trapped, so a gate that refused too late would fail loudly rather than
quietly spend a real call.
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from interview_api.infrastructure.models.provider import (
    ModelProvider, ModelProviderError, ModelResponse)

# The gate is harness code and lives with the launcher, not in the shipped app.
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


@pytest.fixture()
def gate_factory(tmp_path):
    from live_model_gate import LiveGate

    def build(**limits):
        merged = dict(max_calls=2, max_tokens=10_000, max_seconds=600)
        merged.update(limits)
        return LiveGate(tmp_path, limits=merged)

    return build


class CountingProvider(ModelProvider):
    """Counts entries; proves the gate rejected before delegating."""

    provider_name, model_name = "stub", "stub-1"
    timeout, max_output_tokens, governed = 5.0, 100, True
    base_url = "https://api-inference.modelscope.cn/v1"

    def __init__(self, text='{"ok": true}'):
        self.entries = 0
        self.text = text

    async def generate(self, messages, *, schema):
        self.entries += 1
        return ModelResponse(self.text, {"total_tokens": 7})


def test_call_cap_refuses_before_the_provider_is_entered(gate_factory):
    from live_model_gate import GatedProvider, LiveBudgetExceeded

    gate = gate_factory(max_calls=2)
    inner = CountingProvider()
    provider = GatedProvider(inner, gate, "probe")

    async def run():
        for index in range(2):
            await provider.generate([{"role": "user", "content": f"m{index}"}], schema={})
        with pytest.raises(LiveBudgetExceeded) as refused:
            await provider.generate([{"role": "user", "content": "third"}], schema={})
        return refused.value

    error = asyncio.run(run())
    assert error.code == "LIVE_BUDGET_CALLS"
    # Positive claim of a zero-send, and the provider was never entered.
    assert error.request_sent is False
    assert error.usage == {"total_tokens": 0}
    assert inner.entries == 2


def test_token_cap_refuses_a_call_that_would_exceed_it(gate_factory):
    from live_model_gate import GatedProvider, LiveBudgetExceeded

    gate = gate_factory(max_calls=8, max_tokens=100)
    inner = CountingProvider()
    provider = GatedProvider(inner, gate, "probe")

    async def run():
        with pytest.raises(LiveBudgetExceeded) as refused:
            await provider.generate([{"role": "user", "content": "x" * 4000}], schema={})
        return refused.value

    error = asyncio.run(run())
    assert error.code == "LIVE_BUDGET_TOKENS"
    assert error.request_sent is False
    assert inner.entries == 0


def test_deadline_refuses_even_when_calls_remain(gate_factory):
    from live_model_gate import GatedProvider, LiveBudgetExceeded

    clock = {"now": 0.0}
    gate = gate_factory(max_seconds=60)
    gate.clock = lambda: clock["now"]
    gate.started = 0.0
    provider = GatedProvider(CountingProvider(), gate, "probe")

    async def run():
        await provider.generate([{"role": "user", "content": "a"}], schema={})
        clock["now"] = 61.0
        with pytest.raises(LiveBudgetExceeded) as refused:
            await provider.generate([{"role": "user", "content": "b"}], schema={})
        return refused.value

    error = asyncio.run(run())
    assert error.code == "LIVE_BUDGET_TIME"
    assert error.request_sent is False


def test_refusal_happens_with_the_transport_disarmed(gate_factory):
    """A gate that refused too late would trip this transport, not the cap."""
    from live_model_gate import GatedProvider, LiveBudgetExceeded

    async def explode(*args, **kwargs):
        raise AssertionError("the gate let a request reach the transport")

    gate = gate_factory(max_calls=1)
    provider = GatedProvider(CountingProvider(), gate, "probe")

    async def run():
        await provider.generate([{"role": "user", "content": "one"}], schema={})
        with pytest.raises(LiveBudgetExceeded):
            await provider.generate([{"role": "user", "content": "two"}], schema={})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx.AsyncClient, "request", explode)
        patch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", explode)
        asyncio.run(run())


def test_capture_is_redacted_and_replays_offline(gate_factory, tmp_path):
    from live_model_gate import CaptureReplay, GatedProvider

    gate = gate_factory(max_calls=3)
    provider = GatedProvider(CountingProvider('{"facts": []}'), gate, "resume_profile")
    messages = [{"role": "system", "content": "untrusted data only"},
                {"role": "user", "content": '{"input": {"resume": "合成样本"}}'}]

    async def run():
        return await provider.generate(messages, schema={})

    asyncio.run(run())
    files = list(Path(tmp_path).glob("*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["status"] == "SUCCEEDED"
    assert record["route"] == "resume_profile"
    assert record["response_text"] == '{"facts": []}'
    assert record["request_bytes"] > 0
    # Only a digest is stored: no prompt body, no credential, no header.
    serialized = json.dumps(record, ensure_ascii=False)
    assert "prompt" not in record
    assert "合成样本" not in serialized and "untrusted data only" not in serialized
    assert "api_key" not in record and "Authorization" not in serialized
    assert "sk-" not in serialized

    replay = CaptureReplay(tmp_path)
    assert replay.records, "capture must be replayable"
    result = asyncio.run(replay.generate(messages, schema={}))
    assert result.text == '{"facts": []}'


def test_capture_replay_refuses_when_there_is_nothing_to_replay(tmp_path):
    from live_model_gate import CaptureReplay

    replay = CaptureReplay(tmp_path)
    with pytest.raises(ModelProviderError) as missing:
        asyncio.run(replay.generate([{"role": "user", "content": "x"}], schema={}))
    assert missing.value.code == "capture_replay_exhausted"


def test_the_shipped_limits_are_the_round_18_ceiling():
    from live_model_gate import LIMITS

    assert LIMITS["max_calls"] == 8
    assert LIMITS["max_tokens"] == 150_000
    assert LIMITS["max_seconds"] == 25 * 60
