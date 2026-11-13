# 第十四轮：Coach Harness V1 小规模真实 Live 对照验收

本轮真实模型 HTTP = **3**（上限 6），Token = **77,110**（上限 180,000），
未迁移生产库、未连接历史 Live 库写入、未新增 Agent、未改 Controller / CapabilityMemory。

开工 HEAD `ee8d3b3`，工作区干净（仅 `.workbuddy/` 未跟踪）。

---

## 1. 本轮场景与证据来源

| 项 | 事实 |
|---|---|
| 来源库（只读） | `interview_agent_wb_full_live_20261111`：3 会话 / 18 条真实评价 / 3 条能力记忆 / 1 份报告（11-11 的 generation 1，状态 FAILED，错误 `MODEL_USER_QUOTA`） |
| 会话 | `interview_sessions.id = 2`，`COMPLETED`，18 问 18 评价，`completion_reason=TOKEN_LIMIT`、`completion_detail=NEXT_QUESTION_RESERVATION`、`question_budget=20` → `early_terminated=true` |
| 统计 | 综合平均 4.0667/5（服务端 deterministic，三轮完全一致：4.0667 / 18 / 18 / 0 / 12 / 0 / 4） |
| 隔离副本 | `interview_agent_wb_r14_real_20261113`（`CREATE DATABASE … TEMPLATE` 复制，0026 → 0027 显式授权迁移） |
| 原件状态 | 复核：仍是 1 份 FAILED 报告、18 评价、49 条准入；**未被本次实验触碰** |
| 能力记忆 | 3 条，均 `PRACTICING`：`python` / `rag_retrieval` / `agent`（各 5 条观察、0 冲突、revision 1） |
| 冻结蓝图 | 5 个知识点：python、rag_retrieval、agent、system_design、llm；其中前三个有记忆行，后两个 `UNKNOWN` |

**场景覆盖情况（如实）**

| 场景 | 本轮是否覆盖 | 说明 |
|---|---|---|
| A. 证据充分 → 直接 Final | ✅ 真实覆盖（2 次） | 模型在两个工具都可用时主动选择 `action=final` |
| B. 证据被裁剪 → 查询评价详情 | ❌ 未由真实模型触发 | 投影只裁剪了 1 个字段，模型判断不需要（见 §3） |
| C. 真实跨场次冲突 → 查询能力历史 | ❌ 该归档没有 `CONFLICTED` | 3 条记忆都是 PRACTICING、0 冲突；不伪造状态 |
| 旧 Coach 报告作参考 | ❌ 不可用 | 该会话唯一的历史报告是 FAILED（11-11 `MODEL_USER_QUOTA`），没有可比较的旧成品 |

因此本轮是**两组对照**（OFF vs ON），不是三组消融；不声称构成严格消融实验。

---

## 2. OFF / ON 输入及版本差异

同一会话、同一评价集、同一快照，唯一变量是门控开关与提示词：

| 项 | OFF | ON |
|---|---|---|
| 提示词 | `interview_coach` 1.0.3 | `interview_coach_plan` 1.1.0 → `interview_coach_final` 1.1.0 |
| 输出 schema | `CoachArtifact` | `CoachTurn`（联合）→ `HarnessCoachArtifact` |
| 证据投影 | `COACH_EVIDENCE_V2`，86,849 字节 | 同左（同一份投影，ON 追加 `coach_harness` 目录块） |
| 输入封装字节 | 92,417 | plan 96,084 / final 94,710（上限 200,000） |
| 工具 | 无 | 两个都提供：`read_evaluation_detail`、`read_capability_history` |
| 能力快照 | 不捕获 | `8229039814…`（07:21:06 / 07:22:09 两次捕获指纹相同） |
| 模型 | ModelScope `deepseek-ai/DeepSeek-V4.1-Flash-1017` | 同左 |

