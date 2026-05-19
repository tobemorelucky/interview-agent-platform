# Claude Code 协同开发总方案：智能面试准备平台

> 目标：把“招聘面试模拟 + 简历追问生成 + 多源面经采集与查询”项目，拆成可由 Claude Code 稳定推进的工程化开发流程。
>
> 核心原则：**你负责边界、架构与验收；Claude Code 负责按文档、按里程碑、按任务卡执行。**

---

# 1. 最终项目定义

## 1.1 项目一句话定位

一个面向求职者的智能面试准备平台，支持：

1. 面试知识库问答；
2. 上传简历后生成面试官可能追问的问题与参考答案；
3. 管理员触发多平台面经采集任务，系统抓取牛客、小红书、抖音等内容，经解析、评分、审核后沉淀为正式面经库，普通用户只查询已发布内容。

---

## 1.2 关键边界定稿

### 普通用户能力

- 注册 / 登录；
- 使用面试知识库问答；
- 上传简历并生成个性化面试题与答案；
- 查询当前正式面经库；
- 根据公司、岗位、时间、平台、可信度筛选；
- 查看高频问题聚合、来源内容、可信度解释；
- 查看个人历史与收藏。

### 管理员能力

- 登录后台；
- 创建采集任务；
- 配置关键词、平台、采集数量、是否下载媒体、是否执行 OCR / ASR / LLM 抽取；
- 查看任务运行状态；
- 查看候选内容；
- 查看原文、解析文本、OCR、ASR、结构化抽取、评分与去重信息；
- 人工发布 / 拒绝 / 下架；
- 后续可开启自动发布规则。

### 明确不做

- 用户搜索时不实时爬取互联网；
- 原始采集内容不直接进入 Milvus；
- 未审核候选面经默认不可被用户检索；
- 第一版不做语音实时模拟面试；
- 第一版不做全自动商业级反爬对抗系统；
- 第一版不做复杂微服务拆分。

---

# 2. 最终技术架构

## 2.1 架构风格

采用：

- **模块化单体 API**；
- **独立异步 Worker**；
- **可插拔 Source Adapter**；
- **PostgreSQL 管业务结构化数据**；
- **Milvus 管正式可检索知识向量**；
- **Redis 管缓存与任务队列辅助能力**；
- **MinIO 管文件、图片、视频、音频、中间产物**。

---

## 2.2 总体系统分层

```text
Web Frontend
  └── Vue3 + TypeScript + Vite

API Service
  └── FastAPI
      ├── Auth Module
      ├── RAG QA Module
      ├── Resume Interview Module
      ├── Experience Query Module
      ├── Admin Collection Module
      └── Admin Review Module

Async Workers
  └── Celery Worker
      ├── Discovery Worker
      ├── Collector Worker
      ├── Parser Worker
      ├── OCR Worker
      ├── ASR Worker
      ├── Extractor Worker
      ├── Scorer Worker
      ├── Dedup Worker
      └── Indexer Worker

Storage Layer
  ├── PostgreSQL
  ├── Milvus
  ├── Redis
  └── MinIO
```

---

## 2.3 在线链路与离线链路彻底分离

### 在线链路

用户实时访问：

- RAG 问答；
- 简历分析；
- 正式面经库检索。

### 离线链路

管理员触发：

- 多平台采集；
- 媒体下载；
- OCR / ASR；
- LLM 抽取；
- 评分；
- 去重；
- 入候选池；
- 审核发布；
- Milvus 索引。

---

# 3. 项目仓库组织方式

## 3.1 推荐 Monorepo

