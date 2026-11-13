# 第十三轮：Coach Agent 自主化 V1（Phase 0 结构化输入 + Phase 1 受限工具）

真实模型 HTTP = **0**。两个阶段分别提交、分别验证：Phase 0 通过后才开始 Phase 1。
Harness **默认 OFF**，OFF 路径的提示词、schema 与生成键完全未变。

---

## 1. Phase 0：输入问题的根因与修复

### 1.1 根因

`evidence.py::project_snapshot` 对两个记忆字段直接调用了 `_clip()`：

```python
approved, approved_meta = _clip(snapshot.get("approved_memory_context"), 600)
```

`_clip` 的行为是"不是字符串就 `str()` 再截断"。所以模型实际收到的是：

```
{'semantics': 'USER_CONFIRMED_CONTEXT_NOT_SKILL_PROOF', 'items': [{'id': 1, 'content': '希望做后端
```

—— 一个在 token 中间被切断的 Python repr。更严重的是 `memory_guided_plan` **根本不在投影白名单里**，它被记进 `omitted["snapshot_fields"]` 后丢弃。也就是说 Coach 既看不到"这个用户历史上在 Kafka 上有冲突"，也不知道"本场为什么优先考这些点"。

这不是"信息太多需要压缩"，是**信息在进入模型之前就被破坏了**。上一轮把这一点写进 `docs/91` §7.2 时只是描述现象，本轮做了修复。

### 1.2 修复（`COACH_EVIDENCE_V1` → `COACH_EVIDENCE_V2`）

| 字段 | 之前 | 现在 |
|---|---|---|
| `approved_memory_context` | dict 的 str，截断 600 字符 | 结构化：`semantics` / `epistemic_status` / `is_stable_fact` / `items[≤8]`（白名单键：id、content/text、memory_type、observation_type、title）/ `shown` / `total` / `clipped` |
| `recent_performance_observations` | 同上 | 同上，`section=PRIOR_PRACTICE_OBSERVATIONS` |
| `memory_guided_plan` | 被丢弃 | `capability_context`：`version` / `inferred` / `frozen_at` / `fingerprint` / `semantics` / `scope_note` / `items[≤10]` / `items_total` / `deprioritized_targets[≤8]` |
| `training` | 被丢弃 | `training_context`：`mode` / `selected_knowledge_id` / `version` / `semantics` / `targets[≤10]` |
| 每条评价 | 无身份 | 增加 `fingerprint`（裁剪**之前**计算的内容摘要，Phase 1 工具用它证明"返回的就是冻结的那条"） |

`capability_context.items` 每项含：`knowledge_id`、`label`、`state`、`intent`、`priority`、`needs_recheck`、`memory_revision`、`reason_tags`、`observation_refs[≤3]` + `observation_refs_total`、`history_summary`（≤160 字）+ `history_clipped`。

### 1.3 四条硬性约束怎么落地的

| 要求 | 落地方式 | 证据 |
|---|---|---|
| 严格区分四类信息 | 四个互不嵌套的顶层区块：`evaluations`（本场评价）/ `snapshot.requirements`（本场岗位要求）/ `capability_context`（历史能力，含 session-time 冻结标记）/ `training_context`（本场训练范围） | `test_training_context_is_separate_and_labelled`、`test_memory_is_structured_not_a_stringified_dict` |
| 不把跨岗位能力当岗位独有 | `capability_context.scope_note` 明写"按 topic 跨所有面试聚合，不是本岗位掌握度证据" | `test_capability_scope_says_it_is_not_job_specific` 断言原句 |
| 不得用 `str(dict)` | 白名单键 + 逐字段裁剪；测试直接断言 payload JSON 里不存在 `{'semantics'` 这样的 repr 片段 | 同上 |
| 不为避免截断而无限加上下文 | 8 条记忆 / 10 个计划项 / 3 个引用 / 160 字摘要，全部有 `shown`/`total`/`clipped` 计数；40 条记忆只多 ~800 字节 | `test_memory_growth_is_capped_not_unbounded` |

