# 12 任务状态机设计

## 1. 目标

定义系统中的关键状态流，保证：

- 任务可追踪；
- 失败可定位；
- 后续可以重试；
- 发布与索引状态清晰；
- 前端可解释展示。

---

# 2. Crawl Run 状态

```text
CREATED
RUNNING
PARTIAL_SUCCESS
SUCCESS
FAILED
CANCELLED  # 后续可选
```

## 状态说明

| 状态 | 说明 |
|---|---|
| CREATED | 已创建但未开始 |
| RUNNING | 至少一个 task 正在执行 |
| PARTIAL_SUCCESS | 部分成功、部分失败 |
| SUCCESS | 所有预期任务完成 |
| FAILED | 全部失败或主流程不可继续 |

---

# 3. Crawl Task 状态

```text
PENDING
DISCOVERING
DISCOVERED
FETCHING
FETCHED
PARSING
PARSED
MEDIA_PROCESSING
MEDIA_PROCESSED
EXTRACTING
EXTRACTED
SCORING
SCORED
DEDUPING
DEDUPED
CANDIDATE_CREATED
FAILED
```

平台不同，可跳过部分阶段：

- 牛客可跳过 MEDIA_PROCESSING；
- 小红书可能 OCR；
- 抖音需要 ASR + OCR。

---

# 4. Resume Analysis 状态

```text
PENDING
PARSING
PARSED
ANALYZING
SUCCESS
FAILED
```

---

# 5. Candidate Review 状态

## 5.1 review_status

```text
PENDING
APPROVED
REJECTED
```

## 5.2 publish_status

```text
NOT_PUBLISHED
PUBLISHING
PUBLISHED
PUBLISH_FAILED
UNPUBLISHED
```

说明：

- APPROVED 不等于 PUBLISHED；
- 审核通过后可由人工发布；
- 未来自动发布规则也走同一状态机。

---

# 6. Published Index 状态

```text
NOT_INDEXED
INDEXING
INDEXED
FAILED
```

说明：

- Published 内容已存在于 PostgreSQL；
- INDEXED 表示 Milvus 向量写入完成；
- 用户检索可以选择只返回 INDEXED 内容；
- 第一版建议只查 INDEXED 内容，避免不一致。

---

# 7. Task Retry 规则

## 可重试阶段

- FETCH_FAILED
- OCR_FAILED
- ASR_FAILED
- EXTRACTION_FAILED
- SCORING_FAILED
- INDEX_FAILED

## 谨慎重试阶段

- DEDUP_FAILED
- PUBLISH_FAILED

## 不建议自动无限重试

- 解析内容明显无效；
- 原始资源不存在；
- Provider 配置错误。

---

# 8. 状态推进规则

1. 只能前进或由显式 retry 回到指定阶段；
2. 不允许跳过关键产物直接进入后续阶段；
3. 每次状态改变写入更新时间；
4. 失败必须带 error code 和 message；
5. 前端展示使用状态机字段，不依赖猜测。

---

# 9. 推荐状态迁移示例

## 9.1 牛客文本内容

```text
PENDING
 -> DISCOVERING
 -> DISCOVERED
 -> FETCHING
 -> FETCHED
 -> PARSING
 -> PARSED
 -> EXTRACTING
 -> EXTRACTED
 -> SCORING
 -> SCORED
 -> DEDUPING
 -> DEDUPED
 -> CANDIDATE_CREATED
```

## 9.2 抖音视频内容

```text
PENDING
 -> DISCOVERING
 -> DISCOVERED
 -> FETCHING
 -> FETCHED
 -> PARSING
 -> PARSED
 -> MEDIA_PROCESSING
 -> MEDIA_PROCESSED
 -> EXTRACTING
 -> EXTRACTED
 -> SCORING
 -> SCORED
 -> DEDUPING
 -> DEDUPED
 -> CANDIDATE_CREATED
```

---

# 10. 前端展示建议

## 管理员任务详情

展示阶段进度：

```text
发现 -> 抓取 -> 解析 -> 媒体处理 -> 抽取 -> 评分 -> 候选生成
```

## 候选详情

展示：

- 当前审核状态
- 当前发布状态
- 索引状态

---

# 11. 状态机验收

- 数据表中状态字段齐全；
- Worker 正确推进；
- API 能查状态；
- 失败能定位；
- 重试不会乱写；
- 前端不自行推测任务阶段。