```text
interview-agent-platform/
├── apps/
│   ├── web/                         # Vue3 前端
│   ├── api/                         # FastAPI 后端
│   └── worker/                      # Celery 异步任务
│
├── packages/
│   ├── shared_schemas/              # 共享 DTO / Schema
│   ├── prompt_templates/            # Prompt 模板
│   ├── domain_rules/                # 评分、规则、枚举
│   └── evals/                       # 评测脚本
│
├── infra/
│   ├── docker/                      # Dockerfile / compose 片段
│   ├── nginx/                       # Nginx 配置
│   ├── milvus/                      # Milvus 初始化脚本
│   ├── postgres/                    # 初始化脚本
│   └── monitoring/                  # 后续监控
│
├── scripts/
│   ├── bootstrap_dev.sh
│   ├── init_db.py
│   ├── init_milvus.py
│   ├── seed_admin.py
│   ├── seed_keywords.py
│   ├── rebuild_kb_index.py
│   └── rebuild_experience_index.py
│
├── docs/
│   ├── 00_project_overview.md
│   ├── 01_prd.md
│   ├── 02_architecture.md
│   ├── 03_backend_design.md
│   ├── 04_frontend_design.md
│   ├── 05_database_design.md
│   ├── 06_milvus_design.md
│   ├── 07_ingestion_pipeline.md
│   ├── 08_api_contract.md
│   ├── 09_auth_and_permission.md
│   ├── 10_admin_console.md
│   ├── 11_prompt_spec.md
│   ├── 12_task_state_machine.md
│   ├── 13_testing_strategy.md
│   ├── 14_deployment.md
│   ├── 15_roadmap.md
│   └── 16_decision_log.md
│
├── .claude/
│   ├── agents/                      # 可选：项目级子代理
│   └── settings.json                # 项目共享配置，按实际需要
│
├── CLAUDE.md
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md
```

---

# 4. 你和 Claude Code 的协作总原则

## 4.1 必须先文档化，再编码

Claude Code 最怕：

- 边界没定；
- 任务过大；
- 架构不停变；
- 一次让它同时改太多模块。

因此推荐流程：

```text
先完成项目文档
再让 Claude Code 依据文档写代码
再按阶段审查与验收
```

---

## 4.2 每次只给 Claude Code 一类任务

不建议这样：

> “帮我把后端、前端、数据库、爬虫、页面都搭好。”

建议这样：

> “先阅读 docs/01_prd.md、docs/02_architecture.md 和 docs/05_database_design.md。只完成 Phase 0 的数据库基础设施与 Alembic 初始化，不要实现业务功能。先给执行计划，待我确认后再改代码。”

---

## 4.3 大任务必须先 Plan，再执行

凡是涉及：

- 新增模块；
- 修改架构；
- 改动超过 5 个文件；
- 涉及数据库迁移；
- 涉及 Docker Compose；
- 涉及前后端接口联动；
- 涉及 Worker 流水线；

都必须先让 Claude Code：

1. 阅读指定文档；
2. 扫描当前代码；
3. 输出计划；
4. 列出改动文件；
5. 列出风险；
6. 你确认后再执行。

---

# 5. CLAUDE.md 应该怎么写

以下内容可作为项目根目录 `CLAUDE.md` 的初稿。

---

## 5.1 推荐 CLAUDE.md

```md
# Project Context

This repository implements an intelligent interview preparation platform.
The product has three core user-facing capabilities:

1. RAG-based interview knowledge Q&A;
2. Resume-driven personalized interview question generation and answer drafting;
3. Querying a curated interview-experience library built from administrator-triggered ingestion pipelines.

The administrator can create collection tasks for platforms such as Nowcoder, Xiaohongshu, and Douyin. Collected materials enter a raw pool, then pass through parsing, OCR/ASR when needed, structured extraction, scoring, deduplication, review, and only then may be published to the searchable library.

Regular users do NOT trigger live crawling. They only query already published experience records.

---

# Architecture Principles

1. Use a modular monolith for the API service, with a separate worker service for asynchronous pipelines.
2. Keep high cohesion within modules and low coupling across modules.
3. Use PostgreSQL for business data and workflow state.
4. Use Milvus only for published/searchable vectorized knowledge units, not for raw or unreviewed content.
5. Use Redis for caching and task-related support.
6. Use MinIO or S3-compatible object storage for media and intermediate artifacts.
7. Prefer interfaces/adapters over direct concrete dependencies for external integrations.
8. Keep online request flows separate from offline ingestion flows.

---

# Core Backend Modules

- auth
- users
- rag_qa
- resume_analysis
- experience_query
- admin_collection
- admin_review
- shared/common

---

# Async Worker Pipeline

The ingestion pipeline should be decomposed into explicit steps:

1. discovery
2. collection
3. raw storage
4. parsing
5. OCR / ASR when applicable
6. normalized content fusion
7. structured extraction
8. scoring
9. deduplication
10. candidate pool persistence
11. publish workflow
12. Milvus indexing for published data only

Each step should be idempotent, resumable, and observable.

---

# Development Rules

1. Do not silently change product boundaries.
2. Do not convert the system into real-time web search for regular users.
3. Do not index raw or unreviewed experience content into Milvus.
4. Do not hard-code a specific LLM, ASR, OCR, or embedding provider in the domain layer.
5. Use provider interfaces and infrastructure implementations.
6. Keep schema definitions, API contracts, and state machines explicit.
7. Prefer small, reviewable diffs.
8. For non-trivial work, first produce a plan before editing files.
9. After implementation, run relevant tests, type checks, and linting if available.
10. Update docs when architectural behavior changes.

---

# Code Style

Backend:
- Python 3.11+
- FastAPI
- SQLAlchemy 2.x style
- Alembic migrations
- Pydantic v2 models
- Typed code where practical
- Layered services and repositories

Frontend:
- Vue 3
- TypeScript
- Vite
- Pinia
- Vue Router
- Componentized pages and composables

---

# Verification Expectations

For each implementation task, Claude should report:

1. What changed;
2. Why it changed;
3. Which files changed;
4. Which commands were run;
5. Whether tests passed;
6. Known limitations or follow-up tasks.

---

# Documentation Source of Truth

Before making major changes, read the relevant docs under `docs/`.
If implementation and documentation conflict, do not guess. Surface the conflict and propose a resolution.
```

