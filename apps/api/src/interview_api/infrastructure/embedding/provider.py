import asyncio
from typing import Any

from volcenginesdkarkruntime import Ark

from interview_api.core.config import settings


class OpenAICompatibleEmbeddingProvider:
    """
    兼容旧项目接口的 Embedding Provider。

    注意：
    - 类名暂时保持 OpenAICompatibleEmbeddingProvider，避免影响现有 import。
    - 内部实际调用火山方舟 Ark 多模态向量接口。
    - 多模态 embedding 的 input 列表表示“一个样本的多模态组成部分”，不是 batch。
    - 因此 embed_texts(texts) 必须对每个 text 单独请求一次。
    """

    def __init__(self) -> None:
        if not settings.embedding_api_key:
            raise RuntimeError("EMBEDDING_API_KEY 未配置")

        if not settings.embedding_model:
            raise RuntimeError("EMBEDDING_MODEL 未配置")

        self._client = Ark(api_key=settings.embedding_api_key)
        self._model = settings.embedding_model

    def _extract_single_vector(self, resp: Any) -> list[float]:
        """
        从 Ark multimodal_embeddings 响应中解析单个向量。

        兼容：
        1. 新版 SDK: resp.data.embedding
        2. 旧版 SDK: resp.data[0].embedding
        3. dict: resp["data"]["embedding"] 或 resp["data"][0]["embedding"]
        """

        if hasattr(resp, "data"):
            data = resp.data
        elif isinstance(resp, dict) and "data" in resp:
            data = resp["data"]
        else:
            raise RuntimeError(
                f"Ark embedding 响应中没有 data 字段: type={type(resp)}, value={resp}"
            )

        # 新版：resp.data 直接是 MultimodalEmbedding 对象
        if hasattr(data, "embedding"):
            return list(data.embedding)

        # 旧版：resp.data 是 list
        if isinstance(data, list):
            if not data:
                raise RuntimeError("Ark embedding 响应 data 为空列表")

            first = data[0]

            if hasattr(first, "embedding"):
                return list(first.embedding)

            if isinstance(first, dict) and "embedding" in first:
                return list(first["embedding"])

            raise RuntimeError(
                f"无法从 data[0] 解析 embedding: type={type(first)}, value={first}"
            )

        # dict 形式
        if isinstance(data, dict):
            if "embedding" in data:
                return list(data["embedding"])

            if "data" in data and isinstance(data["data"], list) and data["data"]:
                first = data["data"][0]
                if isinstance(first, dict) and "embedding" in first:
                    return list(first["embedding"])

        raise RuntimeError(
            f"无法解析 Ark embedding 响应 data: type={type(data)}, value={data}"
        )

    def _embed_one_text_sync(self, text: str) -> list[float]:
        """
        对单条文本生成一个向量。

        关键点：
        multimodal_embeddings.create 的 input 列表表示一个样本的多个模态组成部分，
        所以这里每次只传一个 text item。
        """

        resp = self._client.multimodal_embeddings.create(
            model=self._model,
            input=[
                {
                    "type": "text",
                    "text": text,
                }
            ],
        )

        vector = self._extract_single_vector(resp)

        actual_dim = len(vector)
        expected_dim = settings.embedding_dim

        if actual_dim != expected_dim:
            raise RuntimeError(
                f"Embedding 维度不一致: actual={actual_dim}, expected={expected_dim}. "
                f"请检查 EMBEDDING_DIM 是否等于模型真实输出维度。"
            )

        return vector

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        对多条文本分别生成向量。

        注意：
        这里不是一次请求批量生成，而是逐条请求。
        这样才能保证 N 个 chunk 返回 N 个向量。
        """

        if not texts:
            return []

        vectors: list[list[float]] = []

        for text in texts:
            vector = await asyncio.to_thread(self._embed_one_text_sync, text)
            vectors.append(vector)

        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Embedding 返回数量与输入文本数量不一致: "
                f"texts={len(texts)}, vectors={len(vectors)}"
            )

        return vectors

    async def embed_query(self, query: str) -> list[float]:
        return await asyncio.to_thread(self._embed_one_text_sync, query)