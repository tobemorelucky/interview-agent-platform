# Director supporting_refs 数量与一次 Repair 契约修复

日期：2026-10-26。范围：V2.3 Live 失败的最小离线修复，不是新功能阶段。收费模型调用0；未迁移任何数据库、未修改模型路由或 `.env`、未commit/push。

## 1. 真实根因：四类问题分别判断

依据59、保留的8次HTTP记录、原失败数据库以及当前源码重新核对。

1. **模型生成错误**：HTTP7输出的17条 supporting_refs 与本轮17条 allowed_refs 完全相同，超过12上限。所有引用存在、组合ID合法，但“允许使用”不代表“必须全部使用”。没有依据声称模型内部为何如此选择，也不保存隐藏推理。
2. **Schema本身**：`AdaptiveSelection` 和服务端 `AdaptiveDecision` 的上限均12。未找到解释“为什么恰好12”的独立ADR；能确认它是有界决策结构约束，不是要求12条证据。选定题源1条、本轮回答或评价至少1条，以及少量辅助依据通常已足够，没有发现正常业务必须13条的证据。本轮不改Schema上限。
3. **Prompt歧义**：旧Main写的是 `supporting_refs must copy adaptive.allowed_refs`，只明确了集合来源，没有说明最小必要选择和数量；它不是明确要求全量引用，但存在被理解为复制清单的空间。Schema虽传了maxItems12，生成仍违反了它。
4. **Repair实现冲突**：只传 `validation_error=ValidationError`，没有精确字段和数量；同时要求保留全部引用字符串，`_fact_inventory` 还按路径保留每个字符串。删除引用会破坏旧修复契约，无法完成这个数量修复。HTTP8确实原样返回17项，不是JSON语法、Unicode、HTTP或题源映射问题。

### supporting_refs 到底是什么

它是Main此次策略的**理由引用**，可指向本轮Answer/Evaluation、相关Question、Fact、Requirement或Skill。不是CandidateFact的Evidence集合，也不是完整题源清单。

题目知识目标与实际题源仍由合法candidate_id绑定，解析为冻结 target/source，Controller继续验证；减少不必要的理由引用不会删除Fact→Evidence、改变Snapshot或扩大来源权限。

## 2. 修复机制

### 生成阶段：新Prompt，旧版本保留

- 新组合选择Prompt：2.1.2；新带能力记忆Prompt：2.2.1。
- 明确通常选择2–4条必要引用、绝不超过12；不能复制全部allowed_refs。
- 引用选定candidate的source.ref；有本轮回答时，还需引用current_answer_ref或current_evaluation_ref至少一个。
- 旧MAIN / MAIN_PAIRS 2.1.1 / MAIN_MEMORY 2.2.0常量保留。`AdaptivePromptRegistry`仍读取历史Snapshot冻结的版本、正文与指纹，不修改旧记录。

### 修复阶段：只给 AdaptiveSelection 的窄例外

`SupportingRefsRepair.prepare` 仅在以下条件全部满足时允许一次引用子集修复：

- 原输出可解析为JSON；Pydantic唯一错误是 `supporting_refs` 的 `too_long`。
- 所有原引用都属于当前allowed_refs；candidate_id存在，strategy属于该candidate。
- 原列表已包含选定source.ref；回答后决策也已包含至少一个本轮Answer/Evaluation引用。

如果原输出有未知引用、非法组合、缺字段或其他同时存在的Schema错误，不允许用这个例外将错误“洗白”，继续使用原严格事实保留规则并交给原业务校验拒绝。

Repair收到精确诊断，例如：

```json
{"path":["supporting_refs"],"type":"too_long","actual_count":17,"limit":12}
```

同时收到原列表的可选引用、必须保留的选定题源、至少保留其一的当前Answer/Evaluation依据。由模型选择有意义的子集，**程序不做 `[:12]`、不排序截断、不补新引用**。

