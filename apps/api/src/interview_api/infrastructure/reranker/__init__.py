from typing import Protocol


class RerankProvider(Protocol):
    async def rerank(
        self, query: str, documents: list[dict], top_k: int | None = None
    ) -> list[dict]: ...
