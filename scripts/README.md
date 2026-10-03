# Scripts

F4 最终本地入口：

- `check-environment.ps1`：只读依赖/环境检查，不安装。
- `start-interview-platform.ps1`：`-Demo`、`-WithSearch`、`-SkipInfrastructure`。
- `stop-interview-platform.ps1`：默认只停拥有的进程；`-StopInfrastructure` 非破坏停容器。
- `status-interview-platform.ps1`：PID ownership + HTTP/TCP + Celery ping + DB revision。
- `delivery.py` / `platform-common.ps1`：单一配置/进程管理实现，无业务 Agent 改动。
- `run-regression.py`：新独立 F4 测试库，拒绝复用旧库。
- `audit-delivery.py`：secret、Git artifact、冻结与 backup 检查；不输出凭据。
- `check-whitespace.py`：标准 Git 空白检查 + 16 个精确路径/行号的历史 EOF 例外；同时验证冻结文件 SHA-256 和 index blob，不豁免 trailing whitespace。`--self-test` 在临时 Git 仓库验证拒绝规则。

下方仅为 Phase 0 历史规划，不代表脚本缺失。完整命令见根 README。

本目录存放初始化、维护和运维脚本。

## 计划后续创建的脚本（Phase 0 不实现）

- `init_db.py` — 数据库初始化（创建表、执行迁移）
- `init_milvus.py` — Milvus collections 初始化
- `init_minio.py` — MinIO bucket 初始化
- `seed_admin.py` — 创建管理员账号
- `seed_kb.py` — 知识库数据导入
- `rebuild_index.py` — 重建向量索引

Phase 0 仅创建本占位说明，不实现任何业务脚本。