旧报告不受影响：投影结果不落库（落库的是 `coach_analysis_json`），所以形状变化只影响新生成；`resolve_coach_artifact` 对既有产物仍然通过（真实 18 轮夹具上 strengths=5 等断言保留）。

### 1.4 真实 18 轮输入的体积代价（实测）

用 `git show` 取出 Phase 0 之前的 `evidence.py`，对同一份 `coach_live_18.json` 分别投影：

```
V1: 85,047 bytes
V2: 86,830 bytes   (+1,783，+2.1%)
```

增量主要来自 18 条评价各自的 64 位指纹（夹具里的计划与记忆本身为空，所以 `capability_context` 只是空结构）。仍有 28% 余量到 120,000 的投影上限、到 200,000 的信封上限。

---

## 2. Harness OFF / ON 的差异

| 维度 | OFF（默认） | ON |
|---|---|---|
| 提示词 | `COACH_PROMPT` 1.0.3，**逐字节未改** | `interview_coach_plan` 1.1.0 + `interview_coach_final` 1.1.0 |
| 输出 schema | `CoachArtifact`（无 `suggested_training`） | `CoachTurn`（工具或终稿）→ `HarnessCoachArtifact` |
| 模型调用 | 1 | 1（未提供工具，或模型选择直接给终稿）/ 2（用了一次工具） |
| 生成键 | 与第十一轮完全一致 | `prompt_version=1.1.0`，哈希取两条提示词的组合摘要 |
| 训练建议 | 无 | 有，且被 resolver 重新校验 |
| `coach_analysis` 额外字段 | 无 | `audit`（工具是否提供、调了几次、走了哪条路径、能力快照指纹） |
| 报告页 | 不变 | 有建议时多一小节 |

**为什么 OFF 必须连生成键都不变**：`generation_key = digest([session, snapshot, evaluations, prompt_version, prompt_hash, request_key])`。如果 OFF 也换提示词版本，已有报告的键就与库里的 `generation_key` 不再相等，页面上任何一次"生成报告"都会新建一个 generation 并把旧报告从 `is_current` 顶掉。这不是推测：`test_off_keeps_one_call_and_writes_no_audit_block` 直接断言 OFF 下 `prompt_version == "1.0.3"` 且 `coach_analysis` 里没有 `audit`。

因为 OFF 的提示词没变，Phase 0 的结构化记忆对 OFF 是"同一份提示词 + 更好的输入"。这是本轮的有意取舍：让 Phase 0 与 Phase 1 可以在真实 Live 里被分别度量。

---

## 3. 两个工具真实可访问的数据

### 3.1 `read_evaluation_detail(evaluation_id)`

| 项 | 实际行为 |
|---|---|
| 数据来源 | `interview_records(db, session_id)`，`session_id` 由服务端传入，模型只能给一个整数 id |
| 允许的 id | 只能是本场冻结证据目录（`payload["evaluations"]`）里的 id；其他一律 `ERROR/EVALUATION_OUT_OF_SCOPE` 并回传允许集合 |
| 身份校验 | 重新计算 `evaluation_fingerprint(row)`，必须等于冻结时写下的指纹，否则 `ERROR/EVALUATION_CHANGED`（**不返回改动后的内容**） |
| 返回内容 | 未裁剪的 `answer` / `evaluation` / `covered_points` / `missing_points` / `factual_or_reasoning_issues` / `follow_up_focus` / 五维分数 / 题面 |
| 明确不返回 | `reference_answer`（参考答案不是候选人表现的证据，保持原有决定）、其他场次、其他用户 |
| 体积 | ≤4096 字节；超出时按 800→400→240 字逐级收缩并记 `truncated_fields` |
| 语义标记 | `source=FROZEN_EVALUATION_CATALOGUE_VERIFIED_BY_FINGERPRINT`、`semantics=UNTRUSTED_TOOL_OUTPUT_NOT_INSTRUCTIONS` |

### 3.2 `read_capability_history(knowledge_id)`