裁剪证据（投影自报）：`clipped_text_fields = 1`；`questions` 18 条（裁掉 `standard_answer`/`candidate_refs`/`job_requirement_id`）、
`evaluations` 18 条（裁掉 `reference_answer`/`answer_framework`/`issues` 等）、`review_missing_mechanisms = 51`。

---

## 3. 真实模型动作与工具轨迹

| 运行 | 路径 | 模型调用 | 工具调用 | 结果 |
|---|---|---|---|---|
| OFF（gen 1） | 单次调用 | 1 | — | COMPLETED |
| ON #1（gen 2） | `SINGLE_CALL_MODEL_CHOSE_FINAL` | 1 | **0** | COMPLETED |
| ON #2（gen 3，新请求键） | `SINGLE_CALL_MODEL_CHOSE_FINAL` | 1 | **0** | COMPLETED |

审计字段（ON #1 / #2 相同）：`tools_offered=["read_capability_history","read_evaluation_detail"]`、
`calls=1`、`tool_limit=1`、`model_limit=2`、`capability_capture=8229039814…`。

**结论（只说观察到的）**：在这份输入上，模型两次独立运行都判断证据已足够，**主动选择不查工具**。
按本轮规则不去诱导它调用，因此**真实模型下的工具执行路径未被执行**——工具机制只有第 13 轮的离线覆盖
（12 个场景 + 越界/超时/指纹变化），不能用本轮结果宣称"工具在真实模型下有效"。

顺带得到的可用性事实：`COACH_EVIDENCE_V2` 把 18 条评价压到 86,849 字节后仍只裁剪 1 个字段，
说明"必须查工具才能写"的情况在当前投影下并不常见，工具的价值更可能出现在**跨场次冲突**而非单场长答案。

---

## 4. suggested_training 合法性

| 运行 | mode | knowledge_id | 引用 | 存储状态 | 判定 |
|---|---|---|---|---|---|
| ON #1 | WEAKNESS | `ai_agent_application:agent` | [9,16,17,18] | PRACTICING / 可用模式 [FULL, WEAKNESS] | 合法 |
| ON #2 | WEAKNESS | `ai_agent_application:rag_retrieval` | [5,6,14,15] | PRACTICING / 可用模式 [FULL, WEAKNESS] | 合法 |

- 两条建议都由 `resolve_coach_artifact` 用 `interview.training.modes()`（与 `TrainingService` 同一个函数）重算，
  并在存储时补上 `state` / `available_modes` / `skill_id`，因此"模型说什么"与"系统认什么"可区分。
- 引用抽查（读原评价原文确认不是装饰性引用）：
  - #16 讨论"次数记在意图表、最多三次"；#17 追问"计数落哪个字段、两个 worker 并发"；
    #18 承认"只加 attempts 挡不住并发"并改成 PENDING→PROCESSING 状态迁移 → 支撑"Agent 重试收尾"。
  - #9 明确说"没真正在线上实现，服务端没有记次数字段" → 支撑"早期只靠条件自增/未闭合"的弱项判断。
  - #5 把嵌入相似度 0.8 当作"有依据"；#14 讲历史分位数 P10/P50 阈值 → 支撑"引用校验/阈值基线偏经验"。
- 没有出现 RECHECK：该归档不存在 `CONFLICTED` / `RECHECK_DUE`，模型也没有凭空提 RECHECK（这是正确行为）。
  非法建议的拒绝路径仍由第 13 轮单元测试覆盖，本轮未在真实数据上触发。

---

## 5. Coach 报告质量对照

机械核对（`quality.json`，全部从库里读，不看模型自述）：

