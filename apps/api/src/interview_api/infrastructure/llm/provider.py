from collections.abc import AsyncIterator
from interview_api.infrastructure.models.governance import governed_call
from interview_api.infrastructure.models.provider import ModelResponse, modelscope_connection

from openai import AsyncOpenAI

from interview_api.core.config import settings

from . import LLMProvider


class OpenAICompatibleLLMProvider:
    def __init__(self, *, user_id: int | None = None, public_system_task: bool = False,
                 thinking_type: str | None = None, max_output_tokens: int = 6000) -> None:
        connection = modelscope_connection() or {
            "base_url": settings.llm_base_url, "api_key": settings.llm_api_key, "model": settings.llm_model,
        }
        self._client = AsyncOpenAI(
            base_url=connection["base_url"],
            api_key=connection["api_key"],
            timeout=settings.llm_request_timeout_seconds,
            max_retries=0,  # Each HTTP retry must receive its own durable admission.
        )
        self._model = connection["model"]
        self.user_id = user_id
        self.public_system_task = public_system_task
        self.model_name = self._model
        self.timeout = settings.llm_request_timeout_seconds
        self.max_output_tokens = max_output_tokens
        # Extraction is not a reasoning task: an unset policy lets the model spend
        # the whole output budget on reasoning and cut the JSON mid-string.
        self.thinking_type = thinking_type

    async def chat(self, messages: list[dict], **kwargs) -> str:
        from interview_api.core.errors import AppError
        if not 1 <= kwargs.get("max_tokens", self.max_output_tokens) <= self.max_output_tokens:
            raise AppError("MODEL_OUTPUT_BUDGET", "输出预算超过当前请求允许范围", 422)
        policy = {"thinking": {"type": self.thinking_type}} if self.thinking_type else {}

        async def operation():
            response = await self._client.chat.completions.create(
                model=self._model, messages=messages,
                **{"max_tokens": self.max_output_tokens, **kwargs}, extra_body=policy or None)
            choice = response.choices[0] if response.choices else None
            if choice is not None and choice.finish_reason == "length":
                # The body is empty/partial because the budget ran out. Reporting
                # this as a JSON error hides the real cause from the user.
                error = AppError("MODEL_OUTPUT_TRUNCATED", "模型输出被截断，未生成完整结果；请重试", 503)
                error.request_sent = True
                raise error
            usage = response.usage
            text = (choice.message.content if choice else None) or ""
            return ModelResponse(text, {"total_tokens": usage.total_tokens if usage else None})
        from interview_api.infrastructure.models.scope import model_scope_for
        from interview_api.infrastructure.db.session import async_session_factory
        from interview_api.infrastructure.models.governance import request_user_id
        uid = self.user_id if self.user_id is not None else request_user_id.get()
        if self.user_id is not None:
            # Resume extraction is a user-scoped feature; keep its own route.
            scope = model_scope_for(async_session_factory, user_id=self.user_id, route="resume_report")
        elif self.public_system_task:
            scope = model_scope_for(async_session_factory, owner="system:public-career",
                                    route="legacy_public_extraction")
        elif uid is not None:
            # Legacy chat inside an authenticated request: the owner is explicit
            # here, not inferred later by the gateway.
            scope = model_scope_for(async_session_factory, user_id=uid, route="legacy_chat")
        else:
            raise AppError("MODEL_IDENTITY_REQUIRED", "模型调用缺少用户身份，未发送请求", 403)
        with scope:
            return (await governed_call(self, messages, operation)).text

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
