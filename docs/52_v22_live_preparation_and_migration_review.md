# V2.2 Live 验收准备与 0026 迁移审查

状态：**非收费准备已完成，真实模型与正式库迁移分别等待授权**。

数据库准备、回归和迁移演练执行于 2026-10-23；2026-10-24 重启后复核保存记录并完成本文。不将重启前结果表述为重启后重新执行。

## 1. 本轮边界与实际改动

- 未新增业务能力、Agent、API 或 migration，未改变现有模型配置、`.env` 或正式业务记录。
- 新增 `apps/api/scripts/prepare_v22_live.py`：仅建立隔离 Replay 历史、冻结未来 Live Session、捕获请求预览；没有执行 Live 的模式。
- 新增 `apps/api/scripts/review_v22_migration.py`：只读检查表、内容指纹、索引、约束和待验收 Session；DDL 锁演练仅允许专用隔离库，事务最终回滚。
- 本文及文档索引为交付记录；既有所有未提交工作继续保留，未 commit/push。

准备脚本同时禁止 CompatibleModelProvider 的在线生成和 HTTP 客户端发送。其 Replay 历史通过现有服务创建、作答、评价、完成和聚合，而非 SQL 裸写能力结论。只允许全新、用户表为空且 revision=0026 的限定命名隔离库，输出不得覆盖既有文件。

## 2. 两组准备好的场景

隔离库：`interview_agent_v21_v22_live_prepare_20261023_a`，revision **0026**。

| 场景 | 明确标记的 Replay 历史 | 能力结果 | 待 Live Session | 冻结规划 |
| --- | --- | --- | --- | --- |
| A | 用户 1；Session 2、3；两次有效独立验证 | VERIFIED | 4，READY | Kafka 进入 deprioritized_targets，原因 RECENT_INDEPENDENT_VERIFICATION |
| B | 用户 2；Session 6、7、8；两次独立正确后一次同条件错误 | CONFLICTED | 9，READY | Kafka 为 INDEPENDENT_VERIFICATION，needs_recheck=true |

历史标题均标记 `DEMO REPLAY HISTORY`；待验收标题为 `DEMO LIVE PENDING AUTHORIZATION`。原始观察、场景、版本和冻结快照可核对，不修改历史以强迫模型输出某种动作。能力推断未写成 UserMemoryItem，也未改 CandidateProfile。

两场均冻结 Main Prompt **2.2.0**，包含 5 个目标、9 个合法候选组合，其中 Kafka 有 2 个合法组合。A 并未删除 Kafka，后续才能真实观察模型是否合理降低重复考察优先级；不能把“题源被禁止”当成自主规划成功。B 提供真实规则算出的冲突与复测提示，不强制 FOLLOW_UP/NEXT。

已通过只读 SQL 核对：两场 state_version=0，started_at/active_question_id 为空，Question/Answer/Evaluation/模型调用记录均为 0；快照内容指纹与数据库保存值一致。等待授权不会消耗尚未启动的 Session 时间预算。

### 已保存的真实模型请求预览

预览复用当前 Structured Output 构造路径，在发送前捕获 messages/schema 后主动中止。包含实际 MemoryGuidedPlan、合法组合和上下文投影，不含 API Key 或隐藏推理。

- A：items 为空；Kafka 降优先级，memory_revision=2。
- B：items 包含 `java_backend:kafka_reliability`，state=CONFLICTED，priority=8，memory_revision=3，观察引用 evaluation:3/4/5；说明这是历史表现推断，不是本人确认事实。
- 尚无 Live 原始决策、生成题目、Controller 提交或新知识观察；对应字段明确为未执行，不用 Replay 填充 Live 结果。

原准备产物中的 snapshot_fingerprint 字段误取不存在的返回字段，值为 null；未覆盖原产物或重建历史。脚本现改为计算 digest(context)，并由独立只读核对产物保存两场真实 content_fingerprint 和 snapshot_verified=true。该诊断字段问题不影响数据库快照本身。

## 3. 真实模型验收方案：尚未执行

