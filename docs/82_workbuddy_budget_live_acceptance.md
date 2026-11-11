# P1.2a 真实模型短程验收 + 两个预算语义检查

2026-11-11。WorkBuddy 第三轮。批次 `wb_budget_live_20261111`，隔离库 `interview_agent_wb_budget_live_20261111`（Alembic `0026`）。真实 ModelScope `deepseek-ai/DeepSeek-V4.1-Flash-1017`，**5 次 HTTP / 41,633 Token**。正式库、`interview_agent_thinking_long_20261110` 与 `.runtime/acceptance/**` 历史批次均未写入。

## 1. 结论摘要

**已验证**：真实 Main 生成合法问题 → 真实 Evaluation 评分并通过业务校验 → 下一题 Main 因会话 Token 准入被拒**且未发送任何 HTTP** → 该 Evaluation 正式提交 → Session `COMPLETED`，`completion_reason=BUDGET_LIMIT`、`completion_detail=NEXT_QUESTION_RESERVATION` 持久化 → 真实 Coach 完成报告 → 下一场 `MemoryGuidedPlan` 只引用本场两条正式 Evaluation。

**未验证**：20 轮完整 Live 长面试、自然结束路径、Harness ON 的 Live 工具闭环、时长拒绝在真实会话中的触发（本批未出现）。

## 2. 检查一：`reserve_model_call` 返回 `None` 的原因

代码只在一处返回 `None`（`controller.py::reserve_model_call`），条件恰好三类，且都属于会话资源上限：

| 条件 | 原标记 | 现标记 |
|---|---|---|
| `remaining_time <= 0`（会话时长到期） | `budget_exhausted=True` | `budget_exhausted=True` + `budget_exhausted_reason="DURATION"` |
| `used >= max_model_tokens` | 同上 | `...reason="TOKENS"` |
| `used + reservation > max_model_tokens` | 同上 | `...reason="TOKENS"` |

**其他资源准入不在 `None` 里**：用户级日额度、并发、身份归属由 `ModelGovernance.reserve` 在真实 HTTP 前抛 `AppError`（429/403），不会被解释为 None，也不会进入终止分支。

**原实现的问题**：两种上限共用同一个标记，上层只能一律写 `BUDGET_LIMIT`，时长到期会被说成 Token 不足。

**修复**：记录 `budget_exhausted_reason`；`adaptive_answer` 读回后映射为
`TOKENS → (BUDGET_LIMIT, NEXT_QUESTION_RESERVATION)`、`DURATION → (DURATION_LIMIT, NEXT_QUESTION_DURATION)`；**读不到原因时不再终止**，继续抛 `INTERVIEW_BUDGET_EXHAUSTED` 并保留可重试现场。成功准入会清除旧的 reason 标记。`commit_answer` 要求阶段与原因成对出现，原因必须在 `TERMINAL_REASONS` 内，且仍只允许 `COMPLETE` + 无下一题；无 HTTP 入口可传。

本批真实数据佐证分类正确：第 2 答终止时 `budget_exhausted_reason=TOKENS`，而不是笼统写"预算"或错写时长。

## 3. 检查二：`main_harness.py` 返回 `None` 的路径

`MainHarness.call` 只有一处返回 `None`：`_loop` 发出准入请求时 `with_model_reservation` 返回 None → 抛模块私有 `_AdmissionRefused` → `call()` 把 run 落为 `FAILED`（`error=INTERVIEW_BUDGET_EXHAUSTED`，`terminal=false`）后返回 None。

其余路径均**不会**被吞：模型超时/Provider 错误、`StructuredOutputError`（Schema 或修复失败）、`UNKNOWN_DIRECTOR_CANDIDATE`、题面/题源/聚焦校验失败、Controller 冲突都经 `except BaseException` 记为 FAILED 并重新抛出；工具自身的错误按设计转换成有界 `tool_feedback`，属于正常循环而不是终止。`FinalSelectionRepair` 里的 4 个 `return None` 是"不生成修复计划"，与 `call()` 返回值无关。

**结论：原实现已满足要求，未为此改动生产代码**，只补了 Harness ON 的拒绝路径测试（非法 FINAL、非法工具动作）。

## 4. 代码改动（本轮两个提交）

