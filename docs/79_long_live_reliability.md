# 长场面试可靠性诊断与修复

本轮仅执行容器恢复、隔离库核对和离线回归。真实模型 HTTP 为 0。docs/76～78 的采集记录保持原样。正式库、模型配置、冻结 Prompt、Memory 聚合与迁移文件均保持原样。

## PostgreSQL 退出与恢复

| 上海时间 | 现场证据 |
| --- | --- |
| 21:54:34 | PostgreSQL 完成最近一次正常 checkpoint。 |
| 21:55:57 | Capture 记录第 5 笔发送前预留。AgentRun、StepRun、Answer 和两层预算预留已写入隔离库。 |
| 22:13:45 | Docker inspect 显示原 PostgreSQL 容器 `359a220e4ac1` 退出，退出码 255，`OOMKilled=false`，State.Error 为空。 |
| 22:13:47 | Docker Desktop backend 报告 Linux/WSL Engine 从 starting 转为 running。VM 日志也记录同一时段 containerd 启动。 |
| 22:26～22:27 | 核对容器及原卷 `interview-agent-platform_pgdata` 后执行一次 `docker start`。PostgreSQL 报告上次关闭不完整，自动 WAL redo 和恢复 checkpoint 完成，随后接受连接。 |

查询的 Docker 历史事件窗口未返回该容器的停止事件。已有日志证实数据库非正常关闭并与 Engine 重启时序接近，无法区分是宿主或 WSL 中断、用户操作还是资源异常触发了 Engine 重启。没有观察到 PostgreSQL 先行 PANIC、OOMKilled 或正常关闭日志。容器退出时间比 #5 预留晚近 18 分钟，不能用后者解释先前模型请求中断。原卷、容器身份及隔离库 0026 经实查仍在。

## 第 5 笔请求与重复检查

- Capture `requests/0005.json` 有完整请求和请求指纹，状态 `RESERVED`。该批的 `SEND` 日志在 HTTP 客户端发送前由 request hook 输出，不能证明 Provider 收到请求。没有收到状态码、响应正文、Provider request ID 或 usage，也没有可查证的 Provider 用量。
- 恢复后隔离库只读查询确认 `ModelAdmission` 原为已过期的 RESERVED。根据用户、路由、预留额和创建秒精确选中唯一记录，通过既有 `ModelGovernance.settle(key)` 结算未知用量。最终为 **UNKNOWN，charged_tokens=26,150，actual_tokens=NULL**。Capture 自身的 27,303 和会话 41,276 是不同保护边界的预估值。
- Session 2 仍为 IN_PROGRESS、版本 4、活动题 2。Answer 2、AgentRun 7 和 StepRun 7 均保持 RUNNING 现场，StepRun 无 usage。已过期的会话租约和 41,276 Token 预留仍在。没有调用会启动新评价的同键答题入口，也没有更改任何题目、回答或审计状态。
- 重启后共有 2 题、2 个 Answer、1 条正式 Evaluation。按会话与题序、会话与题目分别检查，未发现重复题、回答或正式评价。前 4 笔 SETTLED 用量合计 28,289 Token，与历史 Capture 一致。

## 技术选型分类修复

`topics.classify` 原先在所有技术规则之前运行 BACKGROUND 正则，裸 `为什么选择` 即触发动机类别，`validate_topic` 因已选目标为技术类而抛出 `INTERVIEW_BACKGROUND_LIMIT`。docs/78 的 RAG 原题已有合法 Skill、候选组合与题源，失败发生在题面分类这一步。

现在依据同一选择短句的宾语来判定。RRF、Redis 和排名融合复用既有技术词典的主题识别。公司、岗位和明确求职表达优先，句子别处的 Redis 或 RAG 不会使求职动机越过配额。无法识别选择对象时沿用保守的背景归类。候选组合、题源校验、三连冷却、前五题及全场 BACKGROUND 数量限制保持原有规则。历史问题已有的 `topic_policy` 继续用于计数。

原始 RAG 题、RRF 与 Redis 简短题在旧代码中均失败，修改后通过。求职动机、夹带技术词的求职动机及含糊选择仍被拒绝。判断仍受现有主题词典限制，生僻技术实体可能继续保守归类。改动位于 `apps/api/src/interview_api/modules/interview/topics.py`，回归位于 `apps/api/tests/test_interview_topic_policy.py`。

## 中断验证与恢复边界

使用 MockTransport 隔离 HTTP，配额事务实际落到全新 PostgreSQL 0026 测试库。新增故障注入位于 `apps/api/tests/test_v13_shared_governance.py`。

