# 06 Milvus 设计

## 1. 设计目标

Milvus 用于支撑：

1. 面试知识库检索；
2. 已发布面经检索；
3. 已发布面试题检索；
4. 后续的语义聚合、相似问题发现。

---

# 2. 核心原则

1. Milvus 不是真实业务数据源；
2. 业务事实以 PostgreSQL 为准；
3. 只有正式发布的内容进入面经检索 collection；
4. Raw 和 Candidate 内容不入 Milvus；
5. 使用 collection alias 为后续重建索引留空间；
6. 业务层通过 VectorStoreProvider 访问 Milvus，不直接散写 client 代码。

---

# 3. Collection 规划

## 3.1 `kb_chunks_current`

目标：

- 面试知识库 chunk 检索。

建议真实 collection：

```text
kb_chunks_v1
alias -> kb_chunks_current
```

字段建议：

| 字段 | 说明 |
|---|---|
| id | 主键 |
| doc_id | PostgreSQL 文档 ID |
| chunk_id | PostgreSQL chunk ID |
| category | 类别 |
| source_type | 来源 |
| title | 标题 |
| content | chunk 内容 |
| dense_vector | 向量 |
| created_at_ts | 时间戳 |

---

## 3.2 `experience_chunks_current`

目标：

- 已发布面经正文 / 摘要检索。

真实 collection：

```text
experience_chunks_v1
alias -> experience_chunks_current
```

字段建议：

| 字段 | 说明 |
|---|---|
| id | 主键 |
| published_experience_id | PostgreSQL ID |
| platform | 平台 |
| company | 公司 |
| position | 岗位 |
| stage | 轮次 |
| publish_time_ts | 原内容时间 |
| reliability_score | 可信度 |
| content | 可检索正文或摘要 |
| dense_vector | 向量 |

---

## 3.3 `experience_questions_current`

目标：

- 已发布面试题的语义检索。

真实 collection：

```text
experience_questions_v1
alias -> experience_questions_current
```

字段建议：

| 字段 | 说明 |
|---|---|
| id | 主键 |
| published_question_id | PostgreSQL ID |
| published_experience_id | 所属面经 |
| company | 公司 |
| position | 岗位 |
| stage | 轮次 |
| platform | 平台 |
| question_text | 归一化问题 |
| answer_clue | 回答线索 |
| reliability_score | 所属面经可信度 |
| dense_vector | 向量 |

---

# 4. 暂不进入 Milvus 的数据

以下内容不得直接入 Milvus：

- raw HTML / raw JSON
- 原始图片
- 原始视频
- OCR 原始文本
- ASR 原始文本
- Candidate 内容
- 未审核内容
- 低质量失败解析内容

这些可保存在 PostgreSQL / MinIO 中供后台查看。

---

# 5. 检索策略

## 5.1 知识库问答

```text
query
  -> embedding
  -> kb_chunks_current ANN search
  -> metadata optional filter
  -> rerank
  -> prompt assembly
```

## 5.2 正式面经检索

```text
query + filters
  -> embedding
  -> experience_chunks_current search
  -> experience_questions_current search
  -> merge results
  -> rerank / score combination
  -> PostgreSQL hydration
  -> optional LLM aggregation
```

---

# 6. 过滤字段

建议可过滤字段：

```text
platform
company
position
stage
publish_time_ts
reliability_score
```

这些字段用于：

- “只看抖音”
- “只查腾讯后端”
- “只看近 30 天”
- “只要 80 分以上”

---

# 7. 向量主键策略

建议主键可采用：

```text
entity_type + entity_id + embedding_version
```

或由业务层生成稳定 UUID。

目标：

- 避免重复写入；
- 支持同一业务实体重建 embedding；
- 支持索引切换。

---

# 8. Alias 与索引版本化

示例：

```text
experience_chunks_v1
experience_chunks_v2
alias: experience_chunks_current
```

当发生：

- embedding 模型切换；
- chunk 策略变化；
- 字段 schema 演进；

可：

1. 新建 v2；
2. 全量重建；
3. 校验；
4. alias 切换；
5. 保留 v1 作为回滚手段。

---

# 9. Indexer 工作流

## 9.1 发布后索引

```text
publish candidate
  -> create published records
  -> enqueue vector indexing job
  -> embed text
  -> insert Milvus
  -> update PostgreSQL index_status = INDEXED
```

## 9.2 删除 / 下架

下架时：

- PostgreSQL publish_status = UNPUBLISHED；
- 若保留索引，可查询时强制回表过滤；
- 更推荐触发 index cleanup task 删除或禁用相关向量；
- 第一版可实现“下架后用户查询回表时过滤”，后续再增强物理删除。

---

# 10. Dense / Hybrid 方案

第一版可先做：

- Dense vector retrieval
- PostgreSQL 条件筛选
- RerankProvider 预留

后续增强可评估：

- Milvus sparse / BM25 hybrid；
- 多路召回融合；
- 问题向量 + 正文向量联合召回。

---

# 11. Rerank 设计

Provider：

```text
RerankProvider
```

输入：

- query
- candidate documents

输出：

- rerank scores

用途：

- 知识库问答重排；
- 面经查询重排；
- 问题级检索重排。

---

# 12. 性能策略

- 批量 embedding；
- 批量 insert；
- 热门搜索缓存；
- 发布后异步建索引；
- 索引任务失败可重试；
- 搜索结果回表批量查询。

---

# 13. 初始化脚本要求

Phase 0 / Phase 2 应准备：

```text
scripts/init_milvus.py
```

能力：

- 检查连接；
- 创建 collection；
- 创建 alias；
- 打印状态；
- 不重复破坏已有 collection。

---

# 14. Milvus 验收清单

- collection 规划符合文档；
- Raw / Candidate 未入 Milvus；
- 搜索结果能回表；
- 发布后索引状态可追踪；
- alias 机制预留；
- provider 抽象存在；
- 初始化脚本可重复执行。
