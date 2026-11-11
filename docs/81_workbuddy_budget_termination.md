# P1.2a：下一题预算不足时的合法终止

2026-11-11。WorkBuddy 第二轮。离线实施 + 隔离 PostgreSQL 验收；真实模型 HTTP **0**，无迁移，无 Provider/`.env` 变更，无历史验收库写入。

## 1. 原阻断与证据

docs/80 长场（批次 `p1_thinking_long_20261110`）走在 `interview_agent_thinking_long_20261110`：

| 现场事实 | 值 |
|---|---|
| 已提交题目 / 回答 | 18 / 18 |
| 正式 `InterviewEvaluation` | **17** |
| 第 18 答 Evaluation 的 AgentRun | 38，`SUCCEEDED`，用量已结算 |
| 会话状态 | `IN_PROGRESS`、`state_version=38`、Answer 18 `FAILED` |
| 会话已用 / 下一题预留 / 冻结上限 | 399,420 / 81,129 / 460,000 |

代码侧原因（`62333df`）：

1. `core_service.adaptive_answer` 在 Evaluation 成功后调用下一题 Main；`call_agent` 因 `SessionController.reserve_model_call` 拒绝而返回 `None`，随后**直接抛** `INTERVIEW_BUDGET_EXHAUSTED`。
2. `answer()` 的异常分支把该 Answer 标 `FAILED`，**Evaluation 从未进入 `commit_answer`**，只留在 AgentRun 审计里。
3. 即使绕过第 2 步，`commit_answer` 的终止判定是 `exhausted = choose_action(...) == "COMPLETE"`，而 `choose_action` 只在 `tokens >= max_model_tokens` 时返回 `COMPLETE`。此处 399,420 < 460,000，会被判为"尚未耗尽"，再以 `尚有合法考察目标且预算未耗尽` 拒绝一次。

即：**"预算不够再问一题"与"预算已经被用光"是两种不同的耗尽**，旧代码只认后者。

连带影响（同一根因，不是独立缺陷）：`InterviewReportService` 要求 `session.status == "COMPLETED"`，`CapabilityMemoryService.observations` 同样只读 `COMPLETED` 会话，因此**卡住 = 无 Coach 报告 = 无能力记忆刷新 = 下一场无 MemoryGuidedPlan**。

## 2. 修复

只改四处，全部在既有事务与既有准入机制之内。

### `interview/core_service.py`

下一题 Main 返回 `None`（唯一来源：`with_model_reservation` 未取到会话预留，且**未发出任何 HTTP**）时，不再抛错，而是带着已有 Evaluation 走 `action="COMPLETE"`，并传入内部标记 `terminal_detail="NEXT_QUESTION_RESERVATION"`。

- 不创建下一题；`main` 仍为 `None`，`policy` 为空。
- Evaluation 对象就是内存中那个**已通过 `validate_artifact` / `resolve_observations` 的复用结果**，恢复时不会重复发送模型请求。
- 其它失败（Evaluation 校验失败、Provider 异常、HTTP 错误、结算失败、数据库错误）不经过该分支，仍然向上抛。

### `interview/controller.py`

1. `commit_answer(..., *, terminal_detail=None)`，键值参数，**没有 HTTP 入口**。
2. 守卫：`terminal_detail` 只允许 `requested_action == "COMPLETE"` 且 `main is None`；否则 `INVALID_TERMINAL_REQUEST`。非自适应会话传该参数同样直接拒绝。
3. `terminal` 增加 `terminal_detail is not None` 一项；`completion_reason` 仍沿用既有取值 `BUDGET_LIMIT`（不新增枚举、不改 Schema、不加迁移），另加 `completion_detail` 记录子原因。
4. `adaptive_decisions` 轨迹的 `verdict` 新增 `TERMINATED_BUDGET`，与 `NOT_EXECUTED_BUDGET` 区分。

### `interview/main_harness.py`

Harness ON 时同一拒绝发生在 `_loop` 内。改为抛出模块私有 `_AdmissionRefused`，在 `call()` 中把 run 落为 `FAILED`（`error=INTERVIEW_BUDGET_EXHAUSTED`、`terminal=False`）后**返回 `None`**，与 Harness OFF 的语义完全一致。这样 OFF/ON 共用同一条终止路径，不再出现"OFF 能结束、ON 卡住"。