Repair结果仍用**同一个AdaptiveSelection Schema**验证1–12项；再做窄规则验证：

- 新列表只能来自原列表，不得增加、替换或重复引用。
- 必须保留选定source及当前Answer/Evaluation依据。
- 除supporting_refs之外，原JSON的所有字段和值完全一致，包括candidate、strategy、reason_tags、question、expected_points、difficulty及可选字段。不能借修复改题或改决策。
- 随后仍走原 `resolve_selection`、`validate_decision` 和Controller；组合、当前回答、主题、难度、版本及预算校验没有关闭或放宽。

最多一次repair，仍失败就抛 `structured_output_failed`。没有新增Planner调用、自动业务重规划或多次循环。正常请求仍1次模型调用；需要修复时最多增加原有那1次。

其他Agent没有传这个策略，继续使用原 `_fact_inventory` 严格规则。`StructuralRepairPolicy`只是Structured Output函数的一个可选调用参数，不是新Agent或第二套运行时。

### 审计

既有StepRun output_json / model_invocations保存成功子集修复的策略版本、原/新数量、引用ID、冻结candidate与strategy。失败时保存不含原始输入的字段级validation_errors；不存隐藏推理，也没有新审计表。

## 3. 文件与核心函数

本轮直接修改：

- `apps/api/src/interview_api/infrastructure/models/structured.py`：`generate_structured` 可选修复策略、`_validation_errors`精确诊断、失败错误元数据；默认事实保留不变。
- `apps/api/src/interview_api/modules/interview/director_repair.py`（新增）：`SupportingRefsRepair.prepare/validate`，封装唯一数量错误的安全例外。
- `apps/api/src/interview_api/modules/interview/agents.py`：`InterviewAgent.call` 仅对Main的AdaptiveSelection传入策略。
- `apps/api/src/interview_api/modules/public_career/research/audit.py`：`ResearchModelCalls.call`透传可选参数、沿用既有审计记录诊断。
- `apps/api/src/interview_api/modules/interview/adaptive_prompts.py`：新Prompt版本，保留旧正文。
- `apps/api/tests/test_director_reference_repair.py`（新增19项）：Structured Output与真实PostgreSQL service边界。
- `apps/api/tests/fixtures/v23_live_supporting_refs_failure.json`（新增）：原HTTP7业务输出和缩减后的真实允许引用/组合，明确为历史Live DEMO记录，不含认证信息。
- `apps/api/tests/test_v211_director_contract.py`、`test_v22_capability_integration.py`：新默认版本断言，保留并扩展旧2.1.0/2.1.1/2.2.0冻结会话验收。
- 本文及docs索引。

本轮未改Controller、训练模式、数据库结构、HTTP API、Evidence validator、模型Provider/Router。工作区中之前V2.3改动继续保留，不把它们算成本次重新实现。

## 4. 同一失败用例：前后结果

输入取自 `.runtime/acceptance/v23_live_20261026_a.json` 的HTTP7，candidate固定 `C_6fbaf249a8e8a389386dd23b`，NEXT、agent、requirement:3。

- 修复前：17引用不能通过Schema；原严格repair无法减少字符串，失败。
- 修复后离线Replay：原17引用 → 固定Replay修复输出 `[answer:3, evaluation_run:7, requirement:3]`，成功；candidate/策略/问题/预期要点逐值一致。
- 该选定题源在原数组第15位，证明不是截取最前12项。
- 这验证了修复协议和业务链路，**不代表DeepSeek已经实测生成这个修复子集**。本轮没有Live调用。

负例仍拒绝：13项原样保留、Repair继续17项、未知引用、原始非法组合、新加“虽合法但原列表没有”的引用、丢当前回答或题源、改问题/要点/策略/组合/理由标签、重复引用、额外字段。12项正常输出不触发repair。

## 5. PostgreSQL 与回归

沿用用户确认的测试边界：公开generate_structured；真实Main/Controller service + PostgreSQL。仅外部模型响应使用明确Replay，不Mock Controller或数据库。