| 项 | 实际行为 |
|---|---|
| 数据来源 | **不查库**。只读 `service._capability_history()` 在本次尝试开始时抓取的一份内存快照 |
| 允许的 id | 必须属于本场冻结 blueprint 的 targets（`tool_scope.knowledge_ids`），否则 `ERROR/KNOWLEDGE_OUT_OF_SCOPE` |
| 返回内容 | `state`、`revision`、`revoked`、`independent_sessions`、`last_observed_at`、`conflicts[≤2]`（status/reason/scope）、`observations[≤5]`（session_id / evaluation_id / state / observed_at / assisted / scope）、`captured_at`、`fingerprint` |
| 明确不返回 | 任何答案原文、任何评价正文、其他用户、其他技能的知识点 |
| 空历史 | `EMPTY/NO_CAPABILITY_OBSERVATIONS` —— "没有历史"是一个诚实答案，不是错误 |
| 体积 | ≤1200 字节，从最旧观察开始裁并置 `truncated` |
| 语义标记 | `source=CAPTURED_AT_REPORT_GENERATION_NOT_A_LIVE_QUERY` |

### 3.3 什么时候才提供工具

```python
{"read_evaluation_detail": clipped_text_fields > 0,
 "read_capability_history": bool(capability_history.topics)}
```

投影没有被裁剪 → 评价工具不提供；用户在这个技能上没有任何记忆行 → 历史工具不提供。两者都不成立时，schema 直接是终稿，一次调用结束。"为用工具而用工具"在这里没有发生的机会。

---

## 4. 证据冻结与版本机制

本轮把"冻结"拆成两层，并各自标注，避免用实时数据冒充历史快照：

| 层 | 何时产生 | 语义标记 | 用途 |
|---|---|---|---|
| 本场能力状态 | 面试创建时写入快照的 `memory_guided_plan` | `RULE_GENERATED_TRAINING_HINT_FROZEN_AT_SESSION_START` | 输入里的 `capability_context`：本场是带着怎样的历史判断开始的 |
| 报告期能力快照 | 报告尝试开始时读一次，之后不再读库 | `CAPTURED_AT_REPORT_GENERATION_NOT_THE_SESSION_SNAPSHOT` | 工具回答、训练建议合法性校验 |

报告期快照的细节：

- **只读一次**：`service.generate` 在同一个事务里取 `CapabilityMemoryService(db).list(user_id)`，之后两次工具调用都读这份对象，不会出现"第一次说 VERIFIED、第二次说 CONFLICTED"。
- **内容指纹**：`fingerprint = digest(topics)`，只对内容做摘要（不含时间戳），同样的记忆状态永远得到同一个指纹；它被写进 `audit.capability_capture`，也写进工具步骤的 `input_json`。
- **逐条修订号**：每个 topic 带 `revision`，`audit` 里能看出建议是基于哪一版记忆给出的。
- **可审计**：工具调用写成 `experience_agent_step_runs(step_name='coach_tool')`，含工具名、参数、结果、目录规模、能力指纹与耗时；最终建议与校验结论写在 `coach_analysis_json`。
- **可复核**：`coach_tool` 步骤记录了工具实际返回的完整结构（≤4KB/≤1.2KB），所以"当时模型看到了什么"是可查的，不需要重跑。

**诚实说明**：这不等于"逐字节可重放"。跨运行的重放依赖既有 `generation_key`——同一份快照 + 同一批评价 + 同一提示词，第二次请求直接复用已完成的报告（`test_the_same_frozen_input_reuses_the_same_report` 断言 provider 只被调用 1 次）。如果记忆在两代报告之间变化，那是一次**新的生成**，审计里会看到不同的 `capability_capture` 指纹。要做到"记忆也进入生成键"需要把指纹放进 `generation_key`，那会让记忆一变动就新建 generation，本轮没有这么做（会把已有报告顶掉），列为 §10 的一个决策点。

---

## 5. `suggested_training` 的校验

