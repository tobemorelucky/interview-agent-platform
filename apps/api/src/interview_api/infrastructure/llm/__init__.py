from collections.abc import AsyncIterator
from typing import Protocol


class LLMProvider(Protocol):
    async def chat(self, messages: list[dict], **kwargs) -> str: ...

    async def chat_stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]: ...
