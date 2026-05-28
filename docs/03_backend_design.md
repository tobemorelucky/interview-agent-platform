# 03 后端设计

## 1. 后端目标

后端需要满足：

- 清晰模块边界；
- 适配 AI/RAG/异步任务；
- 易于 Claude Code 分阶段扩展；
- 支持长期演进；
- 支持本地开发和容器部署。

---

# 2. 技术栈

- Python 3.11+
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- Celery
- Redis
- PostgreSQL
- Milvus
- MinIO / S3-compatible storage
- `uv` 管理依赖

---

# 3. 推荐目录结构

```text
apps/api/
├── pyproject.toml
├── src/
│   └── interview_api/
│       ├── main.py
│       ├── core/
│       │   ├── config.py
│       │   ├── logging.py
│       │   ├── security.py
│       │   ├── exceptions.py
│       │   └── response.py
│       │
│       ├── modules/
│       │   ├── auth/
│       │   ├── users/
│       │   ├── rag_qa/
│       │   ├── resume_analysis/
│       │   ├── experience_query/
│       │   ├── admin_collection/
│       │   ├── admin_review/
│       │   └── common/
│       │
│       ├── infrastructure/
│       │   ├── db/
│       │   ├── redis/
│       │   ├── milvus/
│       │   ├── storage/
│       │   ├── llm/
│       │   ├── embedding/
│       │   ├── reranker/
│       │   ├── ocr/
│       │   ├── asr/
│       │   └── source_adapters/
│       │
│       ├── api/
│       │   └── deps.py
│       │
│       └── tests/
│
└── alembic/
```

Worker 目录：

```text
apps/worker/
├── pyproject.toml
├── src/
│   └── interview_worker/
│       ├── celery_app.py
│       ├── tasks/
│       │   ├── collection_tasks.py
│       │   ├── parsing_tasks.py
│       │   ├── media_tasks.py
│       │   ├── extraction_tasks.py
│       │   ├── scoring_tasks.py
│       │   ├── dedup_tasks.py
│       │   └── indexing_tasks.py
│       ├── pipelines/
│       │   ├── ingestion_pipeline.py
│       │   └── publish_pipeline.py
│       └── tests/
```

---

# 4. 模块设计

## 4.1 auth 模块

职责：

- 注册
- 登录
- JWT 生成
- 当前用户认证
- 密码哈希与校验

主要文件建议：

```text
router.py
schemas.py
service.py
models.py
repository.py
```

---

## 4.2 users 模块

职责：

- 获取当前用户资料
- 更新基础资料（后续）
- 角色读取

---

## 4.3 rag_qa 模块

职责：

- 管理知识库问答会话
- 召回上下文
- 调用 LLM 流式生成
- 保存问答历史

建议拆分：

```text
retrieval_service.py
chat_service.py
citation_builder.py
conversation_repository.py
```

---

## 4.4 resume_analysis 模块

职责：

- 简历上传记录
- 触发异步分析任务
- 返回报告状态
- 查询报告详情

建议拆分：

```text
upload_service.py
resume_parse_service.py
resume_analysis_service.py
report_repository.py
```

---

## 4.5 experience_query 模块

职责：

- 查询正式面经库
- 解析筛选条件
- 搜索 Milvus 正式向量库
- 回表 PostgreSQL
- 组装聚合结果

不负责：

- 采集
- 审核
- 候选内容处理

---

## 4.6 admin_collection 模块

职责：

- 创建采集计划
- 创建即时采集任务
- 查看任务状态
- 查看任务明细

---

## 4.7 admin_review 模块

职责：

- 查看候选内容
- 查看解析详情
- 发布
- 拒绝
- 下架
- 重新评分
- 重新解析
- 触发索引重建（选定对象）

---

## 4.8 experience 模块（Phase 4）

职责：

- 管理员创建面经采集任务（时间范围+关键词+平台）
- SearXNG SearchProvider 搜索 URL
- ContentFetcher（httpx+trafilatura）抓取网页正文
- Extraction Agent 抽取面经/题目/答案
- Routing Agent 题目分类与路由
- Reliability Agent 可信度与广告/卖课检测
- 与已有面经去重
- 管理员审核（通过/拒绝）
- 可选写入结构化题库
- 可选写入 Milvus 向量索引
- 用户查询已发布面经总结

模块目录：