| 文件 | 改动 |
|---|---|
| `apps/api/src/interview_api/modules/interview/controller.py` | `budget_exhausted_reason` 记录/清除；`TERMINAL_REASONS`；`commit_answer(terminal_detail, terminal_reason)` 成对校验；`completion_reason` 取传入原因 |
| `apps/api/src/interview_api/modules/interview/core_service.py` | 读回拒绝原因并映射；未识别原因继续抛错，不终止 |
| `apps/api/tests/test_budget_termination.py` | +4 项：Token/时长拒绝分类、成功准入清除标记、未识别拒绝不终止、Harness ON 拒绝不终止、时长终态落库 |

`main_harness.py`、`interview_report/service.py` 本轮未改（延续 `bd2e7b2`）。

## 5. 离线测试

隔离库 `interview_agent_wb_p12a_20261111`（Alembic `0026`）。

- 专项 `tests/test_budget_termination.py`：**12 passed、0 failed、0 skipped**
- 十一文件完整回归（含专项）：**202 passed、0 failed、0 skipped**（152.68 s）
- 含 `test_main_harness`、`test_gap_scope_contract`（Harness OFF/ON 各 20 轮 Replay + Coach + 下一场记忆）、`test_m4_interview`（既有 pre-call 预算停止）、`test_m5_*`、`test_v23_training` 等

## 6. Live 设计

- 合成候选人 + 本地合成 JD（5 条要求），**不读取正式库任何数据**；画像生成使用 Replay，不发 HTTP。
- 会话 `question_count=6`，冻结 `max_model_tokens=58,860`。该值 = 开场 Main 预留估算 **28,860**（wire 12,616 + system 4,377 + schema 1,771 + 两次输出 10,096）+ 余量 30,000。估算方式与 `call_agent` 的预留公式逐项一致，未发请求。
- 目标：前两次 Evaluation 正常准入，第 2 答后的下一题 Main 因 `used + reservation > 58,860` 被拒。
- 隔离实例开关：仅本批的 Main/Evaluation 关闭 thinking（`thinking_type="disabled"`），输出上限保持生产默认 3,000；Coach 保持默认。正式 Provider 配置、`.env`、模型路由未改。
- 作答文本为合成内容（逐题现场撰写），不是真人面试记录。

## 7. 逐请求

| # | 阶段 | 路由 | HTTP / finish | 输入 | 输出 | 总 Token | 延迟 |
|---:|---|---|---|---:|---:|---:|---:|
| 1 | 开场 | interview_main | 200 / stop | 5,792 | 229 | 6,021 | 2,705 ms |
| 2 | 第 1 答评价 | answer_evaluation | 200 / stop | 2,971 | 1,158 | 4,129 | 9,258 ms |
| 3 | 第 2 题 | interview_main | 200 / stop | 7,970 | 248 | 8,218 | 3,083 ms |
| 4 | 第 2 答评价 | answer_evaluation | 200 / stop | 3,629 | 919 | 4,548 | 7,645 ms |
| 5 | 终局 Coach | interview_coach | 200 / stop | 13,651 | 5,066 | 18,717 | 36,256 ms |
| | **合计** | | | **34,013** | **7,620** | **41,633** | **58,947 ms** |

- 0 Repair、0 重试、0 回退；5 条均为 HTTP 200 / `finish_reason=stop`。
- **第 4 条之后没有第 3 题的请求**：`with_model_reservation` 在发出前拒绝，未产生 HTTP。
- HTTP 时间窗：批次起点 +4 s 至 +224 s（约 3 分 44 秒）。之后的人工步骤（核对、回放）不再发请求。
- 上限使用：HTTP 5/12、Token 41,633/150,000、HTTP 时间窗远小于 20 分钟。

## 8. 终态与持久化（隔离库只读核对）

| 项 | 值 |
|---|---|
| Session | id=2，`COMPLETED`，`state_version=5` |
| 题目 / 回答 | 2 / 2，全部 `COMPLETED` |
| 正式 `InterviewEvaluation` | 2 条（id 1、2；五维 4/4/4/5/4 与 5/5/4/5/5） |
| `completion_reason` | `BUDGET_LIMIT` |
| `completion_detail` | `NEXT_QUESTION_RESERVATION` |
| `budget_exhausted_reason` | `TOKENS` |
| answered / question_budget | 2 / 6 |
| 已用 Token | 22,916（6,021+4,129+8,218+4,548） |
| 剩余 | 35,944；下一题所需预留超过该值，故拒绝 |
| 会话预留表 | `{}`，`unreported_model_tokens` 未增长 |
| 未创建第 3 题 | 是（题库仍为 2 条） |

`completion_json` 写入数据库而不是只在事件流里出现，是本轮顺带修掉的 `bd2e7b2` 潜伏缺陷的直接验证。