---

# 6. 建议启用的 Claude Code 能力

## 6.1 Plan Mode

用途：

- 架构设计；
- 大功能实现；
- 数据库迁移；
- 多模块重构；
- 复杂 bug 修复。

你应该形成习惯：

```text
重要改动先 Plan，确认计划后再写代码。
```

---

## 6.2 Project Memory / CLAUDE.md

建议把长期不变的项目边界写进 `CLAUDE.md`，把阶段性经验、调试结论和坑点写进项目记忆文件或专门文档中。

特别建议记录：

- 数据流边界；
- Milvus 只索引已发布内容；
- 管理员采集和普通用户查询分离；
- 不要让 Claude Code 擅自引入实时 Web Search；
- 已确定的技术栈；
- 关键表与状态机。

---

## 6.3 Subagents

建议建立 5 个项目级专用子代理。

### A. architecture-reviewer
职责：
- 只看架构与边界；
- 检查是否破坏模块解耦；
- 检查在线/离线链路是否混淆；
- 检查 Milvus 使用是否越界。

### B. backend-implementer
职责：
- FastAPI 模块；
- SQLAlchemy / Alembic；
- Service / Repository；
- API 实现。

### C. pipeline-engineer
职责：
- Worker 任务流；
- 采集器适配器；
- OCR / ASR / Extractor；
- 幂等、重试、状态机。

### D. frontend-implementer
职责：
- Vue 页面；
- 路由；
- 登录态；
- 表单与交互；
- 管理后台。

### E. qa-reviewer
职责：
- 代码审查；
- 测试设计；
- 发现未覆盖边界；
- 检查文档与代码是否一致。

---

## 6.4 Hooks

Hooks 只用于“确定性保障”，不要把核心逻辑放在 Hook 中。

建议用途：

- 大规模编辑前提醒先阅读架构文档；
- 完成任务后提醒运行测试；
- 提交前检查是否更新相关文档；
- 防止直接修改被标记为稳定的协议文件而未同步说明。

---

## 6.5 MCP

MCP 对本项目最有价值的地方不是直接接牛客/抖音，而是帮助 Claude Code 接入你的研发环境。

优先考虑：

- GitHub MCP：查看 issue / PR；
- 数据库只读 MCP：调试开发库；
- 文档库 MCP：让 Claude 读取规范文档；
- 未来如果自己做了采集器调试工具，也可以暴露成 MCP 给 Claude Code 调用。

不要把“平台爬取能力是否有 MCP”当成项目主线依赖。

---

# 7. 开发前必须先准备的文档

推荐先完成这些文档，再让 Claude Code 写核心代码。

## 7.1 `docs/01_prd.md`

内容：