模型输出（可选，≤1 条）：

```json
{"mode": "WEAKNESS|RECHECK|FULL", "knowledge_id": "...", "reason": "...",
 "supporting_evaluation_ids": ["evaluation:12"], "scope": "CURRENT_JOB_TARGET|GENERAL_SKILL"}
```

`resolve_coach_artifact` 逐条校验，**用的是训练中心同一个函数**：

| 检查 | 规则来源 | 失败处理 |
|---|---|---|
| 引用合法 | `resolve_evaluations`（本场 catalog） | 丢弃 + `suggested_training_rejected.code=UNKNOWN_EVALUATION` |
| 目标在冻结范围 | 本场 `adaptive_blueprint.targets` | `KNOWLEDGE_NOT_IN_FROZEN_TARGETS` |
| 模式与状态相符 | `interview.training.modes(state, revoked)`，与 `TrainingService` 完全同一份实现 | `MODE_NOT_ALLOWED_FOR_STATE`（例如 PRACTICING 建议 RECHECK） |
| 理由不含编造数字 | 既有数字门禁 `_qualitative` | 丢弃 + 原错误码 |

关键取舍：**建议不合法不导致报告失败**。findings、统计、记忆提案都已经合法，因为一条可选建议而丢掉整份报告是更坏的结果。所以非法建议被丢弃并留下 `suggested_training_rejected`（含 code / detail / stage），`statistics` 与 `strengths` 原样保留——`test_recheck_suggestion_needs_the_state_that_allows_it` 显式断言了这一点。

建议永远只是建议：它存在 `coach_analysis_json.suggested_training` 里，前端把它显示成一句话 + 一个**预选**链接（`/training?mode=…&knowledge_id=…`），训练中心仍会照常调用 workbench 重新校验；条件不满足时那个目标根本不会出现。没有"因为 Coach 说了就创建会话"的路径。

---

## 6. 旧报告兼容

| 面 | 状态 | 依据 |
|---|---|---|
| 已生成报告的读取 | 不变 | 报告只持久化 `coach_analysis_json` 与 `statistics_json`，不持久化投影 |
| OFF 的生成键 | 不变 | `coach_prompt_signature(False)` 返回 1.0.3 的原文与哈希；`test_off_keeps_one_call_and_writes_no_audit_block` 断言 `prompt_version == "1.0.3"` |
| OFF 的 schema | 不变 | `CoachArtifact` 未加字段（`test_the_off_artifact_schema_did_not_gain_the_suggestion_field`） |
| OFF 的 `coach_analysis` | 不变 | 不写 `audit`，不写 `suggested_training`（同一测试断言） |
| 旧提示词可回放 | 保留 | `CoachPromptRegistry` 仍能取到 1.0.0/1.0.1/1.0.2/1.0.3 |
| 报告链路整体 | 通过 | `test_m5_report.py` 21 条、`test_m5_two_session_e2e.py`、`test_v23_training.py` 等全绿 |

---

## 7. 测试用例与结果

### 7.1 Phase 0（9 条新测试 + 既有证据/数字门禁）

`tests/test_coach_structured_memory.py`：结构化记忆、能力计划投影、跨岗位语义、训练上下文隔离、评价指纹稳定性、体积与分节统计、记忆增长封顶、真实 18 轮夹具、超预算诊断。

`tests/test_coach_evidence_budget.py` 的版本断言从 V1 改为 V2，其余断言（18 轮仍 <50% 原始体积、20 轮线性增长、缺失字段不复制、超限报 `COACH_EVIDENCE_BUDGET`）全部保留。

### 7.2 Phase 1（17 条）

要求的 12 个场景与用例对应：