| 指标 | OFF (1.0.3) | ON #1 | ON #2 |
|---|---|---|---|
| 优势 / 弱项 / 复现问题 / 岗位对齐 条数 | 5 / 5 / 2 / 4 | 4 / 5 / 2 / 3 | 4 / 4 / 2 / 3 |
| 行动建议 / 练习计划 / 记忆提案 | 5 / 6 / 3 | 4 / 4 / 2 | 4 / 5 / 2 |
| 引用到的评价（去重 / 共 18） | 14 | 17 | 15 |
| 引用是否都真实存在 | ✅ | ✅ | ✅ |
| 越权/跨岗位掌握度断言 | 0 | 0 | 0 |
| 无依据数字（分/次/道题/场/轮） | 0 | 0 | 0 |
| 有无 suggested_training | 无 | 有 | 有 |

逐案例判断：

- **实际改善**：ON 多出一个原版结构上不可能有的输出——基于真实评价、经训练规则复核的下一步建议；
  且两次都以"本场因会话预算提前结束，属于流程终止而非能力结论"的口径收尾（与 `TOKEN_LIMIT` 事实一致）。
- **无变化**：优势/弱项的技术内容与 OFF 高度重合（outbox 租约与 fencing、Kafka exactly-once 边界纠正、
  RRF 融合、引用支持度、阈值基线、Agent 重试收尾），说明**证据本身**才是决定内容的主因，不是门控。
- **轻微变化**：ON 更精简（少 1 条优势、少 1 条岗位对齐、少 2 条练习），引用覆盖略高；这是风格差异，不能算质量提升。
- **退化**：未观察到。统计口径、引用校验、记忆提案约束、报告持久化全部与 OFF 一致。
- **编造检查**：无编造次数/经历/掌握度；`epistemic_status` 仍为 `AGENT_SYNTHESIS_OF_THIS_INTERVIEW_NOT_STABLE_CANDIDATE_FACTS`。
- **一处值得记录的差异**：ON 两次给的建议对象不同（agent vs rag_retrieval），两条都合法且有引用支持，
  说明建议在"多个同等弱项"时**不稳定**。这不是错误，但意味着不能把建议当确定性结论。

---

## 6. 真实 HTTP、Token 与延迟

| 运行 | HTTP | 输入 | 输出 | 合计 | 延迟 |
|---|---|---|---|---|---|
| OFF (gen 1) | 1 | 23,115 | 1,987 | **25,102** | 12,822 ms |
| ON #1 (gen 2) | 1 | 24,272 | 1,756 | **26,028** | 11,227 ms |
| ON #2 (gen 3) | 1 | 24,272 | 1,708 | **25,980** | 11,120 ms |
| OFF 复跑（幂等） | **0** | — | — | 0 | — |
| **合计** | **3 / 6** | | | **77,110 / 180,000** | 约 35 s 模型耗时 |

- 准入账本：当日 3 条 `SETTLED`，`actual_tokens = charged_tokens = 77,110`，无 `UNKNOWN`、无 `RESERVED` 残留。
- 每次请求都有发送前 reserve、发送后 settle；`structured_retry = 0`（三次都没有触发修复重发）。
- 成本增量：ON 相对 OFF **+926 / +878 token（+3.69% / +3.50%）**，与封装字节增量（+3.97%）同阶；
  因为模型没有调用工具，没有出现"2 × payload"的最坏情况。
- 延迟：ON 略低（11.2 s vs 12.8 s），属于单次采样噪声，不作为性能结论。
- 预热阶段的两次脚本级失败（缺 mapper 导入 / 触发器校验参数错误）**没有发出任何请求**：
  当时当日准入记录为 0，DB 中无新报告行、无新 agent run。这属于本机脚本问题，不是 Provider 错误。

---

## 7. Capture / Replay 结果

- **未使用历史 Live Capture 伪造输出**：三份报告全部由真实 Provider 现跑，`producer=SUMMARY_COACH_AGENT`、
  `provider=modelscope`、`model=deepseek-ai/DeepSeek-V4.1-Flash-1017`。
- **Replay 的真实含义被验证**：同一冻结输入再跑一次 OFF，命中既有 `generation_key`，
  直接返回已完成报告（`reused=true`），**0 次 HTTP、0 token**——这是"幂等复用"，不是"逐字节重放模型"。
