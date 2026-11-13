# Interview Agent Platform

面向秋招的 Multi-Agent 求职与模拟面试平台。

**简历 → 候选人画像 → 岗位匹配 → 自适应面试 → 评价与复盘 → 长期能力记忆 → 下一场训练**

## 核心设计

- **证据驱动画像**：模型选择、分类已有简历 Evidence，程序建立稳定事实，保留原文引用与版本。
- **可解释岗位匹配**：通过 Requirement、Evidence Matrix 和确定性资格判断推荐岗位，不编造匹配百分比。
- **自适应面试官**：根据回答、知识缺口和已覆盖主题选择追问、举例、深挖或换题；程序校验合法目标—题源组合及会话预算。
- **独立评价与复盘**：Evaluation 对当前回答进行五维评分，Controller 原子提交；报告统计来自持久化评价。
- **跨场次能力记忆**：区分提示后改善、独立验证、冲突和待复测，用户可更正或撤销；下一场使用冻结的 MemoryGuidedPlan。
- **个性化训练中心**：支持弱点专项、独立复测及完成反馈；Main 和 Evaluation 使用各自的上下文投影，评价可引用同场合法的父问答。
- **共享职业资料**：管理员采集、证据校验、审核后发布；普通用户只查询已发布资料，不触发收费抓取。

保留六个 Agent：**Research、Resume/Profile、Job Matching、Interview Main、Answer Evaluation、Summary/Coach**。
Agent 通过结构化产物协作；权限、证据、版本、幂等、预算与状态由程序控制。记忆聚合是服务，不额外增加 MemoryAgent。

## 技术栈

Vue 3 / TypeScript · FastAPI · SQLAlchemy / Alembic · PostgreSQL · Redis / Celery · MinIO

模型采用 Provider / ModelRouter 配置；Search/Fetch 使用独立凭证。Milvus 仅用于已发布知识检索，不参与能力记忆聚合，也不是当前演示的必需依赖。

## 模型测试配置

ModelScope 为可选测试入口。要启用，在项目根目录的本地 `.env` 中保留一条 `LLM_PROVIDER=modelscope`，填写 `MODELSCOPE_API_KEY`，再重启 API 和 Worker。默认模型为 `deepseek-ai/DeepSeek-V4.1-Flash-1017`；可用 `MODELSCOPE_MODEL=Qwen/Qwen3.5-35B-A3B` 手动切换。恢复原 `LLM_PROVIDER` 值即可恢复原有路由配置。ModelScope 模式不会自动回退到旧模型。

1017 与 Qwen 均已通过单次真实纯文本请求；**ModelScope 的 JSON 模式、原生工具调用及完整面试尚未验收**。单次 JSON 探测入口为 `apps/api/scripts/smoke_modelscope.py --send-once`，会消耗实际额度，不会自动重试。仅做演示时使用下方 Replay，不需要调用外部模型。

## 本地运行与演示

环境：Windows、Docker Desktop、Python 3.11+、uv、Node.js、pnpm。
首次安装依赖后，按 [本地部署说明](docs/14_local_development_and_deployment.md) 配置 `.env`；不要覆盖已有配置或提交密钥。

已有隔离 Replay 数据的启动方式（两个终端，项目根目录）：

```powershell
$env:REDIS_URL='redis://localhost:6379/15'
uv run --project apps/api --no-sync python apps/api/scripts/v21_acceptance_runtime.py --database interview_agent_v21_v22_browser_final_20261023_a --memory-replay
```

```powershell
node scripts/v21_acceptance_web.mjs
```

浏览器：<http://127.0.0.1:5177>；API：<http://127.0.0.1:8007/docs>。
需要原 PostgreSQL/Redis 与已有演示库；命令不建库、不迁移、不重复 seed、不调用真实模型。完整步骤与 DEMO 账号见 [7 分钟演示指南](docs/54_repeatable_demo_guide.md)。

通用本地启动、状态与停止入口为 `scripts/start-interview-platform.ps1`、`status-interview-platform.ps1`、`stop-interview-platform.ps1`。默认停止不删除数据卷。

### 启动脚本与数据库授权

通用启动会执行一次 `alembic upgrade head`，所以它必须先判断目标库是不是"可以自动迁移的库"。`scripts/delivery.py migrate` 会先按库名判定（规则集中在 `apps/api/src/interview_api/core/db_targets.py`，与 pytest 闸、测试运行器共用同一份实现）：