- 产品背景；
- 目标用户；
- 功能模块；
- MVP / V1 / V2；
- 普通用户流程；
- 管理员流程；
- 非目标。

---

## 7.2 `docs/02_architecture.md`

内容：

- 总体架构；
- 在线链路；
- 离线链路；
- 模块边界；
- API 与 Worker 关系；
- 数据存储职责。

---

## 7.3 `docs/05_database_design.md`

内容：

- PostgreSQL 表设计；
- 表关系；
- 状态字段；
- 审核流；
- 发布流；
- 任务流。

---

## 7.4 `docs/06_milvus_design.md`

内容：

- `kb_chunks_current`；
- `experience_chunks_current`；
- `experience_questions_current`；
- 字段说明；
- 索引与过滤；
- Alias 方案；
- 重建索引策略。

---

## 7.5 `docs/07_ingestion_pipeline.md`

内容：

- Discovery；
- Collect；
- Parse；
- OCR；
- ASR；
- Extract；
- Score；
- Dedup；
- Candidate；
- Publish；
- Index。

---

## 7.6 `docs/08_api_contract.md`

内容：

- Auth API；
- RAG API；
- Resume API；
- Experience Query API；
- Admin Collection API；
- Admin Review API；
- 通用错误结构。

---

## 7.7 `docs/12_task_state_machine.md`

内容：

- Crawl Task 状态；
- Raw Content 状态；
- Candidate 状态；
- Publish 状态；
- Retry 规则；
- Failed 记录。

---

# 8. 推荐里程碑规划

## Phase 0：工程骨架与项目规范

### 目标

- 仓库骨架；
- Docker Compose；
- FastAPI / Vue3 基础；
- PostgreSQL / Redis / Milvus / MinIO 起服务；
- Alembic 初始化；
- `.env.example`；
- README；
- 初始文档。

### 不做

- 不做业务功能；
- 不做真实爬虫；
- 不做简历分析；
- 不做 RAG。

---

## Phase 1：认证与基础用户系统

### 目标

- 注册；
- 登录；
- JWT；
- 用户信息；
- 普通用户 / 管理员角色；
- 前端登录注册页；
- 路由守卫；
- 后端权限依赖。

---

## Phase 2：面试知识库问答

### 目标

- 文档导入；
- 文本 chunk；
- embedding；
- Milvus 入库；
- 检索；
- rerank 预留；
- SSE 流式回答；
- 引用片段展示；
- 聊天历史保存。

---

## Phase 3：简历模拟面试

### 目标

- 上传简历；
- PDF / DOCX 解析；
- 简历结构化；
- 项目追问点识别；
- 题目生成；
- 参考答案；
- 报告保存与查看；
- 前端交互。

---

## Phase 4：管理员采集任务框架

### 目标

- 管理员创建采集任务；
- 任务配置；
- Celery 任务骨架；
- 任务状态机；
- 任务列表页；
- 执行日志基础展示；
- 先接 `MockSourceAdapter`。

### 重要原则

这个阶段先不碰真实平台采集，先把“任务系统”跑通。

---

## Phase 5：牛客采集链路

### 目标

- NowcoderAdapter；
- 按关键词发现候选帖子；
- 获取详情；
- 原始内容入 Raw Pool；
- 解析正文；
- LLM 抽取面经结构；
- 规则 + AI 评分；
- Candidate Pool；
- 管理员审核页；
- 发布到正式面经库；
- 发布后入 Milvus；
- 普通用户可查询。

---

## Phase 6：小红书图文链路

### 目标

- XiaohongshuAdapter；
- 笔记文本解析；
- 图片下载；
- OCR；
- 融合正文；
- 结构化抽取；
- 候选审核；
- 发布检索。

---

## Phase 7：抖音视频链路

### 目标

- DouyinAdapter；
- 视频元数据；
- 视频下载；
- FFmpeg 提取音频；
- ASR；
- 视频关键帧 OCR；
- ASR + OCR 融合；
- 结构化抽取；
- 候选审核；
- 发布检索。

---

## Phase 8：质量提升与评测

### 目标

- 检索评测集；
- 简历分析评测集；
- 面经抽取评测集；
- 评分一致性评估；
- 失败重跑；
- 缓存与性能优化；
- 管理面板统计。