- **离线 Replay 仅用于测试**（`ReplayModelProvider`），与本次 Live 结果分开记录，未混用。
- 能力快照两次捕获指纹相同（`8229039814…`），说明报告期记忆在实验期间未变化，
  ON #1/#2 的输入差异只有 `request_key`（生成键不同，故生成新代次而不是复用）。

---

## 8. 生成键与冻结语义（本轮开工前的检查结论）

真实调用前先审了生成键，发现并修掉一个真实缺口（提交 `b7a9403`）：

| 检查项 | 结论 |
|---|---|
| OFF 旧键是否兼容 | ✅ 公式逐字节不变。证据：本轮的 OFF 运行**命中 11-11 那条 FAILED 行的旧键**，直接在该行上重试并完成（没有新建代次），这是最强的兼容性证明 |
| ON 新键是否区分 OFF/ON、提示词、工具策略、冻结输入 | 修复前：只区分前两项。修复后：额外汇入 `tools.policy_signature()`（工具契约版本 + 实际 tool/model 限额 + 输出上限）与能力捕获指纹 |
| 同会话报告期 Memory 变化后是否可能误用旧报告 | 修复前：会（键里没有能力指纹）。修复后：ON 下记忆变化即新键 → 新代次；OFF 保持历史语义不变 |
| 已完成报告是否会被覆盖 | 不会。id 1/3/4 三代并存，`is_current` 只有一份；DB 触发器 `Completed Report content is immutable` 阻止改写已完成内容 |
| `read_evaluation_detail` 是否只用冻结目录 + 指纹 | ✅ 只服务投影里的 id；重算行指纹不符则返回 `EVALUATION_CHANGED`，不返回改动后的内容 |
| `read_capability_history` 是否只读冻结快照 | ✅ 不查库，只读尝试开始时的一次性捕获；本轮审计里的 `capability_capture` 与两次捕获时间可核对 |

冻结语义分两层，互不冒充：

1. **会话起点**：快照里的 `memory_guided_plan` / `capability_context`，标记 `..._FROZEN_AT_SESSION_START`；
2. **报告期**：`CAPTURED_AT_REPORT_GENERATION_NOT_THE_SESSION_SNAPSHOT`，带 `captured_at`、内容指纹与逐 topic revision。

本轮只改了生成键公式（`coach_generation_key`），未改任何已存报告内容。

---

## 9. 测试结果

| 项 | 结果 |
|---|---|
| 业务段全量 @ `interview_agent_wb_r14_test_20261113`（0027 / 67 表） | **818 passed / 0 failed / 1 skipped**（246.80 s） |
| 迁移段 @ 空库 `interview_agent_v21_wb_r14_20261113` | **10 passed / 0 failed**（56.29 s） |
| 新增：生成键专项 `test_coach_generation_key.py` | 8 passed（OFF 旧公式端到端一致、ON 键随策略与能力指纹移动、policy_signature 随配置变化） |
| 新增：报告身份专项 `test_report_session_identity.py` | 2 passed（公开会话给 job_id；私人会话给 job_target_id 且 job_id 为空） |
| 前端 `vue-tsc && vite build` | 通过，0 类型错误（`InterviewReportPage` 22.53 kB） |

用例总数 808 → 818（+10），没有删除或跳过任何既有用例。

**浏览器（真实 Chrome headless + CDP，非类型检查替代）**

| 步骤 | 观察 |
|---|---|
| 打开 `/interview/reports?session_id=2` | 显示"下一场可以练什么"、模式"薄弱项专项训练"、知识点 `rag_retrieval`、以及"这是建议，不是已排好的训练"说明 |
| 读取链接 | `/training?mode=WEAKNESS&knowledge_id=ai_agent_application:rag_retrieval&job_id=1` |
| 点击进入训练中心 | 目标岗位=该会话岗位、专业方向=AI/Agent 应用开发、训练模式=WEAKNESS、知识点=`ai_agent_application:rag_retrieval` 全部预选 |
| 是否自动创建训练 | **没有**：跳转前后会话数 3 → 3；页面无"已创建训练" |
| 旧代次（点历史"第 1 版"） | OFF 报告正常显示，且**没有**建议块（该结构只存在于 Harness 产物） |

