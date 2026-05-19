# 14 本地开发与部署说明

## 1. 文档目标

指导开发者和 Claude Code：

- 如何准备本地环境；
- 如何创建 Python 虚拟环境；
- 如何启动 Docker 基础设施；
- 如何启动 API / Worker / Frontend；
- 如何执行迁移与初始化；
- 如何进行后续容器化演进。

---

# 2. 推荐开发环境

## 2.1 操作系统

推荐：

- Linux
- macOS
- Windows + WSL2

若使用 Windows，建议代码项目放在 WSL2 文件系统中，而不是跨文件系统频繁访问。

---

# 3. 必要软件

建议安装：

- Git
- Docker Desktop 或 Docker Engine
- Docker Compose
- Python 3.11+
- uv
- Node.js LTS
- pnpm

---

# 4. Python 虚拟环境规范

本项目统一使用：

```text
uv
```

## 4.1 创建虚拟环境

在对应 Python 子项目目录执行：

```bash
uv venv
```

## 4.2 安装依赖

```bash
uv sync
```

## 4.3 执行命令

```bash
uv run python -m ...
uv run pytest
uv run alembic upgrade head
```

---

# 5. 前端依赖规范

在 `apps/web/` 中：

```bash
pnpm install
pnpm dev
```

不要混用 npm/yarn 生成额外 lockfile。

---

# 6. Docker Compose 基础设施

Phase 0 需要建立：

```text
docker-compose.yml
```

至少包含：

- PostgreSQL
- Redis
- Milvus Standalone
- MinIO

## 6.1 启动基础设施

```bash
docker compose up -d
```

## 6.2 查看状态

```bash
docker compose ps
```

## 6.3 停止

```bash
docker compose down
```

如需删除 volume：

```bash
docker compose down -v
```

---

# 7. 推荐的环境变量文件

根目录：

```text
.env.example
```

建议包含：

```env
APP_ENV=development
APP_DEBUG=true

DATABASE_URL=postgresql+psycopg://...
REDIS_URL=redis://localhost:6379/0

MILVUS_HOST=localhost
MILVUS_PORT=19530

MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=interview-agent

JWT_SECRET_KEY=change_me
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=120

LLM_PROVIDER=openai_compatible
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=

EMBEDDING_PROVIDER=
EMBEDDING_MODEL=

OCR_PROVIDER=
ASR_PROVIDER=
```

---

# 8. 后端启动顺序

## 8.1 启动基础设施

```bash
docker compose up -d
```

## 8.2 初始化 API 依赖

```bash
cd apps/api
uv sync
```

## 8.3 执行迁移

```bash
uv run alembic upgrade head
```

## 8.4 启动 API

```bash
uv run uvicorn interview_api.main:app --reload --host 0.0.0.0 --port 8000
```

---

# 9. Worker 启动

```bash
cd apps/worker
uv sync
uv run celery -A interview_worker.celery_app worker -l info
```

如果后续启用定时任务：

```bash
uv run celery -A interview_worker.celery_app beat -l info
```

---

# 10. 前端启动

```bash
cd apps/web
pnpm install
pnpm dev
```

---

# 11. 初始化脚本建议

根目录 `scripts/`：

```text
init_db.py
init_milvus.py
seed_admin.py
seed_keywords.py
```

## 11.1 创建管理员

建议：

```bash
uv run python scripts/seed_admin.py
```

实际脚本读取环境变量，不要硬编码密码。

## 11.2 初始化 Milvus

```bash
uv run python scripts/init_milvus.py
```

---

# 12. Phase 0 对 Claude Code 的环境要求

Claude Code 实现 Phase 0 时必须：

1. 建立基础目录；
2. 建立 `.env.example`；
3. 建立 `docker-compose.yml`；
4. 建立 API / Worker / Web 最小骨架；
5. README 写明本地启动流程；
6. 不擅自引入其他依赖管理工具；
7. 不直接开始业务功能开发。

---

# 13. 本地开发模式建议

建议开发时：

- PostgreSQL / Redis / Milvus / MinIO 用 Docker；
- API / Worker / Web 本地运行；
- 便于调试与热更新。

后续需要容器化时，再为 API / Worker / Web 添加 Dockerfile。

---

# 14. 部署方向

免费公开上线的初期建议：

```text
Frontend: Nginx / 静态托管
API: Docker container
Worker: Docker container
PostgreSQL: 云数据库或容器
Redis: 容器或托管
Milvus: Standalone 起步
MinIO: 容器或对象存储服务
```

后续规模增长可升级：

- Milvus Cluster
- Worker 横向扩展
- 更完善监控
- 任务队列升级

---

# 15. 常见问题预留

## 15.1 Docker 容器启动但应用连不上

排查：

- 环境变量 host 是否错用 localhost / service name；
- 容器内外访问方式是否混淆；
- 端口是否暴露。

## 15.2 Alembic 无法连接数据库

排查：

- DATABASE_URL；
- PostgreSQL 容器是否 ready；
- 数据库名、用户名、密码。

## 15.3 Milvus 未就绪

排查：

- 依赖服务是否启动；
- init 脚本是否有重试等待；
- Milvus 端口是否可访问。

---

# 16. 验收

Phase 0 完成后必须满足：

- `docker compose up -d` 可用；
- API 健康检查可访问；
- Worker 能启动；
- 前端欢迎页可访问；
- `.env.example` 清晰；
- README 本地启动步骤完整；
- 项目没有混入未约定的依赖管理方式。
