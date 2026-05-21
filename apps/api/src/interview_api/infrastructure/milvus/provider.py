import time
from typing import Any

from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    MilvusClient,
    connections,
)

from interview_api.core.config import settings


class MilvusVectorStoreProvider:
    def __init__(self, embedding_dim: int = 768) -> None:
        self._embedding_dim = embedding_dim
        self._alias = "default"
        connections.connect(
            alias=self._alias,
            host=settings.milvus_host,
            port=str(settings.milvus_port),
        )
        self._client = MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}")

    def ensure_collection(self, collection_name: str, description: str = "") -> Collection:
        if self._client.has_collection(collection_name):
            return Collection(collection_name, using=self._alias)

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False),
            FieldSchema(name="doc_id", dtype=DataType.INT64),
            FieldSchema(name="chunk_id", dtype=DataType.INT64),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=500),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="source_type", dtype=DataType.VARCHAR, max_length=50),
            FieldSchema(name="dense_vector", dtype=DataType.FLOAT_VECTOR, dim=self._embedding_dim),
            FieldSchema(name="created_at_ts", dtype=DataType.INT64),
        ]

        schema = CollectionSchema(fields=fields, description=description)
        collection = Collection(collection_name, schema=schema, using=self._alias)

        index_params = {
            "index_type": "AUTOINDEX",
            "metric_type": "COSINE",
            "params": {},
        }
        collection.create_index(
            field_name="dense_vector",
            index_params=index_params,
        )
        collection.load()
        return collection

    def ensure_alias(self, collection_name: str, alias: str) -> None:
        if self._client.has_collection(collection_name):
            try:
                self._client.create_alias(collection_name=collection_name, alias=alias)
            except Exception:
                pass

    def insert(self, collection_name: str, entities: list[dict]) -> None:
        if not entities:
            return
        self._client.insert(collection_name=collection_name, data=entities)
        self._client.flush(collection_name=collection_name)

    def search(
        self,
        collection_name: str,
        vector: list[float],
        top_k: int = 5,
        filter_expr: str | None = None,
        output_fields: list[str] | None = None,
    ) -> list[dict]:
        if output_fields is None:
            output_fields = ["id", "doc_id", "chunk_id", "title", "content", "source_type"]

        results = self._client.search(
            collection_name=collection_name,
            data=[vector],
            limit=top_k,
            filter=filter_expr,
            output_fields=output_fields,
        )
        if not results:
            return []
        return [
            {**dict(r["entity"]), "score": r.get("distance")}
            for r in results[0]
        ]

    def get_by_ids(self, collection_name: str, ids: list[int]) -> list[dict]:
        results = self._client.get(
            collection_name=collection_name,
            ids=ids,
        )
        return results or []