---

# 9. 每个阶段可直接复制给 Claude Code 的指令

下面这些是开发时可以直接使用的“任务启动 Prompt”。

---

## 9.1 Phase 0：项目骨架

```text
请先阅读以下文档：
- docs/01_prd.md
- docs/02_architecture.md
- docs/15_roadmap.md
- CLAUDE.md

本次任务只实现 Phase 0：工程骨架与基础设施。

目标：
1. 建立 Monorepo 基础目录；
2. 初始化 apps/api、apps/web、apps/worker；
3. 提供 docker-compose.yml，包含 PostgreSQL、Redis、Milvus Standalone、MinIO；
4. 初始化 FastAPI app；
5. 初始化 Vue3 + TypeScript + Vite 前端；
6. 初始化 Celery worker 骨架；
7. 提供 .env.example；
8. 提供 README 的本地启动说明；
9. 提供最小健康检查接口与最小前端欢迎页。

约束：
- 不要实现业务功能；
- 不要创建与 PRD 不一致的新模块；
- 不要先写爬虫；
- 先输出执行计划、拟修改文件和风险点，等我确认后再实际改代码。
```

---

## 9.2 Phase 1：登录注册

```text
请阅读：
- docs/01_prd.md
- docs/03_backend_design.md
- docs/04_frontend_design.md
- docs/08_api_contract.md
- docs/09_auth_and_permission.md
- CLAUDE.md

本次任务实现 Phase 1：认证与基础用户系统。

要求：
1. PostgreSQL 用户表与角色字段；
2. Alembic migration；
3. 注册接口；
4. 登录接口；
5. JWT access token；
6. 当前用户接口；
7. 管理员权限依赖；
8. 前端登录、注册页面；
9. 路由守卫；
10. 基础错误处理。

约束：
- 不要实现第三方 OAuth；
- 不要实现忘记密码；
- 不要实现多租户；
- 所有新增 API 必须与 docs/08_api_contract.md 对齐；
- 先给计划，后执行。
```

---

## 9.3 Phase 2：知识库问答

```text
请阅读：
- docs/01_prd.md
- docs/02_architecture.md
- docs/06_milvus_design.md
- docs/08_api_contract.md
- docs/11_prompt_spec.md
- CLAUDE.md

本次任务实现 Phase 2：面试知识库问答。

目标：
1. 建立知识库文档和 chunk 的 PostgreSQL 元数据表；
2. 提供离线导入脚本；
3. 定义 EmbeddingProvider 与默认实现；
4. 建立 Milvus `kb_chunks_current` 写入与检索服务；
5. 提供问答 API；
6. 提供 SSE 流式输出；
7. 保存会话和消息历史；
8. 前端完成聊天页面、引用片段显示和历史会话切换。

约束：
- 先做最小可用检索，不要过度扩展；
- RerankProvider 接口需要预留，但可先使用 no-op 或简单实现；
- 不要把面经库和知识库混为一个 collection；
- 先给计划，后执行。
```

---

## 9.4 Phase 3：简历模拟面试

```text
请阅读：
- docs/01_prd.md
- docs/02_architecture.md
- docs/03_backend_design.md
- docs/08_api_contract.md
- docs/11_prompt_spec.md
- CLAUDE.md

本次任务实现 Phase 3：简历模拟面试。

目标：
1. 上传 PDF / DOCX；
2. 保存原始文件到对象存储；
3. 解析文本；
4. 将简历解析成结构化 schema；
5. 根据技能与项目生成可能追问问题；
6. 为问题生成参考答案、回答要点、继续追问；
7. 保存分析报告；
8. 提供前端上传页、分析中状态、报告页。

约束：
- 不要假设简历解析一定成功；
- 对解析失败和空文本做好错误处理；
- 保留中间结果，便于后续调试；
- 不要把简历内容自动发布到公共内容库；
- 先给计划，后执行。
```

---

## 9.5 Phase 4：管理员采集任务框架

