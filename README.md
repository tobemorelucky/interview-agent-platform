# Interview Agent Platform

面向秋招的 Multi-Agent 求职与模拟面试平台。

**简历 → 候选人画像 → 岗位匹配 → 自适应面试 → 评价与复盘 → 长期能力记忆 → 下一场训练**

## 核心设计

- **证据驱动画像**：模型选择、分类已有简历 Evidence，程序建立稳定事实，保留原文引用与版本。
- **可解释岗位匹配**：通过 Requirement、Evidence Matrix 和确定性资格判断推荐岗位，不编造匹配百分比。
- **自适应面试官**：根据回答、知识缺口和已覆盖主题选择追问、举例、深挖或换题；程序校验合法目标—题源组合及会话预算。
- **独立评价与复盘**：Evaluation 对当前回答进行五维评分，Controller 原子提交；报告统计来自持久化评价。
- **跨场次能力记忆**：区分提示后改善、独立验证、冲突和待复测，用户可更正或撤销；下一场使用冻结的 MemoryGuidedPlan。
- **共享职业资料**：管理员采集、证据校验、审核后发布；普通用户只查询已发布资料，不触发收费抓取。

保留六个 Agent：**Research、Resume/Profile、Job Matching、Interview Main、Answer Evaluation、Summary/Coach**。
Agent 通过结构化产物协作；权限、证据、版本、幂等、预算与状态由程序控制。记忆聚合是服务，不额外增加 MemoryAgent。

## 技术栈

Vue 3 / TypeScript · FastAPI · SQLAlchemy / Alembic · PostgreSQL · Redis / Celery · MinIO

模型采用 Provider / ModelRouter 配置；Search/Fetch 使用独立凭证。Milvus 仅用于已发布知识检索，不参与能力记忆聚合，也不是当前演示的必需依赖。

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

- V2.2 完整隔离回归记录：后端 **510 passed**、前端 **43/43**，并完成真实浏览器 Replay 闭环；这是历史验收结果，非每次启动重新测试。
- DeepSeek 记忆联动：**6 次请求、35,585 Token**；已验证 Kafka 后选择 Redis，能力冲突时主动验证 Kafka。属于 **Replay 历史 + Live 决策**，不是完整多场次 Live。
- 工程收尾复查：后端定向 28 passed、前端 43/43；未将未执行项目计为通过。

项目是个人工程实践，不宣称生产规模、校准后的掌握概率或模型准确率提升。学历等硬资格缺少可靠结构化依据时保持 UNKNOWN；模型语义质量和外部网页可用性仍有限。旧面经发布失败记录保留，不包装成全部在线链路成功。

## 文档

- [文档索引](docs/README.md) / [演示指南](docs/54_repeatable_demo_guide.md)
- [V2.1.1 自适应决策契约](docs/50_director_source_contract_v2_1_1.md) / [V2.2 能力记忆](docs/51_capability_memory_v2_2.md)
- [真实 DeepSeek 记忆验收](docs/53_v22_live_memory_acceptance.md) / [0026 发布检查](docs/57_production_0026_release_checklist.md)
- [源码讲解](docs/interview/09_v21_v22_source_map.md) / [秋招口述稿与24个追问](docs/interview/10_v21_v22_interview_materials.md)