配置检查：当前六类 Agent 相关 routes 均为 `openai_compatible / deepseek-v4-pro-ga-260813`，未配置自动 fallback；未发请求验证凭证、余额或在线可用性。本轮真实逻辑请求、HTTP、Token 消耗均为 **0**。

建议授权边界：**最多 6 次逻辑请求、10 次 HTTP、60,000 Token、整批 480 秒、只运行一个批次**；最多一次整批受控 repair，仍计入全部上限。用户尚未授权执行。

每组预计 Main 开场 → 一答 Evaluation → 下一题 Main，共 3 次；不含 Coach，不宣称完整多轮或完整跨场次 Live。实际答题输入须响应模型当前题目，A 若合理选择 Redis，不应硬塞 Kafka 回答。Main 开场只能使用当时 Controller 允许的动作，后续动作不固定。

未来执行器必须：

1. 逐请求核对 route/model，记录实际输入/输出/推理 Token（若返回）、缓存信息、耗时、repair、合法组合与业务校验。
2. 对未知 usage 保守记账，不能当零；预留下一次请求的输出和修复预算，任何上限先到即停。
3. 按 Controller 验证候选组合、目标、题源与版本，并人工可核对题意、当前回答和记忆计划的联系。
4. 核对 Answer/Evaluation/Question、知识观察、配额账本和重复有效记录。
5. 首次计费异常、路由错误或业务校验失败即停，不换模型、不重跑筛选成功样例。

现有 `accept_deepseek_v21.py` 的旧预算并不等于本方案，**不能直接沿用其默认上限启动**。需在获授权后以明确的新批次计量执行。当前价格未配置，不编造人民币估价或保证账单为零。

结论边界只能是 **Replay 历史 + Live 决策**；完整多场 Live 训练与 Coach 报告仍未验收。

## 4. 备份与恢复验证

复用备份，不覆盖旧文件：

`E:\work\backups\interview-agent-platform\v211-20261023-144352-e617d0\interview_agent.dump`

- 大小 1,277,943 bytes。
- SHA256：`19931B3EBAD63C863AD0996F7BCFF73346BBAD4A63AD0C31F883DFB9D74C16AB`，与备份记录和容器内副本一致。
- `pg_restore --list` 成功，770 行。
- 使用 `pg_restore --exit-on-error --no-owner --no-privileges` 恢复到全新 `interview_agent_v21_v22_migration_review_20261023_a` 成功，未对正式库执行恢复。
- 恢复库在 0025 时的 64 表内容指纹与正式库一致。随后仅在恢复库升级 0026，形成 66 表。
- 除 alembic_version 外，63 个旧表在升级前后 counts/内容指纹完全一致；两张新能力表为空。

正式库 `interview_agent` 在审查前后仍为 **0025 / 64 表**，全部表的 counts/内容指纹无变化。记录包括 users=5、resumes=38、ProfileVersion=1、sessions=19、answers=45、evaluations=41。不将这些有限核验说成覆盖所有物理存储损坏的保证。

## 5. 0025 → 0026 迁移内容与风险

新增 `capability_memories` 与 `capability_memory_versions`；包含用户/主题唯一约束、能力/版本唯一约束、主键，以及 user_id 和 memory_id 索引。两条外键均 ON DELETE CASCADE，隔离升级后所有约束 convalidated=true。

不修改旧业务表字段，不回填旧观察，不自动批准提案，不改变旧 Session Snapshot。旧能力推断由运行时服务按有效观察构建，不靠 migration 改写历史。

实际 6 个索引包含 2 主键、2 唯一约束索引和 2 单列索引。单列索引与复合唯一索引左前缀有重叠，会带来少量额外存储/写入成本；本轮记录而不擅自修改 migration。

### 真实锁演练

仅在恢复隔离库执行 upgrade 的回滚事务并读取 pg_locks：

