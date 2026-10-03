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

---

# 12. Phase 4：面经采集任务状态机

## 12.1 Collection Task 状态（experience_collection_tasks）

| 状态 | 说明 |
|------|------|
| `PENDING` | 初始，等待 Worker 执行 |
| `SEARCHING` | SearXNG 搜索中 |
| `SEARCH_COMPLETED` | 搜索发现完成，source items 已写入，尚未抓取正文 |
| `FETCHING` | 抓取搜索结果 URL 正文 |
| `FETCH_COMPLETED` | URL 正文抓取阶段完成，source items 已更新为 FETCHED / FETCH_FAILED |
| `EXTRACTING` | Extraction Agent 处理 |
| `ROUTING` | Routing Agent 分类 |
| `SCORING` | Reliability Agent 评分 |
| `DEDUPING` | 与已有面经去重 |
| `WAITING_REVIEW` | 等待管理员审核 |
| `APPROVED` | 审核通过 |
| `INDEXING` | 写入 Milvus（如配置开启） |
| `COMPLETED` | 完成 |
| `FAILED` | 失败（可重试） |

## 12.2 Source Item 状态（experience_source_items）

| 状态 | 说明 |
|------|------|
| `DISCOVERED` | SearXNG 搜索结果，URL 已发现 |
| `FETCHED` | 网页内容已抓取 |
| `FETCH_FAILED` | 抓取失败 |
| `EXTRACTED` | Extraction Agent 已处理 |
| `NOT_EXPERIENCE` | 判定为非面经内容 |
| `WAITING_REVIEW` | 等待管理员审核 |
| `APPROVED` | 审核通过 |
| `REJECTED` | 审核拒绝 |
| `INDEXED` | 已写入向量库 |

---

# 13. M2A PublicDataRefreshTask 状态机

```text
PENDING -> SEARCHING -> FETCHING -> READY_FOR_EXTRACTION
                                      -> COMPLETED   (M2B/后续阶段)
任一执行阶段 -> FAILED
FAILED -> SEARCHING                    (显式兼容重试)
READY_FOR_EXTRACTION -> SEARCHING/FETCHING（显式补采）
```

M2A 正常执行终点是 `READY_FOR_EXTRACTION`，不会调用模型或自行进入抽取。
`ExperienceCollectionTask` 是旧管理入口，通过唯一外键绑定该状态边界；其
`SEARCH_*`/`FETCH_*` 状态仅作为旧 UI 投影，Search/Fetch 的真实调用与审计以
`PublicDataRefreshTask` 和 `ToolInvocation` 为准。

## 13.1 M2B 抽取状态

```text
READY_FOR_EXTRACTION -> EXTRACTING -> VALIDATING -> PUBLISHING -> COMPLETED
任一抽取执行阶段 -> FAILED
COMPLETED / FAILED -> EXTRACTING（仅显式 force 的 INTERVIEW_DATA 重跑）
```

`PublicDataResearchAgent` 是唯一对外公共研究角色，只处理 `JOB_JD` 和
`INTERVIEW_DATA`；旧 `EXPERIENCE_COLLECTION_TASK` target 是面经兼容别名。
任务成功完成不代表内容已发布：社区面经继续等待原管理审核；JD 的
`publication_status` 独立记录 `APPROVED` / `WAITING_REVIEW`。
面经显式重跑只替换该任务的未批准草稿，已批准数据禁止覆盖。
公共实体/证据与任务 COMPLETED 在同一事务提交；失败不会留下半套公共数据。

# 14. M4 Interview SessionController

Session：CREATED -> READY -> IN_PROGRESS -> COMPLETED。
Question：PENDING -> ASKED -> ANSWERED -> EVALUATED -> COMPLETED，或 SKIPPED。
Answer：RUNNING -> COMPLETED，失败为 FAILED，同幂等键可重试。
两个 Agent 只返回 artifact/recommendation；Controller 独占状态推进和 index 同步。
FOLLOW_UP 是同主题补问，DEEP_DIVE 是新 child question；二者共用根题追问上限，
并受题量/轮次/时间/Token 预算控制。陈旧 state_version 结果返回 409，不覆盖。
模型期间无行锁/长事务；全部结果 SSE 在短事务提交后才发送。COMPLETE 由实际
Evaluation 的 DB 聚合产生，结束时无 ASKED/PENDING 残留，不写长期 Memory/Profile。
详见 [M4 状态、幂等与预算](22_mock_interview_core_m4.md)。

# 15. M5 独立报告与提案

Session 保持 COMPLETED。Report generation：RUNNING -> COMPLETED / FAILED；FAILED
同 key 重试增加 attempt，主动 regenerate 新建 generation。模型不持 DB 长事务。
发布完整 Report/Observation/Proposal 后同事务最后切换 current，失败不替换旧 current。
Proposal：PENDING -> APPROVED / CORRECTED / REJECTED；最终决定不可覆盖。approve/
correct 与 UserMemoryItem/Event 一起 commit，reject 不写 Memory；没有自动 commit。
下一场创建时分别固定 approved Memory 与近期 PerformanceObservation，不每轮刷新。
详见 [M5 状态、事务与验收](23_interview_coach_m5.md)。

# 16. M6 Frozen MatchRun

READY/FAILED -> RUNNING -> COMPLETED/FAILED。固定输入不能更新；成功 Run 封存，重试只读。
短事务领取 attempt/version，事务外模型调用，短事务校验 token 后一次写完整推荐和三层
排序，最后切换 COMPLETED。失败不留下部分推荐；attempt/Agent audit 保留。RUNNING
重入 409，过期 lease 允许重试，旧 attempt fencing 防止覆盖。不修改 Interview/Report/Profile/
Memory 状态；无候选不调用模型或 Research。详见 [M6 状态与验收](24_job_matching_m6.md)。
