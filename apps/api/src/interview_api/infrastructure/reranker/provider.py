from . import RerankProvider


class NoopRerankProvider:
    async def rerank(
        self, query: str, documents: list[dict], top_k: int | None = None
    ) -> list[dict]:
        """Return documents in original order, optionally truncated."""
        if top_k is not None:
            return documents[:top_k]
        return documents