- 新表、序列和索引持有 AccessExclusive 等建表相关锁。
- **users 持有 ShareRowExclusiveLock**，外键创建会与用户表并发写入冲突；普通 SELECT 通常不被该锁阻塞。
- 本地小数据演练 DDL 墙钟时间 288.21ms，不是生产耗时保证。
- 演练设置 lock_timeout=3s、statement_timeout=15s，结束必回滚；随后隔离库才执行正常 upgrade。

正式迁移应单独授权，在短维护窗口协调用户写入，设置迁移连接自己的锁等待/语句上限，遇锁超时回滚退出。migration 自身没有这些内建上限，不能宣称自动有界或零锁表。

降级会删除两张能力表及其历史版本；正式运行积累能力数据后不能把 downgrade 当作无损回滚。应优先保留备份、停写评估后决定恢复方案。

## 6. 本轮实际测试与兼容性

以下为 2026-10-23 本次准备实际执行，不重复累计不同重跑的用例：

| 验证 | 结果 |
| --- | --- |
| 新隔离 PostgreSQL 关键回归：V2.2 rules/integration、V2.1.1 Director、V2.1 adaptive、M5 memory、M5 two-session | 63 passed / 0 failed / 0 skipped，32.60s |
| 空隔离库 0026 upgrade/downgrade | 1 passed，7.14s |
| 恢复升级后的纯能力规则检查（与前述有重叠，不加总） | 10 passed，0.86s |
| 前端测试 | 43 passed / 0 failed / 0 skipped |
| 准备和审查脚本语法、git diff whitespace | 通过 |

关键回归覆盖旧 MemoryProposal 审批、提示依赖/独立验证、冲突与时间规则、撤销不会聚合复活、用户隔离、Snapshot 冻结、幂等投影与 Director 合法组合。没有为了准备 Live 增加业务模型调用或放宽 Evidence。

此前 V2.2 全量 **510 passed** 及真实浏览器 Replay 结果见 51 文档，**不是本轮重新跑了 510 项或再次完成浏览器验收**。本轮不新增 UI，因此未重启浏览器流程；完整跨场次 Live 尚未执行。

## 7. 证据与重启后状态

ignored `.runtime/acceptance/` 保存下列只读审查/准备产物，含 DEMO 业务请求，不作为公开用户数据提交：

- `v22_formal_before_20261023_a.json`、`v22_formal_after_20261023_a.json`
- `v22_restore_before_20261023_a.json`、`v22_restore_after_20261023_a.json`
- `v22_migration_locks_20261023_a.json`
- `v22_live_prepare_20261023_a.json`、`v22_live_prepared_checks_20261023_a.json`

2026-10-24 重启后只读 docker inspect：原容器 `interview-agent-platform-postgres-1` 仍为原 ID（359a220e…），镜像 postgres:16-alpine，卷仍是 `interview-agent-platform_pgdata`；状态 exited。仅 SearXNG 正在运行。本轮收尾未启动 PostgreSQL、Redis、API 或 Web，因此不能宣称目前演示服务在线。下一次验收需先核实并启动原容器，再重新只读核对待验收 Session；不重新初始化数据。

未触碰 Docker VHDX、卷、密钥，未重新进行 Socket 排障；没有正式迁移、模型调用、commit 或 push。

## 8. 等待的授权

1. 上述有界 DeepSeek Live 批次的明确授权；执行前恢复原 PostgreSQL 并核对隔离 Session，不能使用旧 Session33。
2. 正式库 0025 → 0026 的独立迁移授权与维护窗口；执行前更新逻辑备份，正式库本轮保持不变。

两项互不替代。当前已具备准备材料与恢复演练证据，**尚不代表 Live 规划效果通过或 V2.2 已部署到正式库**。到此停止，不进入 V2.3。

### 后续事实（2026-10-24）

用户随后明确授权有界 Live 批次，实际执行结果见 [53 验收记录](53_v22_live_memory_acceptance.md)。原 PostgreSQL/Redis 已安全启动，Session 4/9 无需替换；正式库仍为 0025，未授权或执行正式迁移。上文保留准备阶段当时状态，不改写为已经执行。
