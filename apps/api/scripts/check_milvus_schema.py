from pymilvus import connections, Collection

from interview_api.core.config import settings


def main():
    connections.connect(
        alias="default",
        host=settings.milvus_host,
        port=str(settings.milvus_port),
    )

    collection = Collection("kb_chunks_v1")

    for field in collection.schema.fields:
        if field.name == "dense_vector":
            print("dense_vector params:", field.params)
            print("expected EMBEDDING_DIM:", settings.embedding_dim)
            return

    print("没有找到 dense_vector 字段")


if __name__ == "__main__":
    main()
