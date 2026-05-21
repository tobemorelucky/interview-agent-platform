from pymilvus import MilvusClient

from interview_api.core.config import settings


def safe_drop_collection(client, collection_name):
    """
    安全删除Milvus集合：自动先删除所有关联别名，再删除集合本身
    修复：正确处理 list_aliases() 返回的字典格式
    """
    if not client.has_collection(collection_name):
        print(f"collection 不存在: {collection_name}")
        return

    # ✅ 正确写法：从返回的字典中提取别名列表
    alias_result = client.list_aliases(collection_name)
    aliases = alias_result.get("aliases", [])

    if aliases:
        print(f"发现集合 {collection_name} 关联的别名: {aliases}")
        for alias in aliases:
            client.drop_alias(alias)
            print(f"✅ 已删除别名: {alias}")
    else:
        print(f"集合 {collection_name} 没有关联别名")

    # 现在可以安全删除集合了
    client.drop_collection(collection_name)
    print(f"✅ 已成功删除 collection: {collection_name}")


def main():
    client = MilvusClient(
        uri=f"http://{settings.milvus_host}:{settings.milvus_port}"
    )

    collection_name = "kb_chunks_v1"
    safe_drop_collection(client, collection_name)


if __name__ == "__main__":
    main()