| # | 场景 | 用例 |
|---|---|---|
| 1 | 证据充分直接 Final | `test_model_may_decline_the_tool_and_finish_in_one_call` |
| 2 | 评价被裁剪 → 查询评价 | `test_evaluation_tool_returns_unclipped_detail_and_is_audited` |
| 3 | 跨场次冲突 → 查询记忆 | `test_capability_tool_returns_references_not_other_answers` |
| 4 | 无历史 → 不提供无意义工具 | `test_evaluation_tool_is_only_offered_when_the_projection_clipped_something` |
| 5 | 无权引用他人评价 | `test_tool_failure_never_fabricates_evidence`（越界 id → `EVALUATION_OUT_OF_SCOPE`，回传允许集合） |
| 6 | 工具超时 / 非法参数 | `test_tool_timeout_is_audited_and_the_report_still_finishes`、`test_one_turn_has_exactly_three_legal_shapes`（`evaluation_id=0`、字符串 id、未知 action 全部拒绝） |
| 7 | RECHECK 建议不满足准入被拒 | `test_recheck_suggestion_needs_the_state_that_allows_it`（UNKNOWN/PRACTICING/VERIFIED/GAP_SUSPECTED 四种状态都拒绝） |
| 8 | 合法 CONFLICTED/RECHECK_DUE 可通过 | 同一用例的另一半，断言 `validation=LEGAL_AT_REPORT_TIME_ADVISORY_ONLY` |
| 9 | 超预算安全失败 | `test_harness_respects_the_input_budget_before_calling_a_model`（`provider.calls == 0`） |
| 10 | 同一输入精确复用 | `test_the_same_frozen_input_reuses_the_same_report`（同 id + `reused` + 只有 1 次调用） |
| 11 | OFF 保持旧 Coach | `test_off_keeps_one_call_and_writes_no_audit_block` |
| 12 | 报告/统计/记忆不回归 | 全量回归 808 passed；`test_m5_report.py`、`test_v23_training.py` 全绿 |

另有：单次调用无工具路径（`tool_limit=0`）、WEAKNESS/FULL/revoked 的同一规则、越界知识点的建议、建议引用不存在的评价。

### 7.3 真实计数

| 项 | 结果 |
|---|---|
| 后端全量（业务段）@ `interview_agent_wb_r13_test_20261113` | **808 passed / 0 failed / 1 skipped**（227.61 s） |
| 迁移段 @ 空库 `interview_agent_v21_wb_r13_20261113` | **10 passed / 0 failed**（53.75 s） |
| Coach 相关子集（harness + 三份 coach 测试 + 报告 + 训练 + 私有） | **92 passed**（51.47 s） |
| 前端 `vue-tsc && vite build` | 通过，0 类型错误 |
| 真实模型 HTTP | **0** |

### 7.4 测试发现的两个真实缺陷（不是读出来的）

1. `read_capability_history` 写成了普通函数，调用点却 `await` 它 → 每次工具调用都抛 `TypeError`，报告变 FAILED。
2. 工具白名单取自 `capability_context.items`（计划里被**优先**的点），而不是本场冻结 blueprint 的全部 targets。后果是：一个没有历史记忆、但完全合法的知识点会被判成越权。两处都是先有测试、再定位、再修。

---

## 8. 与旧 Coach 的离线对照

### 8.1 可以量化的部分（已做）

| 指标 | OFF / 无工具 ON | 工具 ON |
|---|---|---|
| 模型调用次数 | 1 | 2 |
| 工具调用次数 | 0 | 1（上限 1） |
| 输入体积（真实 18 轮夹具） | 86,830 字节（Phase 0 前 85,047） | 同 + 工具结果 ≤4KB |
| 工具结果上限 | — | 4,096 / 1,200 字节 |
| 报告生成键 | 与上一轮一致（1.0.3） | 1.1.0 |

### 8.2 不能声称的部分

本轮**没有**成对的真实模型输出。所有"新 Coach 更好"的比较都需要同一批会话在 V1 与 V2 下各跑一次真实模型，而本轮真实 HTTP = 0。因此：

- 不声称 Phase 0 提升报告质量；
- 不声称 Harness 提升了建议的可用性；
- 只声称：结构上模型现在**第一次**拿到了它此前拿不到的东西（结构化记忆、优先级、冲突状态、可查询的未裁剪证据），且这些改动没有让任何既有行为回归。