## 9. Coach 与下一场记忆

- 报告：id=1，`generation_status=COMPLETED`，`producer=SUMMARY_COACH_AGENT`，`mode=live`，统计 `evaluated_count=2`。
- Coach 输入确实包含终止事实（从捕获的请求体读取）：

```json
{"completion_reason":"BUDGET_LIMIT","completion_detail":"NEXT_QUESTION_RESERVATION",
 "question_budget":6,"answered_count":2,"evaluated_count":2,"early_terminated":true,
 "semantics":"CONTROLLER_TERMINATION_FACT_NOT_PERFORMANCE_EVIDENCE"}
```

- 下一场（session 3，`READY`）冻结的 `MemoryGuidedPlan`：1 个条目 `ai_agent_application:agent`，`PRACTICING`，`observation_refs=["evaluation:1","evaluation:2"]`，`fingerprint=ff169d72…07701`。两个引用都指向本场正式评价，没有编造引用。
- 能力观察聚合：仅 2 条，`evaluation_id` 分别为 1、2。

## 10. Capture、账本与回放

- 目录 `.runtime/acceptance/wb_budget_live_20261111/`：`requests/0001–0005.json`、`captures.jsonl`、`index.json`、`manifest.json`、`outcomes/`、`transcript/`、`session.json`、`plan.json`、`memory.json`、`ledger.json`、`replay_check.json`、`target_state.json`、`batch_state.json`。
- 账号账本：5 条 `ModelAdmission` 全部 `SETTLED`，`charged_tokens` 与 `actual_tokens` 合计均 **41,633**，与捕获用量逐条一致；无 UNKNOWN、无未结算、无残留预留。
- 回放：5 条响应各按完整请求指纹精确命中 2 次，修改输入后拒绝；`replay_check.json` 记录 `all_exact=true`。
- 未保存密钥、Cookie、认证 Header；每条消息在落盘前做密钥替换与隐藏推理剥除。

**批次操作记录（透明说明）**：第一次达成终态时，采集器把 `TARGET_STATE_REACHED` 写成了批次 `stop`，导致后续 Coach 被拦。该 stop 是**本地书签**、当时无在途请求，已改为 `target_state.json` 标记并清空 `stop`，此后 Coach/记忆/回放才执行；`batch_state.json` 中保留了这次清理的说明字段。HTTP 请求数未因此增加（Coach 本来就属于本批 12 次上限之内）。

## 11. 尚未解决 / 未验证

1. **20 轮完整 Live 未跑**。本批只到 2 答。长场的自然结束、Coach 在长场的表现、跨场记忆质量仍未验证；不得据本批宣称长面试闭环。
2. **时长拒绝未在真实会话中触发**。`DURATION` 分支只有单测；真实会话里 `choose_action` 会先在 `elapsed >= max_duration` 时返回 COMPLETE，因此 `reserve_model_call` 的时长分支几乎不可达（两者用同一阈值）。
3. **`completion_reason=BUDGET_LIMIT` 仍是粗标签**：`choose_action` 把"题数用满""轮次用满""时长到期""Token 用尽"都归为 COMPLETE，`exhausted` 路径统一写 `BUDGET_LIMIT`。本轮只区分了"下一题准入被拒"这一条新路径，未改 `choose_action` 的既有语义（会牵动 20 轮 Replay 断言）。
4. 生产默认 `interview_max_model_tokens=200,000` 低于实测需求（≈11k/答 + 每答预留），20 题配置仍会在接近中途撞墙；这是部署决策，未改代码默认值。
5. 非自适应 `core_service.answer` 的同根因死路仍在。
6. 本批关掉了 Main/Evaluation 的 thinking（隔离实例），结论不能直接外推到生产默认推理模式；Coach 保持了默认 thinking（18,717 Token，其中推理 3,810）。

## 12. 下一步建议

1. 若要 20 轮 Live：单场预算至少覆盖"20 × 每答实测用量 + 末段峰值预留"（本批每答 ≈11k，末次预留 >35,944），建议 ≥ 500,000，并单批 45–60 次 HTTP 上限；仍须单独授权。
2. 若要更细的结束原因：把 `choose_action` 的 COMPLETE 拆成 `QUESTION_BUDGET / ROUND_BUDGET / DURATION_LIMIT / TOKEN_BUDGET` 四个来源，再更新 20 轮 Replay 断言。
3. 非自适应同根因修复，与自适应语义对齐。
