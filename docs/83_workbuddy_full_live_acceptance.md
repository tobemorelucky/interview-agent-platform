# 83 · WorkBuddy 第四轮：终止原因分类 + 一次真实长场 Live（18/20）

- 日期：2026-11-11
- 分支/基线：`master`，起点 `7e928d2`
- 本轮提交：`307d273`（终止原因分类）、`5a24f75`（Main 结构修复）、本文件（Live 报告）
- 隔离库：`interview_agent_wb_full_live_20261111`（新建，Alembic `0026`，66 表）
- 批次目录：`.runtime/acceptance/wb_full_live_20261111/`（只读封存）

**结论先说：本轮没有跑满 20 题。** 18 题、18 条正式 Evaluation 全部提交，会话因会话 Token 预算耗尽而**合法正常结束**，Coach 发生了真实调用但被一条误判规则拒绝，下一场 MemoryGuidedPlan 正常冻结并只引用本场正式评价。不允许用离线结果补足未完成的环节，因此第 19、20 题与 Coach 报告记为**未完成**。

---

## 1. 第一阶段：终止原因分类（`307d273`）

改前 `choose_action` 把四种结束压成同一个 `COMPLETE`，`commit_answer` 再统一写 `BUDGET_LIMIT`，于是"按计划答满题数"也被记成预算提前结束。

改后只有一个判定函数 `SessionController.stop_reason`，按固定顺序返回：

| 条件 | `completion_reason` | 是否提前结束 |
|---|---|---|
| `len(questions) >= question_count` | `QUESTION_COUNT_REACHED` | 否（正常完成） |
| `turn_count >= max_rounds` | `ROUND_LIMIT` | 是 |
| `used >= max_model_tokens` | `TOKEN_LIMIT` | 是 |
| `elapsed >= max_duration_seconds` | `DURATION_LIMIT` | 是 |
| 全部目标已达成 | `BLUEPRINT_COVERED` | 否 |
| 无合法目标/题源 | `NO_LEGAL_TARGET_WITHIN_POLICY` | 是 |

- 题数优先于一切预算：答满计划题数永远不会被标成预算结束。
- `BUDGET_LIMIT` 仅作为旧行的历史值继续被接受，**不回写任何旧会话**；`completion_reason` 存在 `completion_json`（JSON 列），无枚举、无迁移、无 API 契约变更。
- 下一题准入被拒（未发 HTTP）仍是 `TOKEN_LIMIT / NEXT_QUESTION_RESERVATION` 或 `DURATION_LIMIT / NEXT_QUESTION_DURATION`。
- Coach 的 `session_completion.early_terminated` 一直由"已评价数 < 计划题数"推导，不依赖原因字符串，因此答满 20 题的场次不会被误标。

测试：新增 `tests/test_completion_reason.py` 9 passed（顺序、四种原因、轮次上限集成、Coach 全额完成 vs 提前、旧 `BUDGET_LIMIT` 透传）；12 文件离线回归 211 passed。

## 2. 第二阶段：Live 中发现的修复（`5a24f75`，Live 中被发现）

Live 到第 2 题时出现 `structured_output_failed`。离线复现（真实响应已归档）：

- 模型把 `proposed_question` 输出成字符串，另附 `proposed_question_meta`（或 `proposed_question_{dimension,difficulty,expected_points}`）；
- 服务端让模型做一次结构性修复，模型给出的嵌套结构**完全正确**；
- 但通用事实校验按"字符串 + 路径"建索引，内容一致、路径变了，于是被判成"Repair altered factual strings"，修复永远不可能被接受 —— 授权修复与实际校验互相矛盾。

修复：`director_repair.FlattenedQuestionRepair` 只授权这一种归一化，并要求修复后的 `proposed_question` 必须等于**用原始字符串拼出的草稿**、其余字段一字不动；`MainSelectionRepair` 把它与原 `SupportingRefsRepair` 组合，引用子集修复保持原样。契约、Prompt 版本、事实校验本身都没改。

测试：`tests/test_flattened_question_repair.py` 7 passed（真实失败/修复对已存为 `tests/fixtures/flattened_main_selection.json`），13 文件回归 218 passed。

Live 中的出现率：Main 26 次调用里 3 次扁平化。其中 2 次可修复（第 2、5 题），1 次（第 17 题）连维度/要点都没有，修复只能凭空编造，被正确拒绝，最终靠重采样通过。

## 3. Live 批次