```
apps/api/src/interview_api/modules/experience/
  models.py
  schemas.py
  repository.py
  router.py
  service.py
  search/
    provider.py          # SearchProvider 抽象
    searxng_provider.py  # SearXNG 实现
  fetchers/
    provider.py          # ContentFetcher 抽象
    httpx_fetcher.py     # httpx + trafilatura
    browser_fetcher.py   # Playwright/Crawl4AI（可选）
  agents/
    extraction.py        # Extraction Agent
    routing.py           # Routing Agent
    reliability.py       # Reliability Agent
  dedup.py               # 去重
  indexing.py            # Milvus 索引

apps/worker/src/interview_worker/tasks/experience_tasks.py
```

---

# 5. 数据访问模式

## 5.1 Repository 模式

每个模块不直接把 SQLAlchemy 查询散落在 service 里。  
使用 Repository 隔离数据访问。

示例：

```python
class UserRepository:
    async def get_by_email(self, email: str): ...
    async def create_user(self, data): ...
```

## 5.2 Unit of Work 预留

如果代码复杂度上升，可进一步引入 UoW，但第一版可先通过 session 生命周期控制事务。

---

# 6. 标准响应结构

建议统一：

```json
{
  "code": "OK",
  "message": "success",
  "data": {}
}
```

错误：

```json
{
  "code": "AUTH_INVALID_CREDENTIALS",
  "message": "Invalid username or password",
  "data": null
}
```

---

# 7. 配置管理

建议统一由 `core/config.py` 基于环境变量加载。

分类：

```text
APP_*
DATABASE_*
REDIS_*
MILVUS_*
MINIO_*
JWT_*
LLM_*
EMBEDDING_*
RERANK_*
OCR_*
ASR_*
CELERY_*
```

要求：

- `.env.example` 必须完整；
- 真实密钥不得入库；
- Claude Code 修改配置后必须同步 `.env.example` 与文档。

---

# 8. 日志设计

结构化字段建议：

```text
request_id
user_id
task_id
content_id
module
stage
elapsed_ms
status
error_code
```

## 8.1 API 日志

- 请求入口
- 核心业务成功/失败
- 慢查询提示

## 8.2 Worker 日志

- 每个 stage 开始/结束
- 状态推进
- 重试次数
- 错误堆栈

---

# 9. 异常设计

异常分类：

- AuthException
- PermissionException
- NotFoundException
- ConflictException
- ValidationException
- ExternalProviderException
- PipelineStageException

API 层统一转为标准 JSON 错误响应。

---

# 10. 异步任务原则

## 10.1 哪些应该异步

- 简历深度分析
- 内容采集
- 图片 OCR
- 视频 ASR
- 向量批量索引
- 重新解析与重新评分

## 10.2 哪些可同步

- 登录注册
- 正式面经普通查询
- 已准备好索引的 RAG 查询
- 管理员查看任务状态

---

# 11. Provider 设计

## 11.1 LLMProvider

```python
class LLMProvider(Protocol):
    async def chat(self, messages: list[dict], **kwargs): ...
    async def structured_output(self, schema, messages: list[dict], **kwargs): ...
```

## 11.2 EmbeddingProvider

```python
class EmbeddingProvider(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...
```

## 11.3 OCRProvider / ASRProvider

```python
class OCRProvider(Protocol):
    async def recognize_images(self, image_paths: list[str]): ...

class ASRProvider(Protocol):
    async def transcribe_audio(self, audio_path: str): ...
```

---

# 12. 安全设计

- 密码哈希；
- JWT；
- Admin API 权限依赖；
- 用户只能访问自己的简历与报告；
- 上传文件类型校验；
- 文件名与对象存储 key 不使用用户原始路径；
- 限制异常时回显敏感信息。

---

# 13. API 设计原则

- RESTful 风格；
- 管理员 API 统一 `/api/v1/admin/...`；
- 普通功能统一 `/api/v1/...`；
- 长耗时操作返回任务 ID；
- 详情接口可根据任务 ID 查询状态；
- SSE 专用于流式问答，不滥用。

---

# 14. 后端开发顺序建议

1. core/config + logging + DB 基础；
2. auth/users；
3. chat/session；
4. Milvus 抽象；
5. resume；
6. admin task framework；
7. ingestion adapters；
8. review and publish；
9. evaluation and monitoring。

---

# 15. 后端验收清单

- 目录符合设计；
- 依赖由 uv 管理；
- migrations 可执行；
- API 文档可访问；
- 权限控制有效；
- Worker 可启动；
- 所有重任务未写在请求线程；
- Milvus 规则未被破坏；
- 基础测试存在。