### `interview_report/service.py`

Coach 输入新增有界事实块 `session_completion`：`completion_reason`、`completion_detail`、`question_budget`、`answered_count`、`evaluated_count`、`early_terminated`、`semantics=CONTROLLER_TERMINATION_FACT_NOT_PERFORMANCE_EVIDENCE`。

- 只在 `completion_reason` 存在时注入，非自适应会话不出现该字段。
- `early_terminated` 由 `evaluated_count < question_budget` 判定，不靠原因字符串猜测；"按计划问满 20 题"不会被误标为提前结束。
- 统计口径不变：仍来自 `interview_records` / `deterministic_statistics` 的持久化评价，`session_completion` 只是终止事实，不能作为表现证据。

## 3. 顺带修掉的潜伏缺陷（同一行代码）

原代码在 `complete()` 之后执行 `summary["completion_reason"] = ...`，再把 `summary` 深拷贝进 `session.completion_json`。但 `complete()` 存入 `completion_json` 的正是**同一个 dict 对象**，就地修改后 ORM 认为属性"未变化"，**自适应会话的 `completion_reason` 从未真正写进数据库**（只在 SSE `complete` 事件里出现过）。

现在改为构造新 dict 再赋值，`completion_reason` 真实落库；`test_planned_question_count_completion_still_persists_its_reason` 用"计划题数用满"的旧路径做回归。历史会话的 `completion_json` 保持原样，不回填、不改写。

## 4. 明确不做的事

- 不把 Evaluation 校验失败、Provider 异常、余额不足、发送状态 UNKNOWN、数据库错误转成"正常完成"：这些都不经过 `terminal_detail` 分支。
- 不因预算不足伪造下一题；不跳过 `validate_decision`、题源、gap 作用域、`state_version`、幂等与结算。
- 客户端无法请求提前结束：`SendMessageRequest` 无该字段（`Completion` 相关参数不在模型字段内），传入同名 JSON 键会被忽略且不进入 `commit_answer`。
- 普通（非自适应）`answer()` 的同根因死路不在本轮范围，见第 9 节。

## 5. 测试

隔离库 **`interview_agent_wb_p12a_20261111`**（新建，Alembic `0026_capability_memory`，66 张表）。历史验收库（含 `interview_agent_thinking_long_20261110`）未写入、未迁移。无真实模型调用：模型边界使用本地确定性 provider，PostgreSQL 与 Controller 真实执行。

新增 `apps/api/tests/test_budget_termination.py`（8 项）：

| 用例 | 断言要点 |
|---|---|
| `test_valid_evaluation_commits_and_session_ends_on_next_question_budget[False/True]` | 评价落库 1 条；会话 `COMPLETED`；`completion_reason=BUDGET_LIMIT`、`completion_detail=NEXT_QUESTION_RESERVATION`；只有 1 题且为 `COMPLETED`；无第 2 题；预留表为空、`unreported_model_tokens=0`；事件为 `evaluation→complete→done`；同键重试返回同一 events 且不再发模型、不重复写入 |
| `test_invalid_evaluation_cannot_be_turned_into_a_budget_completion` | 引用不在回答中 → `UNSUPPORTED_ANSWER_QUOTE`；会话仍 `IN_PROGRESS`、`completion_json=None`、0 条正式评价 |
| `test_affordable_budget_still_generates_the_next_question` | 预算充足时正常产生第 2 题（FOLLOW_UP），会话继续 |
| `test_controller_rejects_a_budget_termination_that_keeps_a_next_question` | 传 `terminal_detail` 但保留下一题 → `INVALID_TERMINAL_REQUEST`，无写入 |
| `test_client_payload_has_no_way_to_request_early_termination` | 请求模型无该字段，传入被忽略 |
| `test_planned_question_count_completion_still_persists_its_reason` | 旧"题数用满"路径仍为 `BUDGET_LIMIT`、无 `completion_detail`，且原因真实落库 |
| `test_coach_receives_the_committed_evaluation_and_the_termination_reason` | 报告 `COMPLETED`；Coach 收到真实 `evaluation:{id}` 与 `session_completion`（含 `early_terminated=true`）；能力观察仅 1 条且指向该评价；下一场 `MemoryGuidedPlan` 的每条 `observation_refs` 均等于该评价 |