| 项 | 值 |
|---|---|
| 模型/端点 | ModelScope `deepseek-ai/DeepSeek-V4.1-Flash-1017`，`/v1/chat/completions` |
| Harness | OFF（prompt `2.3.3`） |
| Main | `thinking=disabled`，输出上限 3,000（生产值） |
| Evaluation | `thinking=enabled`，输出上限 12,000（验收实例） |
| Coach | `thinking=enabled`，输出上限 12,000（验收实例） |
| 会话预算 | 650,000（仅本场） |
| 计划/实际题数 | 20 / **18** |
| 真实 HTTP | **47**（上限 70） |
| 真实 Token | **588,586**（上限 850,000） |
| HTTP 时间窗 | 约 45 分钟（上限 90 分钟） |
| 非 200 | 0 |
| 结构化修复 HTTP | 4（全部 `interview_main`） |

`thinking` 参数不是靠配置推定，而是从**实际请求体**核对：26 次 Main 全部 `{"type":"disabled"}`，20 次 Evaluation 与 1 次 Coach 全部 `{"type":"enabled"}`。

### 3.1 每 5 轮

| 段 | 题号 | 新增 HTTP | 段内 Token | 累计 HTTP | 累计 Token | 正式评价 | 会话状态 |
|---|---|---:|---:|---:|---:|---:|---|
| R1 | 1–5 | 19 | 161,294 | 19 | 161,294 | 5 | IN_PROGRESS |
| R2 | 6–10 | 10 | 118,512 | 29 | 279,806 | 10 | IN_PROGRESS |
| R3 | 11–15 | 10 | 141,307 | 39 | 421,113 | 15 | IN_PROGRESS |
| R4 | 16–18 | 7 | 99,225 | 46 | 520,338 | 18 | **COMPLETED** |
| — | Coach | 1 | 68,248 | 47 | 588,586 | — | 报告 FAILED |

R1 偏高是因为前 5 题里夹着 2 次截断、3 次结构失败与 1 次重采样。会话侧记账（含保守计入的未上报额度）为 569,860。

### 3.2 终态

- `interview_sessions.id=2`：`COMPLETED`、`state_version=44`、`total_questions=18`、`answered_count=18`、`evaluated_count=18`、`overall_average=4.0667`、`mode=live`。
- `completion_reason=TOKEN_LIMIT`、`completion_detail=NEXT_QUESTION_RESERVATION`、`budget_exhausted_reason=TOKENS`、`model_call_reservations={}`。
- **第 19 题没有任何 HTTP**：下一题预留超出剩余预算，在发出前被拒绝，已通过校验的第 18 条评价照常提交（与 docs/82 同一条代码路径）。
- 18 条 Evaluation 的 `agent_run_id`、`provider=deepseek-*`、`prompt_version=2.3.3` 全部可查；分数分布：技术 2×3 / 3×1 / 4×9 / 5×5，维度均值 3.89 / 3.67 / 3.50 / 4.83 / 4.44，总体 4.07。作答脚本按实际提问逐轮撰写（含明确错误回答、明确"不会"、被追问后纠正），因此分数有真实梯度而不是满分。
- 题型：`NEXT` 6、`FOLLOW_UP` 12。

## 4. Coach 与记忆

**Coach 未生成报告。** 第 47 条请求是真实调用（68,248 Token，`thinking=enabled`，`finish=stop`），响应内容被 `COACH_COMPUTED_NUMBER` 拒绝：

```
... 统一采用“至少一次投递 + 消费端幂等”的口径 ...
```

命中规则的是 `至少一次`（中文数字 + "次"），这是一句技术惯用语，不是统计数字。随后两次重试都没能拿到新样本：一次被批次采集器的重复请求围栏拦下（记为 `CAPTURE_GUARD`），一次被 `ModelGovernance` 以 `MODEL_USER_QUOTA` 在**发出前**拒绝。第 1 次尝试则是 `COACH_INPUT_BUDGET`（见 §5.2）。因此 `final_interview_reports.id=1` 为 `FAILED`，本场没有报告。

**记忆正常。** 下一场 `session 3` 的 `MemoryGuidedPlan` 冻结了 3 条（`ai_agent_application:agent`、`:python`、检索），共 9 个 `observation_refs`：`evaluation:10`–`evaluation:18`，全部解析到本场正式评价（`.runtime/.../memory_check.json`：`refs_resolve_to_this_session=true`）。`memory.json` 里的 `refs_match` 字段用的是嵌套 `all()`，会拿每个 ref 和每条评价都比较，恒为 false，那个字段不可信，以 `memory_check.json` 为准。

## 5. 本轮暴露的产品问题（按证据）

### 5.1 P0 · 思考模式与结构化输出的输出上限不匹配

| 请求 | 上限 | completion | reasoning | 结果 |
|---|---:|---:|---:|---|
| 2（Evaluation） | 3,000 | 3,000 | 3,000 | `finish=length`，无内容 |
| 9（Evaluation） | 8,000 | 8,000 | 8,000 | `finish=length`，无内容 |
| 10（Evaluation） | 12,000 | 11,879 | ~8,300 | 正常 |

