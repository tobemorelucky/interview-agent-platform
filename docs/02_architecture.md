# 02 技术架构设计

## 1. 架构目标

系统需要同时满足：

1. 用户侧响应稳定；
2. 管理员侧采集任务可控；
3. 多源内容解析具备扩展性；
4. 向量检索具备长期演进能力；
5. 代码结构适合 Claude Code 分阶段实现；
6. 第一版不过度微服务化。

---

# 2. 总体架构

```text
┌──────────────────────────────────────────────────────────────┐
│                         Web Frontend                         │
│ Vue3 + TypeScript + Vite                                     │
│ Login / QA / Resume / Experience / Admin                     │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTP / SSE
┌──────────────────────────────▼───────────────────────────────┐
│                         FastAPI API                           │
│ Auth | RAG QA | Resume | Experience Query | Admin Review     │
└──────────────┬───────────────────┬───────────────────────────┘
               │                   │
               │                   │ dispatch tasks
               ▼                   ▼
      ┌────────────────┐   ┌────────────────────┐
      │ PostgreSQL     │   │ Celery Worker       │
      │ business data  │   │ async pipelines     │
      └────────────────┘   └──────────┬─────────┘
               │                      │
               │                      ├── Source adapters
               │                      ├── OCR / ASR
               │                      ├── LLM extraction
               │                      ├── Scoring
               │                      └── Indexing
               │
        ┌──────┼───────────────────────────────┐
        ▼      ▼                               ▼
     Redis   Milvus                         MinIO
```

---

# 3. 核心架构风格

## 3.1 模块化单体 API

API 服务统一部署，内部按业务模块拆分：

```text
auth
users
rag_qa
resume_analysis
experience_query
admin_collection
admin_review
```

优点：

- 个人项目维护成本低；
- 依然能保持高解耦；
- 数据一致性简单；
- 后续如确有必要可拆服务。

## 3.2 独立 Worker 服务

Worker 负责重任务：

- 内容采集
- 媒体下载
- OCR
- ASR
- LLM 抽取
- 评分
- 去重
- 索引

API 不直接做重任务，避免阻塞请求。

---

# 4. 在线链路与离线链路

## 4.1 在线链路

### A. 面试知识问答

```text
User Query
  -> API validates auth
  -> Query understanding
  -> Milvus retrieval
  -> Optional rerank
  -> Prompt assembly
  -> LLM streaming answer
  -> Save chat history
  -> Return answer + citations
```

### B. 简历分析

```text
User uploads resume
  -> API stores file
  -> API creates analysis record
  -> Worker parses resume / runs LLM workflow
  -> User polls or receives task state
  -> Report becomes available
```

### C. 正式面经查询

```text
User query
  -> Parse filters
  -> Search published experience collections
  -> Aggregate matched questions / experiences
  -> LLM summarizes if needed
  -> Return only published content
```

---

## 4.2 离线链路

```text
Admin creates crawl task
  -> Task saved in DB
  -> Celery task dispatched
  -> Discovery worker finds items
  -> Collector downloads raw content
  -> Parser builds clean text
  -> OCR/ASR workers handle media
  -> Fusion worker normalizes content
  -> Extractor creates structured candidate
  -> Scorer calculates reliability
  -> Dedup worker groups duplicates
  -> Candidate saved
  -> Admin reviews
  -> Publish service creates formal content
  -> Indexer writes published vectors to Milvus
```

---

# 5. 服务职责

## 5.1 Web Frontend

职责：

- 登录注册
- 问答交互
- 简历上传与报告展示
- 面经检索页面
- 管理员后台

不负责：

- 业务权限判断
- 任务状态真实来源
- 内容评分逻辑

---

## 5.2 API Service

职责：

- 请求鉴权
- 输入校验
- 业务编排
- 查询 DB/Milvus
- 创建异步任务
- 返回标准响应

不负责：

- 直接执行长耗时媒体处理
- 直接把 raw 候选内容写入向量库

---

## 5.3 Worker Service

职责：

- 离线任务编排
- 状态推进
- 重试
- 中间产物保存
- 输出候选数据

---

# 6. 分层设计

## 6.1 后端分层

```text
api/router
application/service
domain/model
domain/policy
infrastructure/repository
infrastructure/provider
```

### API 层

- FastAPI routes
- Pydantic request/response schema
- Auth dependencies

### Application 层

- 用例编排
- 事务边界
- 调用仓储与 Provider

### Domain 层

- 领域实体
- 状态机规则
- 发布规则
- 评分聚合规则

### Infrastructure 层

- PostgreSQL repository
- Milvus client
- Redis client
- MinIO client
- LLM/ASR/OCR provider
- Source adapters

---

# 7. 关键解耦点

## 7.1 Provider 解耦

必须通过接口隔离：

```text
LLMProvider
EmbeddingProvider
RerankProvider
OCRProvider
ASRProvider
ObjectStorageProvider
VectorStoreProvider
```

原因：

- 后续可能换模型或服务商；
- 测试时可用 fake provider；
- Claude Code 不应把某个 SDK 写死在业务层。

---

## 7.2 Source Adapter 解耦

平台采集统一接口：

```text
discover()
fetch_detail()
fetch_assets()
normalize_raw_item()
```

业务层不关心具体平台页面结构。

---

## 7.3 发布前后数据解耦

```text
Raw
Candidate
Published
```

- Raw 只存事实采集结果；
- Candidate 是 AI/规则处理结果；
- Published 才是用户可查知识。

---

# 8. 数据职责划分

## PostgreSQL

- 用户
- 角色
- 会话
- 简历元数据
- 采集任务
- 状态流
- 候选内容
- 正式发布内容
- 评分
- 审核记录

## Milvus

- 已发布知识库 chunk 向量
- 已发布面经 chunk 向量
- 已发布面试题向量

## Redis

- 缓存
- 任务辅助
- 限流预留
- 临时状态

## MinIO

- 原始简历文件
- 原始 HTML/JSON 快照
- 图片
- 视频
- 音频
- OCR/ASR 中间文件

---

# 9. 典型顺序图

## 9.1 管理员发布一条候选面经

```text
Admin UI
  -> API: publish candidate
  -> Application service:
       validate admin
       validate candidate state
       create published experience
       create published questions
       persist publish audit log
       enqueue indexing task
  -> Worker:
       embed published content
       write to Milvus
       update index status
  -> API returns accepted
```

## 9.2 用户查询正式面经

```text
User UI
  -> API: query published experiences
  -> API:
       parse filters
       search Milvus or DB
       fetch formal records
       aggregate result
       optionally summarize
  -> UI displays results
```

---

# 10. 设计约束

1. 任何“是否发布”的逻辑以 PostgreSQL 状态为准，不以 Milvus 是否存在为准。
2. Milvus 是检索索引，不是业务事实来源。
3. 面经原始内容与正式展示内容要能回溯关联。
4. 评分需要可解释，不能只存一个分数。
5. 每个 Worker 步骤都需要状态可见。
6. 失败状态不得无声吞掉。

---

# 11. 后续可扩展方向

- 自动发布规则；
- 周期性采集计划；
- 更细粒度模型质量评估；
- 高级搜索缓存；
- 多模态 embedding；
- 任务仪表板；
- 使用独立消息队列升级 Worker 系统；
- 若规模扩大，再评估局部微服务拆分。