```text
请阅读：
- docs/01_prd.md
- docs/02_architecture.md
- docs/07_ingestion_pipeline.md
- docs/08_api_contract.md
- docs/10_admin_console.md
- docs/12_task_state_machine.md
- CLAUDE.md

本次任务实现 Phase 4：管理员采集任务框架。

目标：
1. 设计 crawl_plan / crawl_run / crawl_task 相关表；
2. 提供管理员创建任务、查看任务列表、查看任务详情接口；
3. 建立 Celery 任务投递与状态更新；
4. 先实现 MockSourceAdapter，用假数据完整跑通任务状态机；
5. 管理后台前端完成采集任务创建页、任务列表页、任务详情页；
6. 明确日志与错误状态。

约束：
- 本阶段不接真实牛客/小红书/抖音；
- 目标是把任务编排系统跑通；
- 任务必须可追踪、可失败、可重试；
- 先给计划，后执行。
```

---

## 9.6 Phase 5：牛客采集链路

```text
请阅读：
- docs/07_ingestion_pipeline.md
- docs/10_admin_console.md
- docs/12_task_state_machine.md
- docs/05_database_design.md
- docs/06_milvus_design.md
- CLAUDE.md

本次任务实现 Phase 5：牛客采集链路。

目标：
1. 实现 NowcoderAdapter；
2. 根据管理员关键词发现帖子；
3. 抓取详情页与正文；
4. 原始内容入 Raw Pool；
5. 解析正文形成 Parsed Content；
6. 调用结构化抽取流程，生成 Candidate Experience；
7. 基于规则与 AI 给出可信度评分；
8. 管理后台支持审核、发布、拒绝；
9. 发布后写入正式面经库；
10. 仅已发布内容写入 Milvus `experience_chunks_current` 与 `experience_questions_current`；
11. 普通用户端支持查询已发布牛客面经。

约束：
- 任何 raw / candidate 未审核内容不得进入 Milvus；
- 用户查询不得触发实时采集；
- 内容去重必须预留；
- 对采集失败必须留日志；
- 先给计划，后执行。
```

---

## 9.7 Phase 6：小红书图文链路

```text
请阅读：
- docs/07_ingestion_pipeline.md
- docs/10_admin_console.md
- docs/12_task_state_machine.md
- CLAUDE.md

本次任务实现 Phase 6：小红书图文内容链路。

目标：
1. 实现 XiaohongshuAdapter；
2. 抓取标题、正文、标签、图片资源与基础元数据；
3. 下载图片到对象存储；
4. 建立 OCRProvider 与默认实现；
5. 对图片执行 OCR；
6. 融合正文与 OCR 文本；
7. 继续复用 Candidate / Score / Review / Publish / Index 流程；
8. 前端候选内容审核页应展示 OCR 文本。

约束：
- 不要破坏 Phase 5 已有发布链路；
- OCR 结果必须可回溯；
- 图片和文本之间要有可追踪关系；
- 先给计划，后执行。
```

---

## 9.8 Phase 7：抖音视频链路

```text
请阅读：
- docs/07_ingestion_pipeline.md
- docs/10_admin_console.md
- docs/12_task_state_machine.md
- CLAUDE.md

本次任务实现 Phase 7：抖音视频面经链路。

目标：
1. 实现 DouyinAdapter；
2. 获取视频标题、简介、发布时间、互动数据与媒体链接；
3. 下载视频到对象存储；
4. 使用 FFmpeg 生成音频；
5. 定义 ASRProvider 与默认实现；
6. 生成转写文本；
7. 抽关键帧并执行 OCR；
8. 融合 ASR 与 OCR；
9. 复用抽取、评分、审核、发布、索引流程；
10. 审核页展示视频摘要、ASR、OCR、结构化结果。

约束：
- 媒体任务必须异步执行；
- 大文件处理要有失败与重试机制；
- ASR 与 OCR 的结果都必须持久化；
- 先给计划，后执行。
```

---

# 10. 你如何验收 Claude Code 每次交付

每个任务完成后，要求 Claude Code 固定给出：

```text
1. 本次完成了什么；
2. 哪些文件被修改；
3. 数据库是否新增 migration；
4. 新增或变更了哪些 API；
5. 运行了哪些命令；
6. 测试结果；
7. 目前遗留问题；
8. 下一阶段建议。
```

---

## 10.1 你的审查清单