Replay 只能证明"机制在工作"，不能证明"模型会用得好"。这一点在 §10 里列为需要 Live 验证的第一项。

---

## 9. 模型调用、Token 与性能风险

| 项 | 数值/机制 |
|---|---|
| 最多模型调用 | 2（`interview_coach_model_limit`，且第二次 schema 是终稿，不存在第三次） |
| 最多工具调用 | 1（`interview_coach_tool_limit`） |
| 工具超时 | 2 秒，超时返回 `ERROR/TOOL_TIMEOUT`，模型仍需给终稿 |
| 不提供工具的判定 | 未裁剪 + 无记忆 → 单次调用 |
| 预算检查 | 每次发送前算信封（`payload + schema + system`），超过 `interview_coach_max_input_bytes`（200,000）→ `COACH_INPUT_BUDGET` 422，**不静默裁剪**；`test_harness_respects_the_input_budget_before_calling_a_model` 断言此时 provider 调用数为 0 |
| HTTP 重试 | 未新增。仍走 `generate_structured` 内既有的"一次结构修复"与 provider fallback 规则 |
| 治理与结算 | 两次调用都经 `ResearchModelCalls` → `generate_structured` → `model_scope`/admission，token 用量按步骤写入 `experience_agent_step_runs` |

**性能风险（事实）**：

1. 用工具的报告要发送两次输入，成本约 `2 × payload`。以 18 轮夹具计约 +87KB 输入；这是本设计最主要的代价，限制手段是"只在确有缺失时才提供工具"。
2. 若工具的返回把模型推向更长的总结，输出 token 也可能上升，本轮未测。
3. 第二次调用没有使用提示词缓存保证（不同 provider 能否命中前缀缓存取决于部署），所以按最坏情况≈2 倍输入做预算更稳妥。

---

## 10. 仍需真实 Live 验证的条件

1. **模型是否真的会用这两个工具**（而不是永远直接给终稿）。Replay 只能证明通道存在；工具使用率、调用是否合理，必须看真实输出。
2. **真实 schema 遵循度**：`CoachTurn` 是判别式联合，模型在真实 provider 下的首轮 JSON 合法率未知；若偏低，`structured_retry` 会吃掉第二次调用额度。
3. **质量对照**：同一批会话 V1（1.0.3）vs Phase 0（1.0.3 + 结构化输入）vs Phase 1（1.1.0 + 工具）三组真实产物的人工/规则对照。
4. **Token 与延迟实测**：本轮只有字节数与调用次数，没有真实 token 计量。
5. **建议合法性在真实记忆下**：本轮用 `modes()` 的单元测试覆盖了四种状态，尚未在真实 `CONFLICTED`/`RECHECK_DUE` 用户上端到端跑一次。
6. **`generation_key` 是否应包含能力快照指纹**：现在是"记忆变了不重算、审计里可见"；若产品希望"记忆变了就该重算"，需要一次显式设计（会改变已生成报告被复用/顶替的语义），本轮未做。
7. 前端只做了必要兼容（报告页一句建议 + 预选链接），未做工具轨迹查看页；真实 Live 后若需要排查，再决定是否加。

---

## 附：本轮库与提交

| 库 | 用途 | 状态 |
|---|---|---|
| `interview_agent` | 生产 | 未连接、未迁移 |
| `interview_agent_wb_r13_test_20261113` | 业务回归 | 0027 / 67 表 |
| `interview_agent_v21_wb_r13_20261113` | 迁移用例空库 | 跑完 0027，不可二次跑迁移用例 |
| 历史 Live 库 | 封存 | 只读，未连接 |

| 提交 | 内容 |
|---|---|
| `790913e` | Phase 0：结构化记忆投影 + 9 条测试 |
| `4e726f9` | Phase 1：受限工具、建议校验、审计、前端兼容 + 17 条测试 |
| 本文档 | `docs/92` |
| 项目记忆 | `.codebuddy/CODEBUDDY.md` |