测试库是已有 `interview_agent_v21_v23_full_20261026_a`（0026）。没有新建或清空历史验收库，没有执行任何Alembic命令。

- 定向Structured / Director / V2.2：**44 passed**。
- 全部非迁移后端模块分两批：**305 + 219 = 524 passed / 0 failed / 0 skipped**，分别92.94秒、48.52秒，每批175秒硬上限。
- 遵守不执行migration：10个 `test_00…test_09` 迁移测试模块**未选择运行**，不把它们当作通过或因环境跳过。
- 前端重新运行：**43 passed / 0 failed / 0 skipped**。
- vue-tsc / Vite生产构建、修改Python模块py_compile、`git diff --check`通过。
- 本轮没有追加浏览器或Live测试，不借用59的浏览器结果声称本轮Live成功。

真实PostgreSQL的四条路径：

1. 17项 → 一次合法子集 → 正常提交下一题。
2. Repair仍17项 → Answer保留失败、无下一题、无正式Evaluation事件；同键重试复用已验证Evaluation，再正确提交。
3. 数量合规但未知引用 → `UNKNOWN_DIRECTOR_REFERENCE`；修复不能绕过。
4. 数量合规但非法组合 → `UNKNOWN_DIRECTOR_CANDIDATE`。

以上重试都只评价原回答一次；再次重放成功键不推进版本、不重复用户回答或有效问题，Snapshot固定。既有并发、版本冲突、事务回滚、V1/V1.2工具、Skill、配额、公共资料、Memory/Report相关测试继续通过。

## 6. 历史失败证据与正式库保护

原Live库只读核对前后无变化：Session1仍IN_PROGRESS、state_version6、active_question_id3；Question3、Answer3（2 COMPLETED + 1 FAILED）、正式Evaluation2；Evaluation AgentRun7成功，Main AgentRun8失败，Snapshot仍Prompt2.2.0。无处理中lease或Token预留，8次调用全部已结算80,321。

只读核对时Session已运行约4,935秒，尚未超过7,200秒；没有因此续跑，也不保证未来仍未过期。无论是否过期，后续只使用新Session，不重置旧时间预算。

只读对比文件：`.runtime/acceptance/refs_fix_history_before_20261026_a.json` / `refs_fix_history_after_20261026_a.json`，所有会话/回答/评价/快照/记忆/账本区段均一致。正式库before/after逐表指纹也一致，仍0025、64表。

保留原文件未改：

- 原8次HTTP记录SHA256：`DB6773ED21221BC3E3C35F5E5B61C4F0807703E1CF7C363836B99A2A73484164`。
- 原59文档SHA256：`70B8BBB161FD92A0FCBF861A47CBF3CEC42821C33DA51B0A515C92A70A4ACDD2`。

## 7. 80,321 Token 分析：不混淆正常与失败

| 类别 | 输入 | 输出 | 合计 | 说明 |
| --- | ---: | ---: | ---: | --- |
| Main原始请求（不含Repair） | 42,112 | 1,039 | 43,151 | 4次，其中最后1次未通过Schema |
| Evaluation | 34,007 | 1,728 | 35,735 | 3次，最后一次已验证但未正式提交 |
| Repair | 1,107 | 328 | 1,435 | 1次，失败 |
| 总计 | **77,226** | **3,095** | **80,321** | 7逻辑、8 HTTP |

第三轮尝试共23,646：Evaluation11,161 + 失败Main11,050 + Repair1,435。

必须区分：失败Main及无效Repair合计12,485未产生可提交下一题；其中明确额外发生的repair是1,435。Evaluation11,161不是应当丢弃的重复开销，它已保存可复用；后续如果安全重试，不应再评价同一答案。本轮仅在新隔离Replay Session验证该复用，不实际重试旧Live Session。

### 输入字符数与增长

