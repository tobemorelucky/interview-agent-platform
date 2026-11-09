"""Offline tests at the configured router and model HTTP boundary."""
import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from interview_api.core.config import Settings, settings
from interview_api.infrastructure.models import router as routes
from interview_api.infrastructure.models.provider import ModelProviderError
from interview_api.infrastructure.models.structured import generate_structured

BUILDERS = (
    routes.build_model_router,
    routes.build_resume_profile_router,
    routes.build_interview_router,
    routes.build_interview_coach_router,
    routes.build_job_matching_router,
)
URL = "https://api-inference.modelscope.cn/v1"
MODEL = "deepseek-ai/DeepSeek-V4.1-Flash-1017"
QWEN = "Qwen/Qwen3.5-35B-A3B"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "modelscope")
    monkeypatch.setattr(settings, "modelscope_api_key", SecretStr("modelscope-fixture"))
    monkeypatch.delenv("MODELSCOPE_MODEL", raising=False)
    monkeypatch.setattr(settings, "modelscope_model", Settings(_env_file=None).modelscope_model)
    for prefix in ("llm", "public_research", "resume_profile", "interview_model",
                   "interview_coach", "job_matching"):
        monkeypatch.setattr(settings, prefix + "_base_url", "https://old.invalid/v1")
        monkeypatch.setattr(settings, prefix + "_api_key", "old-fixture")
    for route in ("llm", "public_research_extract", "public_research_quality",
                  "resume_profile", "interview_main", "answer_evaluation", "interview_coach", "job_matching"):
        monkeypatch.setattr(settings, route + "_model", "old-" + route)
    monkeypatch.setattr(settings, "llm_fallback_model", "old-fallback")


def test_modelscope_overrides_every_configured_route(configured):
    built = [build() for build in BUILDERS]
    assert {key for router in built for key in router.routes} == {
        routes.EXTRACT_ROUTE, routes.QUALITY_ROUTE, routes.RESUME_PROFILE_ROUTE,
        routes.INTERVIEW_MAIN_ROUTE, routes.ANSWER_EVALUATION_ROUTE,
        routes.INTERVIEW_COACH_ROUTE, routes.JOB_MATCHING_ROUTE,
    }
    for router in built:
        assert not router.fallback_routes
        for route in router.routes:
            provider = router.primary(route)
            assert (provider.provider_name, provider.base_url, provider.model_name) == ("modelscope", URL, MODEL)


@pytest.mark.parametrize("http_status", [200, 401, 429, 503])
def test_modelscope_structured_wire_and_no_availability_fallback(configured, http_status):
    class Reply(BaseModel):
        ok: bool

    seen = []
    def respond(request):
        seen.append(request)
        if http_status != 200:
            return httpx.Response(http_status, json={"error": {"code": "quota_exceeded"}})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 4, "total_tokens": 19},
        })

    async def run():
        router = routes.build_interview_router()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for provider in (*router.routes.values(), *router.fallback_routes.values()):
                provider.client = client
                provider.governed = False  # Provider boundary only; no quota database.
            async def call():
                return await generate_structured(router, routes.ANSWER_EVALUATION_ROUTE,
                    system="Return a JSON object.", payload={"input": "fixture"}, response_model=Reply)
            if http_status == 200:
                result = await call()
                assert result.parsed.ok and result.usage["total_tokens"] == 19
            else:
                with pytest.raises(ModelProviderError):
                    await call()
    asyncio.run(run())
    assert len(seen) == 1
    assert str(seen[0].url) == URL + "/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer modelscope-fixture"
    body = json.loads(seen[0].content)
    assert body["model"] == MODEL
    assert body["response_format"] == {"type": "json_object"}
    assert "thinking" not in body


def test_modelscope_missing_key_does_not_use_old_credentials(configured, monkeypatch):
    from interview_api.infrastructure.llm.provider import OpenAICompatibleLLMProvider
    monkeypatch.setattr(settings, "modelscope_api_key", SecretStr(""))
    for build in (*BUILDERS, OpenAICompatibleLLMProvider):
        with pytest.raises(ModelProviderError, match="MODELSCOPE_API_KEY"):
            build()


def test_legacy_sdk_uses_same_switch_and_original_model_can_be_restored(configured, monkeypatch):
    from interview_api.infrastructure.llm.provider import OpenAICompatibleLLMProvider
    assert OpenAICompatibleLLMProvider().model_name == MODEL
    monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
    assert OpenAICompatibleLLMProvider().model_name == "old-llm"
    router = routes.build_interview_router()
    assert router.primary(routes.INTERVIEW_MAIN_ROUTE).model_name == "old-interview_main"
    assert router.fallback_routes[routes.INTERVIEW_MAIN_ROUTE].model_name == "old-fallback"


def test_modelscope_qwen_manual_selection_is_shared_and_has_no_fallback(configured, monkeypatch):
    from interview_api.infrastructure.llm.provider import OpenAICompatibleLLMProvider
    monkeypatch.setenv("MODELSCOPE_MODEL", QWEN)
    configured_model = Settings(_env_file=None).modelscope_model
    monkeypatch.setattr(settings, "modelscope_model", configured_model)
    assert OpenAICompatibleLLMProvider().model_name == QWEN
    for router in (build() for build in BUILDERS):
        assert not router.fallback_routes
        assert all(router.primary(route).model_name == QWEN for route in router.routes)
