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

通用本地启动、状态与停止入口为 `scripts/start-interview-platform.ps1`、`status-interview-platform.ps1`、`stop-interview-platform.ps1`。
**通用启动包含 migration；现有正式库仍为 0025，升级 0026 需先按 [发布检查清单](docs/57_production_0026_release_checklist.md) 单独确认，不要将通用启动当作只读演示入口。** 默认停止不删除数据卷。

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
