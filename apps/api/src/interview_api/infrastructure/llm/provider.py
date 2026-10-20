from collections.abc import AsyncIterator
from interview_api.infrastructure.models.governance import governed_call, model_scope
from interview_api.infrastructure.models.provider import ModelResponse

from openai import AsyncOpenAI

from interview_api.core.config import settings

from . import LLMProvider


class OpenAICompatibleLLMProvider:
    def __init__(self, *, user_id: int | None = None, public_system_task: bool = False) -> None:
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_request_timeout_seconds,
            max_retries=0,  # Each HTTP retry must receive its own durable admission.
        )
        self._model = settings.llm_model
        self.user_id = user_id
        self.public_system_task = public_system_task
        self.model_name = self._model
        self.timeout = settings.llm_request_timeout_seconds
        self.max_output_tokens = 6000

    async def chat(self, messages: list[dict], **kwargs) -> str:
        from interview_api.core.errors import AppError
        if not 1 <= kwargs.get("max_tokens", self.max_output_tokens) <= self.max_output_tokens:
            raise AppError("MODEL_OUTPUT_BUDGET", "输出预算超过当前请求允许范围", 422)
        async def operation():
            response = await self._client.chat.completions.create(
                model=self._model, messages=messages,
                **{"max_tokens": self.max_output_tokens, **kwargs})
            usage = response.usage
            return ModelResponse(response.choices[0].message.content or "", {
                "total_tokens": usage.total_tokens if usage else None})
        token = None
        if self.user_id is not None:
            from interview_api.infrastructure.db.session import async_session_factory
            token = model_scope.set((async_session_factory, f"user:{self.user_id}", self.user_id, "resume_report"))
        elif self.public_system_task:
            from interview_api.infrastructure.db.session import async_session_factory
            token = model_scope.set((async_session_factory, "system:public-career", None, "legacy_public_extraction"))
        try:
            return (await governed_call(self, messages, operation)).text
        finally:
            if token is not None:
                model_scope.reset(token)

    async def chat_stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        # Reserve before opening the stream; hold the reservation through disconnect.
        from interview_api.infrastructure.models.governance import ModelGovernance, request_user_id
        from interview_api.infrastructure.db.session import async_session_factory
        from interview_api.core.errors import AppError
        import asyncio
        import json
        if not 1 <= kwargs.get("max_tokens", self.max_output_tokens) <= self.max_output_tokens:
            raise AppError("MODEL_OUTPUT_BUDGET", "输出预算超过当前请求允许范围", 422)
        uid = self.user_id or request_user_id.get()
        if uid is None:
            raise AppError("MODEL_IDENTITY_REQUIRED", "模型调用缺少用户身份", 403)
        governance = ModelGovernance(async_session_factory)
        key = await governance.reserve(f"user:{uid}", user_id=uid, route="legacy_stream", model=self._model,
            tokens=len(json.dumps(messages, ensure_ascii=False).encode()) + self.max_output_tokens, timeout=self.timeout)
        usage = None
        stream = None
        try:
            async with asyncio.timeout(self.timeout):
                stream = await self._client.chat.completions.create(model=self._model, messages=messages,
                    stream=True, stream_options={"include_usage": True},
                    **{"max_tokens": self.max_output_tokens, **kwargs})
                async for chunk in stream:
                    if chunk.usage:
                        usage = {"total_tokens": chunk.usage.total_tokens}
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
        finally:
            if stream is not None:
                await stream.close()
            await asyncio.shield(governance.settle(key, usage))