| 阶段 | 验证结果 |
| --- | --- |
| 确认尚未进入 HTTP 的协作取消 | 0 次 transport 进入，应用账本按明确未发送结算。突然进程退出没有该证据，过期预留仍按 UNKNOWN 处理。 |
| transport 进入后连接中断 | 只有一次调用，实际用量为空，保留保守配额。 |
| HTTP 200 已到响应 hook，正文用量未交给 Provider 解析 | 注入中断后保留 UNKNOWN，恢复时未重发。 |
| HTTP 200 已解析，随后数据库结算入口不可用 | 已有预留保持 RESERVED。模拟数据库恢复后走结算入口保守转 UNKNOWN，transport 仍只有一次。 |
| 已完成答题的同键重放 | 返回已保存事件，不新增有效 Answer、Evaluation 或 Question。 |
| 未完成回答的过期租约 | 旧版本结果会被围栏拒绝，剩余会话预留计为未报告用量。显式调用答题入口可继续该 Answer，但没有已持久化的成功步骤时可能再次发模型。 |

响应 hook 和结算入口用故障注入模拟中断，本轮未在测试中关闭共享 PostgreSQL，也未声称模拟了实际进程被强杀。原有过期预留测试、取消测试、旧结果围栏和业务幂等继续覆盖对应恢复分支。当前批次的安全处置是只读核对加账本未知结算，封存会话。生产生命周期持久化未改动。

### 请求生命周期最小改进方案

1. 为每个逻辑步骤和 HTTP 尝试绑定服务端稳定调用标识。覆盖 Session、Answer、角色、步骤和输入指纹，关联已有 Admission 与 StepRun。
2. 在传输前持久化发送意图。没有进入发送意图且能由记录证明未进入 transport 时才允许恢复发起。只存在发送意图时标记未知，禁止用相同业务请求自动重新发送。
3. 在业务审计或账本失败前保存经过脱敏的响应回执，记录 Provider request ID、响应指纹和已验证 usage。若要覆盖数据库掉线窗口，回执需要进程外的持久存储。可先复用验收 Capture 的本地原子记录设计，生产的多实例归属、访问控制和保留期限需要单独审批。
4. 恢复入口仅重放已有回执或执行幂等结算。没有回执或 Provider 证据的调用继续保守占额。发送意图与网络之间仍存在不可消除的窗口。验收工具继续在未知状态停批。

## 离线连续验收与历史 Capture

- Harness OFF 和 ON 各完成一场 20 轮固定业务 Replay。包括 8 个 FOLLOW_UP、目标切换、QUESTION_GAP_SCOPE_V1、自然结束、Coach 报告与下一场 MemoryGuidedPlan。ON 组使用工具反馈。每轮成功结果都检查同键重放。冷却和中途故障恢复由独立测试覆盖。
- 历史 HTTP 精确 Replay 覆盖 docs/76 的 39 条、docs/77 的 4 条和 docs/78 的 4 条，每条精确匹配两次且变更输入后拒绝。8 条保存的工具反馈也通过精确复放。docs/78 第 5 条没有响应可回放。测试前后逐文件 SHA256 一致。
- 固定业务 Replay 验证服务与数据状态，历史 HTTP Replay 验证采集与输入匹配。本轮没有新增 Live 样本。
- 核对命令使用 `docker inspect`、`docker events`、`docker logs`、一次 `docker start` 和 `pg_isready`。隔离库以只读 SQL 审计，只有第 5 笔走现有治理接口结算未知。新隔离测试库 `interview_agent_reliability_79_20261109` 执行 `alembic upgrade 0026` 后运行阻断外部网络的 pytest。
- 最终七文件回归 113 passed、0 failed、0 skipped、1 deselected。首次运行中，唯一失败的旧测试 `test_evaluation_reasoning_policy_is_route_only` 在当前 ModelScope 路由下要求专属 thinking 参数为 `disabled`，而现有 ModelScope 适配器有意返回 `None`。使用临时进程配置验证旧路由后，该项单独 1 passed。没有修改配置文件、生产路由或测试断言。
- 核对回执位于 `.runtime/acceptance/docs79_reliability/` 下的 `ledger_before.json`、`ledger_after.json` 和 `archive_replay.json`。

短程 Live 的题面误判已解除，数据库可用。若以后另行授权，单次人工受控验收可以沿用独立会话、预算与 Capture 停批规则。自动恢复式 Live 仍需先补齐发送状态与显式同键重试的持久化围栏。
