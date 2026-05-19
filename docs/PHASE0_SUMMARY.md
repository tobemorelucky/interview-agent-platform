# Phase 0 实施总结

## 完成时间

2026-05-19

## 完成内容

建立项目 Monorepo 工程骨架与基础设施，包括：

1. **Monorepo 目录结构** — `apps/api`、`apps/web`、`apps/worker`、`scripts/`、`docs/`
2. **Docker Compose 基础设施** — PostgreSQL 16、Redis 7、etcd、MinIO、Milvus Standalone (v2.5.4)
3. **FastAPI 最小服务** — 健康检查端点 `/api/v1/health`
4. **Celery Worker 骨架** — 可启动的 Celery app + ping 示例任务
5. **Vue3 前端欢迎页** — 展示三个功能入口卡片的欢迎页
6. **环境变量模板** — `.env.example` 覆盖全部配置分类
7. **README** — 完整的本地启动流程说明
8. **Scripts 占位** — 后续脚本清单说明

## 修改/创建的文件

| # | 文件 | 操作 |
|---|------|------|
| 1 | `.gitignore` | 修改 |
| 2 | `docker-compose.yml` | 新建 |
| 3 | `.env.example` | 新建 |
| 4 | `README.md` | 新建 |
| 5 | `apps/api/pyproject.toml` | 新建 |
| 6 | `apps/api/src/interview_api/__init__.py` | 新建 |
| 7 | `apps/api/src/interview_api/core/__init__.py` | 新建 |
| 8 | `apps/api/src/interview_api/core/config.py` | 新建 |
| 9 | `apps/api/src/interview_api/main.py` | 新建 |
| 10 | `apps/worker/pyproject.toml` | 新建 |
| 11 | `apps/worker/src/interview_worker/__init__.py` | 新建 |
| 12 | `apps/worker/src/interview_worker/config.py` | 新建 |
| 13 | `apps/worker/src/interview_worker/celery_app.py` | 新建 |
| 14 | `apps/worker/src/interview_worker/tasks/__init__.py` | 新建 |
| 15 | `apps/web/package.json` | 新建 |
| 16 | `apps/web/pnpm-lock.yaml` | 新建 |
| 17 | `apps/web/vite.config.ts` | 新建 |
| 18 | `apps/web/tsconfig.json` | 新建 |
| 19 | `apps/web/tsconfig.node.json` | 新建 |
| 20 | `apps/web/index.html` | 新建 |
| 21 | `apps/web/src/main.ts` | 新建 |
| 22 | `apps/web/src/App.vue` | 新建 |
| 23 | `apps/web/src/env.d.ts` | 新建 |
| 24 | `scripts/README.md` | 新建 |
| 25 | `docs/PHASE0_SUMMARY.md` | 新建（本文件） |

## 关键决策

1. **Milvus 拓扑**: 使用官方 Docker Compose Standalone 5 服务拓扑（postgres + redis + etcd + minio + milvus-standalone），MinIO 作为共享服务同时服务 Milvus 内部存储和项目对象存储
2. **依赖最小化**: API 仅 4 个依赖（fastapi + uvicorn + pydantic + pydantic-settings），Worker 仅 2 个（celery + redis），Web 仅 4 个（vue + vite + typescript + vue-tsc）
3. **延迟 readiness**: readiness 端点推迟到 Phase 1+，届时引入 DB/Redis/Milvus/MinIO 客户端库后再实现
4. **MinIO bucket**: `interview-agent` bucket 初始化留到后续 `scripts/init_minio.py`
5. **pnpm-lock.yaml**: 提交到版本控制，保证依赖可复现
**6. pyproject.toml**: 使用 `hatchling` 构建后端 + src-layout，`[dependency-groups]` 替代已弃用的 `[tool.uv] dev-dependencies`  
**7. Worker config**: 使用 `os.environ.get()` 替代 `pydantic-settings`，保持 Worker 依赖最小化（仅 celery + redis）

## 验证结果

- `uv sync` (API): 22 packages resolved, interview-api installed as editable
- `uv sync` (Worker): 20 packages resolved, interview-worker installed as editable
- `pnpm install` (Web): packages installed (with npm registry connectivity retries)
- `curl http://localhost:8000/api/v1/health` → `{"status":"ok","version":"0.1.0"}`
- `curl http://localhost:8000/` → `{"name":"Interview Agent Platform","version":"0.1.0"}`
- Worker 启动 (--pool=solo): 任务 `ping` 注册成功，Celery 就绪
- Windows: Celery prefork pool 有 PermissionError（已知限制），使用 `--pool=solo` 可正常启动

## 已知限制

- Docker Compose 基础设施未在本机启动验证（需要 Docker Desktop / WSL2 环境）
- 前端未启动浏览器验证（依赖安装成功，Vite dev server 配置正确）
- `/api/v1/readiness` 端点未实现，待 Phase 1+
- MinIO `interview-agent` bucket 未初始化，待后续脚本

## 下一步

进入 Phase 1：认证与基础用户系统
- 注册/登录
- JWT
- 用户角色
- 前端登录注册页面