回归（同库）：`test_main_harness`、`test_gap_scope_contract`（含 Harness OFF/ON 各 20 轮 Replay + Coach + 下一场记忆）、`test_v21_adaptive`、`test_m4_interview`、`test_m5_report`、`test_m5_two_session_e2e`、`test_v211_director_contract`、`test_v13_shared_governance`、`test_v23_training`、`test_interview_topic_policy`、`test_budget_termination`。

运行结果见第 6 节；`git diff --check` 通过。

## 6. 本轮结果

- 专项 `test_budget_termination.py`：**8 passed、0 failed、0 skipped**。
- 十一文件完整回归（含专项）：**198 passed、0 failed、0 skipped**（169.85 s）。
- 其中十文件回归（不含专项）：**190 passed**（168.82 s），说明 `test_main_harness`、Harness OFF/ON 各 20 轮 Replay、Coach 与下一场 MemoryGuidedPlan 均未回归。
- `git diff --check` 通过。隔离库 `interview_agent_wb_p12a_20261111` 在 Alembic `0026` 上未发生迁移。

## 7. 限制（未验证项）

1. **未重跑 Live**。第 18 答所在会话属封存现场，未回写、未补提交；本轮只在新建隔离库证明"同类状态会合法结束"。真实模型的 Token 曲线、预留字节数、Coach 真实输出质量均未重新测量。
2. **单场预算默认值仍偏小**。`interview_max_model_tokens` 默认 200,000，而实测 ≈22k/答、docs/70 中性估算 20 答含 Coach 251,982。本轮只改终止语义，未改默认值——这是部署决策。
3. **非自适应模式仍有同根因死路**（`core_service.answer` 的"下一题预留不足"）。本轮按要求不扩大范围。
4. `completion_detail` 目前只有一个取值 `NEXT_QUESTION_RESERVATION`；前端尚未展示它。
5. 题面主题仍为词典正则；与本轮无关。

## 8. 文件

- `apps/api/src/interview_api/modules/interview/core_service.py`：终止分支 + 内部标记
- `apps/api/src/interview_api/modules/interview/controller.py`：`terminal_detail` 守卫、`completion_reason`/`completion_detail` 落库、轨迹 verdict
- `apps/api/src/interview_api/modules/interview/main_harness.py`：准入拒绝统一返回 `None`
- `apps/api/src/interview_api/modules/interview_report/service.py`：`session_completion` 事实块
- `apps/api/tests/test_budget_termination.py`：新增专项测试
- 本文件

无数据库迁移、无 API 契约变更（仅 `completion_json` 内新增一个可选键）、无 Provider 配置变更。

## 9. 建议的下一轮

1. **Live 短程复验**（需另行授权与额度）：在新建隔离库跑一场"明显会被预算截断"的短场，确认真实 Provider 下同样以 `BUDGET_LIMIT / NEXT_QUESTION_RESERVATION` 收尾，并跑通 Coach 与下一场 MemoryGuidedPlan。仅此一项即可补上本轮唯一的 Live 缺口。
2. 普通模式同根因修复（`core_service.answer`），与自适应保持一致的终止语义。
3. 预算默认值与题量的匹配（配置/部署决策），避免生产 20 题默认配置在第 9～11 答就撞墙。

## 10. 追加：第三轮的预算语义区分与真实短程验收

`reserve_model_call` 的 `None` 实际只来自两类上限（会话时长、Token）。第三轮补上了拒绝原因标记
`budget_exhausted_reason`（`DURATION` / `TOKENS`），并据此区分终态：

| 原因 | `completion_reason` | `completion_detail` |
|---|---|---|
| Token 不足以准入下一题 | `BUDGET_LIMIT` | `NEXT_QUESTION_RESERVATION` |
| 会话时长到期 | `DURATION_LIMIT` | `NEXT_QUESTION_DURATION` |

读不到原因时不再终止，继续抛 `INTERVIEW_BUDGET_EXHAUSTED`。真实模型短程验收（5 HTTP / 41,633 Token）确认了
`TOKENS` 分支的完整闭环，见 [P1.2a 真实模型短程验收](82_workbuddy_budget_live_acceptance.md)。