### 架构审查
- 是否破坏了模块边界；
- 是否把在线查询和离线采集混在一起；
- 是否直接在 API 中执行重型媒体处理；
- 是否未经审核就入 Milvus；
- 是否写死外部供应商；
- 是否缺乏接口抽象。

### 工程审查
- 是否有类型标注；
- 是否有错误处理；
- 是否有日志；
- 是否有状态字段；
- 是否有 idempotency；
- 是否有最基本测试；
- 是否更新文档。

### 产品审查
- 是否符合当前阶段范围；
- 是否偷做了你没要求的功能；
- 是否把页面做得太花而忽略核心流程；
- 是否引入了无法维护的复杂度。

---

# 11. 分支与提交建议

## 11.1 分支策略

```text
main
  ├── feat/phase-0-bootstrap
  ├── feat/auth-foundation
  ├── feat/rag-qa
  ├── feat/resume-interview
  ├── feat/admin-collection-framework
  ├── feat/nowcoder-ingestion
  ├── feat/xhs-ingestion
  └── feat/douyin-video-ingestion
```

---

## 11.2 每阶段尽量形成小提交

推荐：

```text
feat(auth): add user and role models
feat(auth): implement JWT login flow
feat(web-auth): add login and register pages
```

不要让 Claude Code 一次提交一个“巨型总改动”。

---

# 12. 最容易翻车的地方与预防措施

## 12.1 翻车点：Claude Code 自作主张改变边界

预防：

- `CLAUDE.md` 写死关键边界；
- 每次任务明确“不做什么”；
- 大任务先 Plan。

---

## 12.2 翻车点：Worker 流程被写成一坨同步逻辑

预防：

- 明确每一步状态；
- 每一步单独任务；
- 强制失败可重试。

---

## 12.3 翻车点：Milvus 被当垃圾桶

预防：

- 只有 Published 内容才能 Index；
- Raw / Candidate 只进 PostgreSQL + MinIO；
- 所有 Indexer 逻辑都检查发布状态。

---

## 12.4 翻车点：供应商写死

预防：

- LLMProvider；
- EmbeddingProvider；
- RerankProvider；
- OCRProvider；
- ASRProvider；
- SourceAdapter。

---

## 12.5 翻车点：爬虫和业务耦合

预防：

- Adapter 层只负责平台接入；
- 业务层只处理统一标准格式；
- 不让平台字段渗透到领域模型中。

---

# 13. 最终推荐开发顺序

1. 先让我为项目生成完整文档包；
2. 你用 Claude Code Phase 0 指令搭骨架；
3. 我帮你审查 Phase 0 结果；
4. 再推进 Phase 1 登录注册；
5. 然后 Phase 2 RAG；
6. Phase 3 简历；
7. Phase 4 任务系统；
8. Phase 5 牛客；
9. Phase 6 小红书；
10. Phase 7 抖音；
11. 最后做评测、性能、部署和 README 打磨。

---

# 14. 我建议你和 Claude Code 的真实协作节奏

## 每个阶段固定 5 步

```text
Step 1：我帮你确定阶段需求与边界
Step 2：你把阶段任务 Prompt 发给 Claude Code
Step 3：Claude Code 先出 Plan
Step 4：你把 Plan 发给我，我帮你审查
Step 5：通过后 Claude Code 实施，实施后我再帮你 Code Review
```

这比你直接让 Claude Code 从头写到底更稳。

---

# 15. 项目启动时最推荐你先做的一件事

在真正写代码之前，先准备：

```text
- PRD
- Architecture
- Database Design
- Milvus Design
- Ingestion Pipeline
- API Contract
- Task State Machine
- CLAUDE.md
```

然后再开 Phase 0。

这会显著减少后面反复推翻。

---

# 16. 当前项目的最终实施口径

后续所有开发都以这句话为总原则：

> 本系统不是一个实时全网搜索聊天机器人，而是一个面向面试准备的知识与内容平台。普通用户消费已发布的知识与面经内容；管理员负责驱动内容采集、解析、评分、审核与发布。在线服务追求稳定响应，离线流水线追求可重试、可回溯、可扩展。Milvus 只承载正式可检索知识单元的向量索引，PostgreSQL 与 MinIO 负责完整业务状态与原始资料留存。

