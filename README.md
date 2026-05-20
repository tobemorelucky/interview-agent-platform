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

看到 `celery@... ready.` 及 `[tasks] . ping` 即表示 Worker 启动成功。

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
| Phase 2 | 面试知识库问答 | 待开始 |
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

> `/api/v1/readiness`（依赖就绪检查）将在后续阶段补上。