| 库名判定 | 例子 | 不经授权能否迁移 |
|---|---|---|
| `TEST` / `MIGRATION` / `DEMO` | `..._test_...`、`..._mig_...`、`interview_agent_m6_f3_ux_demo` | 可以 |
| `PRODUCTION` / `E2E` / `OTHER` | `interview_agent`（正式库）、`..._e2e_...` | 必须显式授权 |
| `SYSTEM` / `ARCHIVED_LIVE` | `postgres`、`..._live_...`、`..._accept_...`、`..._thinking_long...` | 永不，没有覆盖选项 |

`.env` 里的 `DATABASE_URL` **不构成授权**。如果它指向正式库，`start-dev.bat` 会在迁移这一步停下，报 `MIGRATION_REFUSED`（退出码 3），既不修改任何数据库，也不启动 API/Worker。此时有三个选择：

1. **只想看演示**：`start-dev.bat -Demo`。固定使用 Replay 演示库，无需授权，不调用真实模型。
2. **本地开发用隔离库（推荐）**：

   ```powershell
   # 建一个只给你自己用的库；不要复用 interview_agent
   docker exec interview-agent-platform-postgres-1 psql -U interview_agent -d postgres -c 'CREATE DATABASE "interview_agent_dev_20261113";'

   # 本次终端指向它，并只授权这一个名字
   $env:DATABASE_URL='postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_dev_20261113'
   .\start-dev.bat -AuthorizeDatabase interview_agent_dev_20261113
   ```

   授权字符串必须与库名**完全一致**。写 `-AuthorizeDatabase interview_agent` 只授权正式库本身，不会顺带授权其他库；写成别的名字同样会被拒绝。
3. **确实要升级正式库**：先按[0026 发布检查清单](docs/57_production_0026_release_checklist.md)确认版本与备份，再显式执行：

   ```powershell
   .\start-dev.bat -AuthorizeDatabase interview_agent
   ```

   这是运维动作，不是日常启动步骤。不要把 `-AuthorizeDatabase interview_agent` 写进脚本、快捷方式或 README 的复制粘贴默认值里——那等于把授权变成默认行为。

历史封存库（`*_live_*`、`*_accept_*`、`*_thinking_long*` 等）是验收证据，没有任何授权入口可以迁移它们。任何情况下都不要用 `docker compose down -v` 清理数据卷；停止服务用 `stop-interview-platform.ps1`。

后端测试请用 `scripts/run-workbuddy-tests.py`（自带库名闸与两段式分组），不要手工把 `DATABASE_URL` 指向 E2E 或正式库跑 pytest。

## 验收与边界

- **Replay**：隔离 PostgreSQL 完成 20 轮问答、Coach 报告及下一场 MemoryGuidedPlan。20 轮整批中性估算约 **25.2 万 Token**，含 Coach 和 Repair 预留，不是 Live 用量。
- **Live**：历史 DeepSeek 记忆联动为“Replay 历史 + Live 决策”。完整长面试在第二答因 `UNRESOLVED_KNOWLEDGE_GAP` 停止；该故障仍可复现，不能把合法样本的 Replay 通过视为完整 Live 通过。

项目是个人工程实践，不宣称生产规模、校准后的掌握概率或模型准确率提升。学历等硬资格缺少可靠结构化依据时保持 UNKNOWN；模型语义质量和外部网页可用性仍有限。旧面经发布失败记录保留，不包装成全部在线链路成功。

## 文档

- [文档索引](docs/README.md) / [演示指南](docs/54_repeatable_demo_guide.md)
- [V2.1.1 自适应决策契约](docs/50_director_source_contract_v2_1_1.md) / [V2.2 能力记忆](docs/51_capability_memory_v2_2.md)
- [真实 DeepSeek 记忆验收](docs/53_v22_live_memory_acceptance.md) / [0026 发布检查](docs/57_production_0026_release_checklist.md)
- [训练中心](docs/58_personalized_interview_training_v2_3.md) / [长面试 Live 停止记录](docs/69_v23_final_live_acceptance.md) / [20 轮 Replay 与成本估算](docs/70_v23_replay20_token_estimate.md)
- [源码讲解](docs/interview/09_v21_v22_source_map.md) / [秋招口述稿与24个追问](docs/interview/10_v21_v22_interview_materials.md)
