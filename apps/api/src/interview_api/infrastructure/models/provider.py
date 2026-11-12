"""Provider-neutral generation and deterministic offline replay."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
import re


@dataclass(frozen=True)
class ModelResponse:
    text: str
    usage: dict[str, int | None] = field(default_factory=dict)


class ModelProviderError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.retried = False
        self.usage: dict[str, int | None] = {}
        self.availability_reason: str | None = None
        self.invocations: list[dict] = []
        # None means "not classified here"; governed_call falls back to its own
        # flag. False is a positive claim that no bytes reached the provider.
        self.request_sent: bool | None = None


class ModelProvider(ABC):
    provider_name: str
    model_name: str

    @abstractmethod
    async def generate(self, messages: list[dict], *, schema: dict) -> ModelResponse: ...


def modelscope_connection() -> dict[str, str] | None:
    """Shared opt-in for structured routes and legacy SDK chat/streaming."""
    from interview_api.core.config import settings

    if not settings.use_modelscope:
        return None
    key = settings.modelscope_api_key.get_secret_value().strip()
    if not key:
        raise ModelProviderError("model_config_missing", "Set MODELSCOPE_API_KEY for LLM_PROVIDER=modelscope")
    model = settings.modelscope_model.strip()
    if not model:
        raise ModelProviderError("model_config_missing", "MODELSCOPE_MODEL must not be empty")
    return {
        "base_url": "https://api-inference.modelscope.cn/v1",
        "api_key": key,
        "model": model,
    }


class CompatibleModelProvider(ModelProvider):
    provider_name = "openai_compatible"

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 timeout_seconds: float, max_output_tokens: int,
                 client: httpx.AsyncClient | None = None, thinking_type: str | None = None,
                 governed: bool = False):
        if not base_url or not api_key or not model:
            raise ModelProviderError("model_config_missing", "Public research model configuration is incomplete")
        self.model_name = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.client = client
        self.governed = governed
        if thinking_type not in (None, "enabled", "disabled"):
            raise ValueError("Unsupported thinking policy")
        self.thinking_type = thinking_type

    async def generate(self, messages: list[dict], *, schema: dict) -> ModelResponse:
        if self.governed:
            from .governance import governed_call
            return await governed_call(self, messages, lambda: self._generate(messages, schema=schema))
        return await self._generate(messages, schema=schema)

    async def _generate(self, messages: list[dict], *, schema: dict) -> ModelResponse:
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        usage = {}
        try:
            body = {"model": self.model_name, "messages": messages,
                    "temperature": 0, "max_tokens": self.max_output_tokens,
                    "response_format": {"type": "json_object"}}
            if self.thinking_type is not None:
                body["thinking"] = {"type": self.thinking_type}
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                    timeout=self.timeout,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                # No connection was established, so nothing can have reached the
                # provider. This is a proven pre-send failure, not an unknown one.
                error = ModelProviderError("model_request_not_sent", "Model endpoint was not reachable")
                error.request_sent, error.usage = False, {"total_tokens": 0}
                error.availability_reason = "provider_connect_failed"
                raise error from exc
            except httpx.TimeoutException as exc:
                # A read/write timeout cannot be attributed to either side.
                raise ModelProviderError("model_timeout", "Public research model request timed out") from exc
            except httpx.HTTPError as exc:
                raise ModelProviderError("model_request_failed", "Public research model request failed") from exc
            except Exception as exc:
                # httpx runs request hooks before the transport writes, so an
                # exception raised by our own code here (payload assembly or a
                # client-side guard) is local: it must not be reported as a
                # provider network failure, nor charged as an unknown send.
                error = ModelProviderError("model_request_not_sent", "Model request failed before it was sent")
                error.request_sent, error.usage = False, {"total_tokens": 0}
                raise error from exc
            response.raise_for_status()
            data = response.json()
            raw_usage = data.get("usage") or {}
            raw_usage = raw_usage if isinstance(raw_usage, dict) else {}
            details = raw_usage.get('completion_tokens_details') or {}
            usage = {"input_tokens": raw_usage.get("prompt_tokens"),
                     "output_tokens": raw_usage.get("completion_tokens"),
                     "total_tokens": raw_usage.get("total_tokens"),
                     "reasoning_tokens": details.get('reasoning_tokens') if isinstance(details, dict) else None}
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                error = ModelProviderError("model_output_truncated", "Structured response was truncated")
                error.usage = usage
                raise error
            message = choice["message"]
            if message.get("refusal"):
                error = ModelProviderError("model_refusal", "Provider refused structured generation")
                error.usage = usage
                raise error
            return ModelResponse(message.get("content") or "", usage)
        except httpx.HTTPStatusError as exc:
            # The response arrived; only its billing remains unknown.
            error = ModelProviderError("model_request_failed", "Public research model request failed")
            error.request_sent = True
            try:
                envelope = exc.response.json().get("error") or {}
                code = str(envelope.get("code") or "")
                message = str(envelope.get("message") or "").lower()
            except (ValueError, AttributeError, TypeError):
                code, message = "", ""
            normalized = re.sub(r"[^a-z]", "", code.lower())
            known = any(name in normalized for name in (
                "modelnotfound", "endpointnotfound", "modelunavailable", "endpointunavailable",
                "serviceunavailable", "resourceunavailable", "ratelimit", "quotaexceeded", "serveroverloaded"))
            missing = exc.response.status_code in (400, 404) and ("model" in message or "endpoint" in message) and (
                "not found" in message or "not available" in message or "does not exist" in message)
            if known or missing or exc.response.status_code in (429, 502, 503, 504):
                error.availability_reason = "provider_availability_http_" + str(exc.response.status_code)
            raise error from exc
        except (KeyError, ValueError, IndexError, TypeError, AttributeError) as exc:
            # Reached only after a response: an unusable envelope is a provider
            # response problem, never a local pre-send failure.
            error = ModelProviderError("model_request_failed", "Public research model returned an unusable response")
            error.request_sent, error.usage = True, usage
            raise error from exc
        finally:
            if self.client is None:
                await client.aclose()


class ModelScopeModelProvider(CompatibleModelProvider):
    provider_name = "modelscope"


class ReplayModelProvider(ModelProvider):
    provider_name = "replay"
    model_name = "fixture"

    def __init__(self, responses: list[str | dict[str, Any]]):
        self.responses = list(responses)
        self.calls = 0

    async def generate(self, messages: list[dict], *, schema: dict) -> ModelResponse:
        import json
        self.calls += 1
        if not self.responses:
            raise ModelProviderError("replay_model_exhausted", "Replay model fixture has no next response")
        value = self.responses.pop(0)
        return ModelResponse(value if isinstance(value, str) else json.dumps(value), {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        })