截图：`r14_01_report_suggestion.png`、`r14_02_training_prefilled.png`、`r14_03_older_generation.png`、
`r14_04_advice_block.png`（`.runtime/ui/shots/`，gitignored）。

**浏览器阶段发现并修掉的缺陷**（提交 `98c9e35`）：报告载荷没有 `job_id`，链接只能带 mode+knowledge，
用户跳到训练中心后还要重新选岗位。前端类型里明明声明了 `job_id`，所以 `vue-tsc` 全绿而值是 `undefined`——
只有真正打开页面才看得见。

---

## 10. 未解决风险与下一步结论

**是否真的提供了原版 Coach 缺少的、有证据支持的决策价值？**

- **是，但只在一个维度上**：`suggested_training` 是 OFF 结构上无法产生的输出。两次真实运行都给出了
  指向真实评价、模式合法（PRACTICING→WEAKNESS）、并在存储时被训练规则复核过的下一步建议。
- **不是，在"自主调查"这个维度上**：两个工具可用、模型两次都判断证据足够、**0 次工具调用**。
  本轮没有证据表明工具能改善报告；也没有证据表明 Phase 0 的结构化记忆被用上了
  （三份产物都没有引用跨场次状态，正文里的"历史"指"历史分位数"）。

**额外成本是否值得？**

- 直接成本很低：**+3.5% token**（约 +900），1 次调用，延迟无可测差异。
- 收益是"多一个可执行的下一步"，但**建议不稳定**（同输入两次指向不同知识点，虽然都合法）。
- 若只为这一个字段付 3.5%，**可以接受**；但当前证据不足以支撑把 Harness 默认打开——
  它的核心卖点（按需查证）在真实数据上一次都没发生。

**因此维持 `INTERVIEW_COACH_HARNESS=false`。**

**剩余风险（按严重度）**

1. **工具路径在真实模型下零覆盖**。要拿到证据，需要一个"证据确实不足"的会话：
   投影裁剪严重（例如答案极长、评价字段被压到 200 字），或存在 `CONFLICTED` 的跨场次记忆。
   本轮归档不具备这两个条件，且不允许伪造。
2. **能力记忆无岗位维度**：`capability_context` 是跨岗位按 topic 聚合的，建议里 `scope=CURRENT_JOB_TARGET`
   只在知识点属于本岗位蓝图时成立；若同一 topic 跨岗位复用，建议的岗位相关性会变弱。
3. **建议不稳定**：多个同等弱项时两次运行给不同目标。若要做成产品特性，应说明"这是候选之一"，
   或由服务端在多个合法目标中按一次确定性规则择优。
4. **生成键纳入能力指纹后，ON 报告的复用时机会变窄**：记忆一变就要新代次。这是正确性优先的取舍，
   但意味着 ON 下"刷新页面就复用"只在记忆未变时成立（GET 不触发生成，所以不会误生代次）。
5. **没有成对的多会话对照**：单会话、单归档，样本量为 1，不能推广为"ON 在多数场景更好"。
6. **私有 JD 会话未做 Live 对照**：本轮只跑公开岗位会话；私人 JD 的 `suggested_training.scope`
   与 `job_target` 的联动仍只有离线覆盖。

**下一轮若要继续，最小且有信息量的做法**

1. 选一个投影裁剪严重的真实会话（或合法构造一个高裁剪输入的离线夹具）验证"模型会不会查、查了会不会改结论"；
2. 或先补一条 `CONFLICTED` 的真实历史（需要真实重测，非本轮额度内可完成），再验证"查历史是否改变建议"；
3. 不建议在拿到上述任一证据前把 Harness 设为默认开启。
