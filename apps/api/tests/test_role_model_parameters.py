"""Role-level thinking and output budget: configured, forwarded, and actually sent.

The round-4 live batch showed two separate defects: the ModelScope branch dropped
the configured thinking parameter, and an enabled role kept the 3000-token output
cap so all of it was spent on reasoning. No network here: the request body is
captured with a local transport.
"""
import json

import httpx
import pytest

from interview_api.core.config import settings
from interview_api.infrastructure.models.provider import ModelScopeModelProvider
from interview_api.infrastructure.models.router import (ANSWER_EVALUATION_ROUTE, INTERVIEW_MAIN_ROUTE,
    _chat_provider, build_interview_router)


def modelscope(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "modelscope")
    monkeypatch.setattr(settings, "modelscope_api_key", type(settings.modelscope_api_key)("test-key"))
    monkeypatch.setattr(settings, "modelscope_model", "deepseek-ai/DeepSeek-V4.1-Flash-1017")


def capture_body(provider, payload=b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}],"usage":{"total_tokens":1}}'):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=json.loads(payload))

    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return seen


@pytest.mark.parametrize("thinking,cap", [("enabled", 12000), ("disabled", 3000), (None, 3000)])
def test_enabled_roles_get_reasoning_headroom(monkeypatch, thinking, cap):
    monkeypatch.setattr(settings, "thinking_output_tokens", 12000)
    provider = _chat_provider(base_url="https://example.invalid/v1", api_key="k", model="m",
        timeout_seconds=1, max_output_tokens=3000, thinking_type=thinking)
    assert provider.max_output_tokens == cap
    assert provider.thinking_type == thinking


def test_modelscope_no_longer_drops_the_configured_thinking(monkeypatch):
    modelscope(monkeypatch)
    provider = _chat_provider(base_url="https://ignored.invalid", api_key="ignored", model="ignored",
        timeout_seconds=1, max_output_tokens=3000, thinking_type="enabled")
    assert isinstance(provider, ModelScopeModelProvider)
    assert provider.thinking_type == "enabled"
    assert provider.max_output_tokens == 12000
    assert provider.base_url == "https://api-inference.modelscope.cn/v1"


def test_the_request_body_carries_thinking_and_the_raised_cap(monkeypatch):
    monkeypatch.setattr(settings, "thinking_output_tokens", 12000)
    provider = ModelScopeModelProvider(base_url="https://api-inference.modelscope.cn/v1", api_key="k",
        model="deepseek-ai/DeepSeek-V4.1-Flash-1017", timeout_seconds=1, max_output_tokens=12000,
        thinking_type="enabled")
    seen = capture_body(provider)
    import asyncio
    asyncio.run(provider.generate([{"role": "user", "content": "{}"}], schema={"title": "S"}))
    assert seen["body"]["thinking"] == {"type": "enabled"}
    assert seen["body"]["max_tokens"] == 12000
    assert seen["body"]["response_format"] == {"type": "json_object"}


def test_an_opted_out_role_sends_no_thinking_field(monkeypatch):
    monkeypatch.setattr(settings, "thinking_output_tokens", 12000)
    provider = ModelScopeModelProvider(base_url="https://api-inference.modelscope.cn/v1", api_key="k",
        model="m", timeout_seconds=1, max_output_tokens=3000, thinking_type=None)
    seen = capture_body(provider)
    import asyncio
    asyncio.run(provider.generate([{"role": "user", "content": "{}"}], schema={"title": "S"}))
    assert "thinking" not in seen["body"]
    assert seen["body"]["max_tokens"] == 3000


def test_interview_router_keeps_main_small_and_gives_evaluation_headroom(monkeypatch):
    modelscope(monkeypatch)
    monkeypatch.setattr(settings, "interview_model_max_output_tokens", 3000)
    monkeypatch.setattr(settings, "thinking_output_tokens", 12000)
    monkeypatch.setattr(settings, "interview_main_thinking", "disabled")
    monkeypatch.setattr(settings, "answer_evaluation_thinking", "enabled")
    router = build_interview_router()
    main = router.primary(INTERVIEW_MAIN_ROUTE)
    evaluation = router.primary(ANSWER_EVALUATION_ROUTE)
    assert (main.thinking_type, main.max_output_tokens) == ("disabled", 3000)
    assert (evaluation.thinking_type, evaluation.max_output_tokens) == ("enabled", 12000)


def test_truncated_output_still_fails_safely_with_usage(monkeypatch):
    """A reply that spends the whole budget on reasoning must never become a score."""
    from interview_api.infrastructure.models.provider import ModelProviderError
    provider = ModelScopeModelProvider(base_url="https://api-inference.modelscope.cn/v1", api_key="k",
        model="m", timeout_seconds=1, max_output_tokens=3000, thinking_type="enabled")
    seen = capture_body(provider, payload=json.dumps(dict(
        choices=[{"message": {"content": ""}, "finish_reason": "length"}],
        usage={"prompt_tokens": 3389, "completion_tokens": 3000, "total_tokens": 6389,
               "completion_tokens_details": {"reasoning_tokens": 3000}})).encode())
    import asyncio
    with pytest.raises(ModelProviderError) as error:
        asyncio.run(provider.generate([{"role": "user", "content": "{}"}], schema={"title": "S"}))
    assert error.value.code == "model_output_truncated"
    assert error.value.usage["total_tokens"] == 6389
    assert error.value.usage["reasoning_tokens"] == 3000
    assert seen["body"]["thinking"] == {"type": "enabled"}