| HTTP / 用途 | 实际system+user字符 | system字符 | 候选数量 | 紧凑序列化历史字符 |
| --- | ---: | ---: | ---: | ---: |
| 1 Main开场 | 23,993 | 2,909 | 29 | 0（空历史） |
| 2 Evaluation1 | 29,091 | 1,959 | 29 | 183 |
| 3 Main2 | 27,846 | 2,909 | 29 | 183 |
| 4 Evaluation2 | 31,051 | 1,959 | 29 | 363 |
| 5 Main3 | 29,649 | 2,909 | 29 | 363 |
| 6 Evaluation3 | 27,160 | 1,959 | 20 | 533 |
| 7 Main4失败 | 27,176 | 2,909 | 20 | 533 |
| 8 Repair失败 | 2,851 | 变更为受约束Repair指令 | 不重新规划 | 原错误输出与Schema |

上述history为各题简短历史投影的紧凑JSON字符，不是整个请求；空历史显示0不表示没有字段。Main Snapshot紧凑JSON约7,117字符固定，adaptive部分约10,972→13,091→14,346→12,102字符。当前样本中历史只有数百字符，候选空间、知识观察、当前回答/评价及固定上下文占大头；冷却使候选29降20，输入并非逐轮单调增长。

输入占约96.15%，reasoning返回0。不能把成本归因于长思考，也没有证据证明20轮历史一定爆炸或一定安全。只有两次完整提交，**不足以准确预测20轮总成本**。

### 15 / 20轮条件性预测

假设正常结束、最后一答无需下一题Main，则N个有效回答约需N次Main（含开场）+ N次Evaluation + 1次Coach。没有测得Coach输入用量，以C表示，不当成0。

| 轮次 | 正常均值外推（无失败） | 用已观测单项最大值的保守情景 | 该保守情景另发生一次相同Main失败并安全重试 |
| --- | ---: | ---: | ---: |
| 15 | 353,602 + C | 370,215 + C | 382,700 + C |
| 20 | 472,202 + C | 493,620 + C | 506,105 + C |

均值口径：开场9,235 + (N−1)×后续成功Main均值11,433 + N×前两次Evaluation均值12,287。保守情景用N×(Main最大11,855 + Evaluation最大12,826)。失败情景额外12,485，假设成功Evaluation可复用。

这是**条件性容量估计，不是统计置信上界**；未来上下文、回答长度或额外失败可能更高。C和长程增长未测，不给精确人民币成本。本轮没有调整Session原有200,000上限；当前样本外推的15–20轮消耗与该上限不匹配，后续长程计划必须单独评审，不能用批次650,000授权替代会话预算。

## 8. 下一轮建议：分别授权，不自动执行

### 阶段一：短程Live

- 新隔离Session，Prompt2.2.1，沿用指定DeepSeek和原预算；RAG链路→具体召回→度量/实现补充，Codex仍按实际问题动态作答。
- 目标3个有效Answer/Evaluation及其后Main决策；不固定NEXT/FOLLOW_UP，不人为篡改模型输出制造成功。
- 建议上限：**7逻辑请求、9 HTTP、120,000 Token、10分钟**。正常预计7请求；多出的HTTP只供已有的一次/调用repair使用，不再加独立规划调用。
- 每次请求先保守预留，任一硬预算或业务校验失败即停。若自然过程没发生repair，只能说Live正常引用选择通过，不能把离线注入测试写成Live repair实测。
- 核对Main引用数量、关键依据、原题语义、组合和Controller提交；检查Evaluation复用与账本，不续跑旧失败Session。

### 阶段二：长程Live

短程通过后再单独授权15–20轮，先确认输入成本、会话200,000约束和UI轮数上限不一致如何处理。继续保留第15轮检查点、最终Coach与Memory验证，绝不重置旧Session或继承上轮已经结束的650,000收费授权。

当前状态：离线修复与相关回归通过；真实DeepSeek修复效果未验证。完成后停止。