生产默认 `interview_model_max_output_tokens=3000` 与 `answer_evaluation_thinking` 组合时，**Evaluation 必然截断**；`router.py` 目前在 ModelScope 分支丢弃 `thinking_type`，等于把这个问题掩盖成"配置不生效"。这是两条问题叠加：传输层不落地（P1-2 旧账）+ 落地后上限不足（新增）。

### 5.2 P0 · Coach 长场直接不可生成

`interview_coach_max_input_bytes=200000`，18 条评价的证据信封超过该值 → `COACH_INPUT_BUDGET`，且错误信息明确"未静默删除证据"。也就是说**生产配置下，长场根本进不了 Coach**。本轮只在验收进程内把上限提到 1,000,000，生产值未动。

### 5.3 P0 · 数字围栏误杀技术惯用语

`resolver._qualitative` 用正则拦"分数/次数"，中文数字分支会把 `至少一次`、`一次投递` 当成统计断言。Coach 因此 100% 无法通过（正文里几乎必然出现"一次"）。本轮**没有为通过验收而放宽这条规则**；建议改为：中文数字只匹配 `分 / 道题 / 场面试 / 轮回答`，`次` 只匹配阿拉伯数字，或把校验改成"数字必须能在 statistics 中找到同源"。

### 5.4 P1 · 结构性输出失败不可恢复

无法修复的第三种形态（`proposed_question` 是字符串且没有任何草稿字段）会让整题卡死：同一幂等键重试、同输入重采样都不保证通过。建议在 `structured_output_failed` 且无授权修复时，由服务端对同一 payload 做**一次**新采样，而不是直接把 Answer 标 FAILED。

### 5.5 P1 · 采集围栏会在准入后中断，留下 UNKNOWN 计费

请求被采集器拒绝时，`ModelAdmission` 已经创建，产品按整笔预留保守计费并记 `UNKNOWN`：本轮两笔共 **291,808 Token**（Main 44,655 / Coach 247,153），其中 Coach 那笔是额度拒绝路径。账面 `known=charged=588,586` 一致，但账户实际消耗远高于真实 Token。这正是 docs/79 说的"发送意图与结算缺跨进程围栏"的现场证据。

### 5.6 P1 · 650,000 不足 20 题

实测每答平均 31.6k（Evaluation 思考 ~7–13k + Main 提示词随题数增长到 ~20k）。20 题需要 ≥700k；若保持 650k，`question_count` 应降到 18 以内。

### 5.7 未完成项（不得用离线补足）

- 第 19、20 题：未提问、未作答、未评价。
- Coach 报告：未生成（真实调用被规则拒绝）。
- 因此"20 题 + Coach + 记忆"的完整闭环**没有**达成，只是"18 题 + 记忆"成立。

## 6. 与 docs/80 的对照

| 维度 | docs/80（Codex，17 答卡死） | 本轮（WorkBuddy，18 答） |
|---|---|---|
| 结束方式 | 停在 `IN_PROGRESS`，第 18 条评价丢失 | `COMPLETED`，第 18 条评价正式提交 |
| 原因 | 下一题预留不足直接抛错 | `TOKEN_LIMIT / NEXT_QUESTION_RESERVATION` |
| 评价落库 | 17 | 18 |
| 结束原因持久化 | 无（且 `completion_json` 因对象共享从未落库） | 有，且语义区分到具体限制 |
| Coach | 未跑（会话没结束） | 跑了，被 `COACH_COMPUTED_NUMBER` 误杀 |
| 记忆 | 无 | 3 条，引用真实评价 |

## 7. Capture / Manifest / Replay

- 47 条请求全部 `transport=RESPONDED`，`status=200`，`known_tokens` 与 `model_admissions` 的 `charged_actual` 完全一致（588,586）。
- `replay_check.json`：47 行各精确回放 2 次，改一个空格即被 `REPLAY_INPUT_HASH_MISMATCH` 拒绝。
- `index.json` / `captures.jsonl` / `manifest.json` 完整；包含 `retry_of` 标记的 5 条（显式重采样，不是静默重复发送）。
- 停止原因：`COACH_NOT_ACCEPTED`；额度拒绝（`MODEL_USER_QUOTA`）记录在报告尝试表与 `stop_cleared_8.json`。
- 本批共 8 个 `stop_cleared_*.json`，每一次清除停止标记都写明了原因、证据与采取的动作，没有修改任何业务数据。

## 8. 仍未修复（交下一轮）

1. Evaluation 输出上限与思考模式的匹配（配置或路由层，需先决定生产策略）。
2. `router.py` 丢弃 `thinking_type`（P1-2 旧账）。
3. Coach 输入上限与数字围栏（两个独立问题，都要改）。
4. `structured_output_failed` 的受控重采样。
5. 650k/20 题的预算与题量匹配。
6. 非自适应 `core_service.answer` 的同根因死路。
