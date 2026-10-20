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


class ModelProvider(ABC):
    provider_name: str
    model_name: str

    @abstractmethod
    async def generate(self, messages: list[dict], *, schema: dict) -> ModelResponse: ...


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
        try:
            body = {"model": self.model_name, "messages": messages,
                    "temperature": 0, "max_tokens": self.max_output_tokens,
                    "response_format": {"type": "json_object"}}
            if self.thinking_type is not None:
                body["thinking"] = {"type": self.thinking_type}
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            raw_usage = data.get("usage") or {}
            usage = {"input_tokens": raw_usage.get("prompt_tokens"),
                     "output_tokens": raw_usage.get("completion_tokens"),
                     "total_tokens": raw_usage.get("total_tokens"),
                     "reasoning_tokens": (raw_usage.get("completion_tokens_details") or {}).get("reasoning_tokens")}
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
        except httpx.TimeoutException as exc:
            raise ModelProviderError("model_timeout", "Public research model request timed out") from exc
        except httpx.HTTPStatusError as exc:
            error = ModelProviderError("model_request_failed", "Public research model request failed")
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
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            raise ModelProviderError("model_request_failed", "Public research model request failed") from exc
        finally:
            if self.client is None:
                await client.aclose()


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
