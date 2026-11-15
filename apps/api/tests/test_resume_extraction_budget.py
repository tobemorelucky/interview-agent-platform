"""A truncated model body must be named as truncation, not as bad JSON.

Round 9 uploaded a synthetic resume through the real UI and the worker failed
with ``Unterminated string starting at: line 1 column 2012``: the extraction
route sent no thinking policy, so the reasoning pass consumed the whole 6000
token output budget and cut the JSON mid-string. These tests fix the meaning
of that failure and the text a user is allowed to see.
"""

import asyncio
from types import SimpleNamespace

import pytest

from interview_api.core.errors import AppError


class _Completions:
    def __init__(self, response, seen):
        self._response, self._seen = response, seen

    async def create(self, **kwargs):
        self._seen.append(kwargs)
        return self._response


class _Client:
    def __init__(self, response, seen):
        self.chat = SimpleNamespace(completions=_Completions(response, seen))


def _provider(monkeypatch, *, thinking_type=None, finish_reason="stop", content='{"ok": true}'):
    from interview_api.infrastructure.llm import provider as module

    monkeypatch.setattr(module, "modelscope_connection", lambda: {
        "base_url": "http://offline.local/v1", "api_key": "offline", "model": "offline-model"})
    seen = []
    provider = module.OpenAICompatibleLLMProvider(user_id=1, thinking_type=thinking_type)
    provider._client = _Client(SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason, message=SimpleNamespace(content=content))], usage=None), seen)

    async def passthrough(_provider, _messages, operation):
        return await operation()

    monkeypatch.setattr(module, "governed_call", passthrough)
    return provider, seen


def test_length_finish_reason_is_a_truncation_not_a_parse_error(monkeypatch):
    provider, _ = _provider(monkeypatch, finish_reason="length", content='{"basic_info": {"name": "张')
    with pytest.raises(AppError) as raised:
        asyncio.run(provider.chat([{"role": "user", "content": "x"}]))
    assert raised.value.code == "MODEL_OUTPUT_TRUNCATED"
    assert raised.value.request_sent is True


def test_the_extraction_call_pins_thinking_off(monkeypatch):
    provider, seen = _provider(monkeypatch, thinking_type="disabled")
    asyncio.run(provider.chat([{"role": "user", "content": "x"}]))
    assert seen[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_a_provider_without_a_policy_sends_no_thinking_body(monkeypatch):
    provider, seen = _provider(monkeypatch)
    asyncio.run(provider.chat([{"role": "user", "content": "x"}]))
    assert seen[0]["extra_body"] is None


def test_user_facing_failure_text_hides_the_internal_exception():
    from interview_api.modules.resume import processor

    try:
        raise AppError("MODEL_OUTPUT_TRUNCATED", "模型输出被截断，未生成完整结果；请重试", 503)
    except AppError:
        text = processor._format_error()
    assert "JSONDecodeError" not in text and "Unterminated" not in text
    assert "截断" in text

    try:
        raise ValueError("Unterminated string starting at: line 1 column 2012")
    except ValueError:
        generic = processor._format_error()
    assert "Unterminated" not in generic and "ValueError" not in generic
