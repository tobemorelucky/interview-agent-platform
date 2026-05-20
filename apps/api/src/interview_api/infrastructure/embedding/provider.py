from openai import AsyncOpenAI

from interview_api.core.config import settings

from . import EmbeddingProvider


class OpenAICompatibleEmbeddingProvider:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
        )
        self._model = settings.embedding_model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        return [d.embedding for d in response.data]

    async def embed_query(self, query: str) -> list[float]:
        results = await self.embed_texts([query])
        return results[0]
