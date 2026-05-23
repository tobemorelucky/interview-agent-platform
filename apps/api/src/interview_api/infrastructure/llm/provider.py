from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from interview_api.core.config import settings

from . import LLMProvider


class OpenAICompatibleLLMProvider:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_request_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        self._model = settings.llm_model

    async def chat(self, messages: list[dict], **kwargs) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            **kwargs,
        )
        return response.choices[0].message.content or ""

    async def chat_stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            stream=True,
            **kwargs,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
