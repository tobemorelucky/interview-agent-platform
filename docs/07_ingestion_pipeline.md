# 07 多源面经采集与处理流水线

## 1. 目标

该流水线用于由管理员触发内容采集，并将多平台内容沉淀为正式可检索面经库。

来源平台分阶段支持：

1. MockSourceAdapter
2. NowcoderAdapter
3. XiaohongshuAdapter
4. DouyinAdapter

---

# 2. 核心边界

- 仅管理员触发采集任务；
- 普通用户搜索不触发采集；
- 采集结果先进入 Raw Pool；
- 处理结果进入 Candidate Pool；
- 审核通过后才进入 Published Library；
- 只有 Published Library 内容写入 Milvus。

---

# 3. 总体流程

```text
Admin creates crawl run
  -> Discovery
  -> Collection
  -> Raw storage
  -> Parsing
  -> OCR / ASR
  -> Fusion
  -> Structured extraction
  -> Scoring
  -> Deduplication
  -> Candidate pool
  -> Review
  -> Publish
  -> Milvus indexing
```

---

# 4. 阶段详解

## 4.1 Discovery

输入：

- 平台
- 关键词
- 数量限制
- 可选时间范围

输出：

- source item candidate list

统一结构示例：

```json
{
  "platform": "NOWCODER",
  "source_item_id": "abc123",
  "canonical_url": "...",
  "title": "...",
  "preview_text": "...",
  "discovered_at": "..."
}
```

---

## 4.2 Collection

目标：

- 获取详情页；
- 下载原始内容；
- 获取元数据；
- 获取媒体资源引用。

输出：

- raw payload
- raw metadata
- asset manifest

---

## 4.3 Raw Storage

存储：

- PostgreSQL：raw_contents / raw_assets
- MinIO：
  - html snapshots
  - json payloads
  - images
  - videos
  - audios

要求：

- 幂等；
- `(platform, source_item_id)` 唯一；
- 重复采集只更新必要元数据，不生成重复内容。

---

## 4.4 Parsing

目标：

- 从原始 HTML / JSON 中提取主要文本；
- 去掉导航、按钮、无关片段；
- 保存 parsed_text。

---

## 4.5 OCR / ASR

### 小红书

- 对图片做 OCR；
- 保存每张图片 OCR 结果；
- 后续参与融合。

### 抖音

- 下载视频；
- FFmpeg 提取音频；
- ASR 生成 transcript；
- 抽关键帧；
- OCR 画面文字；
- 保存中间结果。

---

## 4.6 Fusion

目标：

- 将 parsed_text、OCR 文本、ASR 文本合成为 `fused_text`；
- 保留来源信息；
- 不能丢失原始中间结果。

---

## 4.7 Structured Extraction

输入：

- fused_text
- 平台元数据

输出：

```json
{
  "is_interview_experience": true,
  "company": "字节跳动",
  "position": "大模型应用开发实习",
  "stage": "一面",
  "experience_summary": "...",
  "questions": [
    {
      "raw_question": "为什么 RAG 要加 rerank？",
      "canonical_question": "RAG 中 rerank 的作用是什么？",
      "answer_clue": "...",
      "question_type": "RAG",
      "confidence": 0.92
    }
  ],
  "marketing_signals": [],
  "extraction_notes": []
}
```

---

## 4.8 Scoring

评分分为：

- Rule Score
- AI Score
- Marketing Risk
- Freshness Score
- Final Score

Final Score 应由程序公式聚合，而非完全依赖 LLM。

示例：

```text
final_score =
  0.35 * rule_score
+ 0.30 * ai_specificity_score
+ 0.15 * freshness_score
+ 0.10 * structure_completeness_score
- 0.10 * marketing_risk_score
```

公式后续可调。

---

## 4.9 Deduplication

四层去重：

1. 平台 ID 去重；
2. URL / hash 去重；
3. 近重复文本去重；
4. 语义相似内容聚类。

第一版可以先完成：

- 平台 ID 去重；
- 文本 hash 去重；
- 语义聚类预留字段。

---

## 4.10 Candidate Pool

Candidate 存在状态：

```text
PENDING_REVIEW
APPROVED
REJECTED
PUBLISHED
```

候选池默认不对普通用户开放。

---

## 4.11 Review

管理员可：

- 发布；
- 拒绝；
- 备注；
- 重新评分；
- 重新解析；
- 查看原始、中间、结构化结果。

---

## 4.12 Publish

发布时：

- 创建 published_experiences；
- 创建 published_experience_questions；
- 写入 review_actions；
- 创建 vector_index_jobs；
- 异步索引。

---

## 4.13 Indexing

索引只处理发布内容。

写入：

- `experience_chunks_current`
- `experience_questions_current`

发布完成 ≠ 索引完成。  
需要独立 index_status。

---

# 5. Source Adapter 设计

## 5.1 统一接口

```python
class SourceAdapter(Protocol):
    async def discover(self, keyword: str, limit: int, **kwargs): ...
    async def fetch_detail(self, source_item_id: str, url: str, **kwargs): ...
    async def fetch_assets(self, raw_item, **kwargs): ...
    async def normalize_raw_item(self, raw_payload): ...
```

## 5.2 MockSourceAdapter

Phase 4 先实现 mock，用于验证：

- 任务状态；
- worker 调度；
- raw -> candidate -> review -> publish；
- 管理后台交互。

---

# 6. 平台差异

## 6.1 牛客

重点：

- 文本正文；
- 标题；
- 发布时间；
- 作者；
- 互动数据。

优先级最高，适合第一条真实链路。

## 6.2 小红书

重点：

- 正文；
- 图片；
- OCR；
- 标签；
- 营销判断。

## 6.3 抖音

重点：

- 视频；
- ASR；
- OCR；
- 标题简介；
- 视频中的问题与讲解。

---

# 7. 状态推进原则

每个 stage：

- 写 DB 状态；
- 记录成功/失败；
- 失败可重试；
- 不覆盖原始产物；
- 后续 stage 只能基于有效前置产物运行。

---

# 8. 幂等性

## 8.1 采集幂等

同一内容重复采集：

- 不重复插 raw；
- 可更新互动指标；
- 不重复创建候选，除非触发 reprocess。

## 8.2 索引幂等

同一 published entity：

- 不重复生成冲突向量；
- 允许按 embedding_version 重建。

---

# 9. 错误分类

- DISCOVERY_FAILED
- FETCH_FAILED
- RAW_STORE_FAILED
- PARSE_FAILED
- OCR_FAILED
- ASR_FAILED
- EXTRACTION_FAILED
- SCORING_FAILED
- DEDUP_FAILED
- INDEX_FAILED

---

# 10. 监控指标

- 发现内容数；
- 抓取成功率；
- OCR 成功率；
- ASR 成功率；
- 抽取有效率；
- 进入候选池数量；
- 审核发布率；
- 索引成功率；
- 平均处理耗时。

---

# 11. 第一版实施顺序

Phase 4：

- Task framework + Mock adapter

Phase 5：

- Nowcoder real adapter + text pipeline

Phase 6：

- Xiaohongshu + OCR

Phase 7：

- Douyin + video media pipeline

---

# 12. 验收标准

- 管理员任务能创建；
- Worker 能跑完整状态；
- Raw、Candidate、Published 清晰分层；
- Candidate 不被普通用户查到；
- Published 才能索引；
- 失败可追踪；
- 审核页可查看完整处理链路。
