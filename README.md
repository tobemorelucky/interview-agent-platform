# Interview Agent Platform

智能面试准备平台 — 面向求职者的面试知识库问答、简历驱动模拟面试、多源面经采集与查询系统。

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | Vue 3 + TypeScript + Vite |
| API 服务 | FastAPI + Pydantic v2 |
| 异步任务 | Celery + Redis |
| 业务数据库 | PostgreSQL 16 |
| 向量数据库 | Milvus 2.5 |
| 对象存储 | MinIO |
| 缓存/队列 | Redis 7 |
| Python 包管理 | uv |
| 前端包管理 | pnpm |

## 前置要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 22+
- [pnpm](https://pnpm.io/)
- Docker & Docker Compose

## 本地启动

### 快速启动（Windows，一键）

配置好 `.env` 后，在仓库根目录双击运行：

| 脚本 | 作用 |
|------|------|
| `start-dev.bat` | 启动 Docker + API + Worker + Web（每个服务独立窗口） |
| `stop-dev.bat` | 停止 API / Worker / Web + Docker 容器 |
| `status-dev.bat` | 查看各服务运行状态 + Docker 容器状态 |

每个服务运行在独立的 cmd 窗口中，可直接看到实时日志。关闭窗口即停止该服务。

---

### 手动逐步启动

如需手动控制每个步骤：

### Step 0: 配置环境变量

在仓库根目录创建 `.env` 文件（从 `.env.example` 复制并填入实际值）：

```bash
cp .env.example .env
# 然后编辑 .env，填入 LLM_API_KEY、EMBEDDING_API_KEY 等实际配置
```

> `.env` 已加入 `.gitignore`，不会被提交。API 和 Worker 均从仓库根目录 `.env` 自动读取配置，无需分别放置。

### 关键配置项说明

| 分类 | 环境变量 | 默认值 | 说明 |
|------|---------|--------|------|
| KB 分块 | `KB_CHUNK_SIZE` | 800 | 知识库文档分块大小（字符数），修改后需重新导入 |
| KB 分块 | `KB_CHUNK_OVERLAP` | 120 | 相邻块重叠字符数，修改后需重新导入 |
| KB 分块 | `KB_CHUNK_MIN_SIZE` | 80 | 最小 chunk 大小，小于此值的片段会被丢弃 |
| KB 分块 | `KB_EMBEDDING_BATCH_SIZE` | 20 | embedding 批处理大小 |
| RAG 检索 | `RAG_RETRIEVAL_TOP_K` | 3 | 检索召回数量，无需重新导入 |
| RAG 检索 | `RAG_CONTEXT_MAX_CHARS` | 4000 | 传入 LLM 的上下文最大字符数 |
| RAG 检索 | `RAG_CITATION_PREVIEW_CHARS` | 240 | 前端引用来源预览字符数 |

### Step 1: 启动基础设施

```bash
docker compose up -d
```

启动后可用 `docker compose ps` 确认所有服务状态为 healthy：

- PostgreSQL → `localhost:5432`
- Redis → `localhost:6379`
- Milvus → `localhost:19530`
- MinIO API → `localhost:9000`
- MinIO Console → `localhost:9001`

### Step 2: 执行数据库迁移

```bash
cd apps/api
uv sync
uv run alembic upgrade head
```

### Step 2.5: 初始化 MinIO Bucket

```bash
cd apps/api
uv run python scripts/init_minio.py
```

### Step 2.6: 初始化 Milvus Collection

```bash
cd apps/api
uv run python scripts/init_milvus.py
```

### Step 2.7: 批量导入知识库文档（可选）

准备 `.md` 或 `.txt` 文档目录，执行离线导入：

```bash
cd apps/api
uv run python scripts/import_kb.py --dir /path/to/docs
```

导入成功后，文档将被 chunk 化、embedding 化并写入 Milvus。

> **提示**：修改 `.env` 中的 `KB_CHUNK_SIZE` / `KB_CHUNK_OVERLAP` / `KB_CHUNK_MIN_SIZE` 后，需在管理后台删除旧文档并重新上传，使新的分块参数生效。

### Step 3: 创建管理员账号（可选）

```bash
cd apps/api

# Linux / macOS
ADMIN_EMAIL=admin@example.com ADMIN_USERNAME=admin ADMIN_PASSWORD=change_me_admin \
  uv run python scripts/seed_admin.py

# Windows (PowerShell)
$env:ADMIN_EMAIL="admin@example.com"
$env:ADMIN_USERNAME="admin"
$env:ADMIN_PASSWORD="change_me_admin"
uv run python scripts/seed_admin.py
```

若管理员已存在，脚本会跳过创建。

### Step 4: 启动 API 服务

```bash
cd apps/api
uv sync
uv run uvicorn interview_api.main:app --reload --host 0.0.0.0 --port 8000
```

API 文档：http://localhost:8000/docs

验证健康检查：

```bash
curl http://localhost:8000/api/v1/health
# → {"status":"ok","version":"0.1.0"}
```

### Step 5: 启动 Worker

```bash
cd apps/worker
uv sync
# macOS / Linux
uv run celery -A interview_worker.celery_app worker -l info
# Windows (prefork pool 在 Windows 上受限)
uv run celery -A interview_worker.celery_app worker -l info --pool=solo
```

看到 `[tasks]` 中包含 `process_kb_document` 表示 Worker 启动成功：

```
[tasks]
  . ping
  . process_kb_document
```

> **配置说明**：API 和 Worker 均从仓库根目录 `.env` 读取配置（复用同一 `Settings` 类），确保 broker / database / embedding 等使用相同的值。

**启动后验证任务注册**（另开终端）：

```bash
cd apps/worker
uv run celery -A interview_worker.celery_app inspect registered
# 预期输出包含:  * process_kb_document
```

**端到端验证上传→处理流程**：

1. 管理后台 http://localhost:5173/admin/kb/documents 上传一个 `.md` 文件
2. 观察 **API 日志** — 应看到：
   ```
   Dispatched process_kb_document doc_id=3 task_id=xxx-xxx broker=redis://...
   ```
3. 观察 **Worker 日志** — 几秒内应出现：
   ```
   Task received: document_id=3 ...
   [doc 3] Status -> PROCESSING
   [doc 3] Downloading from MinIO ...
   [doc 3] Chunked into N pieces
   [doc 3] Embedding ...
   [doc 3] Inserting vectors into Milvus ...
   [doc 3] Status -> INDEXED (complete)
   ```
4. 前端刷新 — 文档状态从"待处理"变为"已索引"

**诊断命令**：

```bash
# 检查 Worker 监听哪些队列
cd apps/worker
uv run celery -A interview_worker.celery_app inspect active_queues

# 检查 Worker 是否在线
uv run celery -A interview_worker.celery_app inspect ping

# 检查 Worker 统计数据
uv run celery -A interview_worker.celery_app inspect stats
```

### Step 6: 启动前端

```bash
cd apps/web
pnpm install
pnpm dev
```

浏览器打开 http://localhost:5173 查看欢迎页。

## 项目目录结构

```
interview-agent-platform/
├── apps/
│   ├── api/           # FastAPI 后端服务
│   ├── web/           # Vue3 前端
│   └── worker/        # Celery 异步任务
├── docs/              # 项目设计文档
├── scripts/           # 初始化与运维脚本
├── docker-compose.yml
├── .env.example
└── README.md
```

## 开发阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 0 | 工程骨架与基础设施 | 已完成 |
| Phase 1 | 认证与用户系统 | 已完成 |
| Phase 2 | 面试知识库问答 | 已完成 |
| Phase 3 | 简历模拟面试 | 待开始 |
| Phase 4 | 管理员采集任务框架 | 待开始 |
| Phase 5 | 牛客采集链路 | 待开始 |
| Phase 6 | 小红书图文采集 | 待开始 |
| Phase 7 | 抖音视频采集 | 待开始 |
| Phase 8 | 评测与优化 | 待开始 |

## API 端点

### 系统

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/` | 应用信息 | 公开 |
| GET | `/api/v1/health` | 健康检查 | 公开 |

### 认证 (Phase 1)

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | `/api/v1/auth/register` | 注册 | 公开 |
| POST | `/api/v1/auth/login` | 登录 | 公开 |
| GET | `/api/v1/auth/me` | 当前用户信息 | 登录 |

### 知识库管理 (Phase 2, 管理员)

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | `/api/v1/admin/kb/documents/upload` | 上传知识文档（支持批量） | ADMIN |
| GET | `/api/v1/admin/kb/documents` | 文档列表 | ADMIN |
| GET | `/api/v1/admin/kb/documents/{id}` | 文档详情+chunks | ADMIN |
| DELETE | `/api/v1/admin/kb/documents/{id}` | 删除文档（含 Milvus/MinIO/PostgreSQL） | ADMIN |

> **更新知识库内容**：当前阶段采用"删除旧文档 → 重新上传"的方式更新知识库。修改 `KB_CHUNK_SIZE` / `KB_CHUNK_OVERLAP` / embedding 模型等配置后，请在管理后台删除旧文档并重新上传，使新配置生效。

### 知识库问答 (Phase 2, 用户)

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | `/api/v1/qa/sessions` | 创建会话 | 登录 |
| GET | `/api/v1/qa/sessions` | 我的会话列表 | 登录 |
| GET | `/api/v1/qa/sessions/{id}` | 会话详情+历史消息 | 登录 |
| POST | `/api/v1/qa/chat/stream` | 流式问答 (SSE) | 登录 |

> `/api/v1/readiness`（依赖就绪检查）将在后续阶段补上。
