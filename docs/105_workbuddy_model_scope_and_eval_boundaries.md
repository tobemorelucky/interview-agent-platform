# 第 26 轮：模型账本归属、引用 Repair 与五维评分准则

- 基线 HEAD：`55d97aa`（开始前工作区无未提交的已跟踪改动）
- 真实模型 HTTP：**0**（全部离线；任何"评分"结论都来自 Replay/脚本化回复，不是新模型输出）
- 受保护资产零改动：正式库 `0025`（19/41/5/0）、18 轮原件 `0026`（3/18/1/49）、JD 快照库 `0026`（1621/1761/219/147）、MinIO 主 bucket 48 对象、三闸仍关闭
- 新增隔离库：`interview_agent_wb_r26_test_20261115`（0029/68）、`interview_agent_wb_r26_alt_20261115`（跨库拒绝用）、`interview_agent_v21_r26_20261115`、`interview_agent_v21_r26b_20261115`（迁移空库）
- 全量回归：**1003 passed / 0 failed / 1 skipped**（业务）+ **12 passed**（迁移）= **1015 passed / 0 failed / 1 skipped**

---

## 1. P0：账本归属与数据库路由

### 1.1 先纠正上一轮的错误结论

上一轮 `docs/104 §7.2` 写的是"`modules/interview` 与 `api/` 都没有设置 `model_scope`"。**这是错的。**

正式评价走 `ResearchModelCalls`（`modules/public_career/research/audit.py`），它在 `getattr(provider, "governed", False)` 为真时会：

```python
owner, uid = await run_owner(self.factory, self.run_id)   # 从 session/question/answer 的 FK 反查
with model_scope_for(...):                                # 第 26 轮换成上下文管理器
    await generate_structured(...)
```

`run_owner()` 通过 `InterviewAgentCall → InterviewSession.user_id` 反查归属，route 是 `answer_evaluation`。链路本身是对的。

上一轮账本出现 `legacy_llm` 与 owner 错位，根因是**我写的 `GuardedEvaluation` 包装类没有暴露 `governed` 属性**：audit 因此跳过了 scope 设置，网关落到回退分支，而回退分支用的是我自己在开会话时设置的那个（会被同一对第二场覆盖的）scope。这是夹具缺陷，不是产品缺陷。

### 1.2 但回退分支本身确实是缺陷

`governed_call()` 原来的开头是：

```python
scope = model_scope.get()
if scope is None:
    scope = (async_session_factory, f"user:{uid}", uid, "legacy_llm")   # 静默兜底
```

三个真实问题：

1. **无提示地换库**：兜底用的是 `interview_api.infrastructure.db.session.async_session_factory`，即 `DATABASE_URL`。夹具指向 sandbox 业务库、进程仍是正式库配置时，账本会写进正式库。
2. **无提示地换 route**：`legacy_llm` 让"按 route 统计评价成本"永久失真。
3. **归属点分散且可被覆盖**：scope 由各调用方零散设置；一旦某个调用方在"建会话"阶段设置、在"提交回答"阶段使用，就会被同一进程内另一个用户的设置覆盖（第 25 轮实际发生）。

### 1.3 修复

新增 `infrastructure/models/scope.py`：

| 元素 | 作用 |
|---|---|
| `ModelScope(factory, owner, user_id, route, ledger_database)` | 一次调用所需的全部归属信息，`ledger_database` 在绑定作用域时从工厂实际 bind 解析 |
| `model_scope_for(factory, route=..., user_id=/owner=, ledger_database=?)` | 上下文管理器；`ContextVar` 在进入时设置、退出时 `reset`，并发任务互不覆盖 |
| `require_scope()` | 没有作用域就 `MODEL_SCOPE_REQUIRED`（403），**不再兜底** |
| `expected_ledger_database()` | 进程声明的账本库：`MODEL_LEDGER_DATABASE`，为空则取 `DATABASE_URL` 的库名 |
| `assert_ledger_target(scope)` | 作用域库名 ≠ 进程声明 → `MODEL_LEDGER_TARGET_MISMATCH`（409），**在预留之前失败，0 费用** |
| `ledger_factory(db)` | 有 session 时用该 session 的 engine 建短事务工厂；无 session 才用进程工厂 |

接入点：

| 位置 | 改动 |
|---|---|
| `governed_call()` | 删除 `legacy_llm` 兜底；改为 `require_scope()` + `assert_ledger_target()` |
| `MockInterviewService.call_agent()` | **每次 agent 调用**包一层 `model_scope_for(self.factory, user_id, route=agent.route)`，再进原逻辑。包装类是否暴露 `governed` 都不再影响归属 |
| `ResearchModelCalls` | 手写 `set/reset` 换成 `with model_scope_for(...)`（仅 governed provider 时） |
| `JobTargetService.extract()` | 用户态 `user:{id}`；无用户时显式 `system:job-target-extract`；工厂取 `self.db` 的 bind；不再用 `legacy_llm` |
| `OpenAICompatibleLLMProvider.chat()` | 三档显式作用域：`resume_report` / `legacy_public_extraction` / `legacy_chat`；三者都不成立则 `MODEL_IDENTITY_REQUIRED`（403） |
| `context_projection.TERMINAL_CODES` | 加入 `MODEL_SCOPE_REQUIRED`、`MODEL_LEDGER_TARGET_MISMATCH`：配置类错误不可重试，不给"重试"提示 |
| `tests/conftest.py` | 测试进程用 `M1_TEST_DATABASE_URL` 显式声明自己的账本库（`declared_ledger_target`），不让测试依赖 `DATABASE_URL` 恰好一致 |

最终不变量：
- 每个真实请求都有 `user_id` 或显式 owner、route、以及一个**与本进程声明一致**的账本库；
- 归属信息只存在于当前任务的 `ContextVar` 中，退出即恢复；
- 缺归属 → 403；库不一致 → 409；两种都在发送前失败；
- 正式 API 与 Worker 路径保持可用（见 §1.4 兼容矩阵）。

这套"先解析、再绑定、再核对、全程不建立连接"的做法沿用 `core/runtime_target.py` 的第 16 轮教训（当时 API 只校验了 `--database` 的名字，实际引擎仍来自 `.env`，于是服务读写了正式库）。模型账本是同一类错误，因此用同一套形状修，而不是另创一套。

### 1.4 兼容矩阵

| 路径 | 归属来源 | 结果 |
|---|---|---|
| 面试 create/start/answer（API 与 Worker） | `call_agent` 里的 service 级 scope | `user:{uid}` + `answer_evaluation`/`interview_main` |
| 简历画像抽取 | `ResearchModelCalls` + `run_owner` | `user:{uid}` |
| 公开数据研究 / Coach / 岗位匹配 | 同上（`run_owner` 走任务 FK） | 用户态或 `system:public-career` |
| 旧 SDK 兼容 provider | `chat()` 内显式三档 | `resume_report` / `legacy_public_extraction` / `legacy_chat` |
| 私有 JD 解析（有用户/无用户） | `extract()` 内显式 | `user:{id}` / `system:job-target-extract` |
| 外部夹具（第 25 轮脚本） | 服务自动绑定；脚本只声明 `MODEL_LEDGER_DATABASE` | sandbox 库；指向别处即 409 |

历史账本**未重写**：第 25 轮那 8 条 admission 的 owner 仍保持当时写入的值，只在 `docs/104` 勘误与本文件说明。

---

## 2. P1：`answer_quotes` 上限导致的额外 Repair

### 2.1 核实结果

| 问题 | 事实 |
|---|---|
| Prompt 是否限制引用数量 | **没有**。2.3.3 只写"short EXACT CONTIGUOUS excerpts"，没给条数 |
| Schema 上限语义 | `KnowledgeObservation.answer_quotes: list[ShortText] = Field(max_length=3)`，超 3 条是 `too_long`，不是内容错误 |
| Repair 机制 | `EVALUATION_QUOTE_SUBSET_V1`：只在"唯一错误是 answer_quotes 过长、且每条都是回答原文的连续子串"时触发；第二次请求只允许**从原有引用里选子集**，`validate()` 断言除引用外所有字段逐字不变 |
| 这 5 条是否合法 | 合法。修复记录 `original_count=5, retained_count=3`，`prepare()` 只有在"每条都是 `payload['answer']` 的子串"时才返回计划 |
| 是否存在多条引用支持同一结论 | 存在。KF-A1 的回答里"同一事务写去重表""处理成功后手动提交 offset""重放窗口靠幂等键兜住"三条各自支撑不同结论，但模型也给了两条同义引用，**无法在不看结论的情况下确定哪条是唯一支撑** |

### 2.2 为什么最终选择"改 Prompt、保留 Repair"

- **不能确定性裁剪**：删除任意一条都可能删掉某个结论的唯一证据；保留哪三条需要语义判断，不是确定性规则。
- **不能直接提高上限**：`ResolvedObservation.answer_spans` 也是 `max_length=3`，且 `answer_spans` 由 `answer_quotes` 逐个映射生成（`adaptive.py:296`）。把 3 改成 5 会级联修改观测结构与证据语义，属于"放宽正式 Evidence 校验"，超出本轮最小改动范围。
- **改 Prompt 是对症的**：模型不知道上限才给 5 条。2.3.4 明确写"at most 3 ... chosen to support conclusions you actually draw. Never pad to a fixed count."

保留 Repair 作为兜底：模型仍超额时，安全路径仍在；非法引用（改写、杜撰、越权）仍被拒绝，不会被"裁掉"。

### 2.3 离线重放（无模型）

原始 5 条引用**没有被持久化**（`step.output_json` 只存 `original_count/retained_count`），所以证据用第 25 轮真实回答 `KF-A1` 的原文重建了 5 条逐字引用。这一点必须说清楚：这是**用捕获的回答重建的合法超量案例**，不是原始模型输出的字节级回放。

`apps/api/tests/test_r26_quote_subset_offline.py`（5 个用例）证明：

| 场景 | 结果 |
|---|---|
| 5 条合法引用 | 触发 `too_long`；`prepare()` 返回 5 条候选、`max_items=3`；修好后 `retained_count=3` |
| 修复前后 artifact 对比 | 除 `answer_quotes` 外**逐字相同**（评分、评语、缺口、follow-up 全部不变） |
| 其中一条是改写（非原文） | `prepare()` 返回 `None` → 不进入修复，错误如实抛出，不会静默丢弃 |
| 修复时顺手改分数 | `validate()` 抛 `ValueError` |
| 修复时塞入未在候选内的引用 | `validate()` 抛 `ValueError` |
| 3 条引用 | 直接通过，零修复 |

---

## 3. P1：五维评分准则

### 3.1 变更形态

- 新增 `EVALUATION_BOUNDARIES_PROMPT`，版本 **2.3.4**，内容 = 2.3.3 全文 + 边界契约。
- 新会话在 `adaptive_policy` 里冻结 `evaluation_boundary_contract="FIVE_DIMENSION_BOUNDARIES_V1"`。
- 旧会话没有该键、仍是 2.3.3，**按 2.3.3 读取**；混合（声明契约但冻结 2.3.3，或反之）一律 `EVALUATION_BOUNDARY_UNSUPPORTED` / `CONTEXT_PROJECTION_PROMPT_MISMATCH`，不做"自动升级"。
- 五维字段名、1..5 取值、数据库 schema、`interview_evaluations` 列**都没有变**。

### 3.2 新旧准则差异

| 维度 | 2.3.3（旧） | 2.3.4（新） |
|---|---|---|
| technical_correctness | 依赖 Skill 锚点一句定性描述 | 明确"只看**陈述是否准确**，遗漏不算技术错误"；给出 5/4/3/2/1 的可操作描述（5=全部准确；3=一处非致命不准确；1=核心机制错误） |
| coverage | 无 | 5=全覆盖 … 1=几乎没有；并写明"coverage 永不降低 technical_correctness" |
| depth | 无 | 5=理由+失败边界+验证方式；并写明"A coverage gap lowers depth **only when** the missing part is a reason, a boundary or a validation step" |
| relevance | 无 | 明确"fluency or length never raises relevance" |
| communication | 无 | 按结构与因果分档 |
| 多维度同时扣分 | 无规则（实测出现同一理由扣两次） | 允许同一缺陷影响多维，但**每个扣分必须有本维度的独立理由**，不得换个名字重复 |
| 五维是否必须不同 | 无说明（实测中间档五维同步 +1） | 明确"不要强行让五个分数不同，也不要禁止相同" |
| 错误严重度 | 无 | 核心机制错误 → tc ≤2 且 depth=2；次要细节错误 → tc=3 且不单独压 depth；纯遗漏只压 coverage（必要时压 depth） |
| PARTIAL / NEAR_MISS | 只有状态定义 | 追加判据：遗漏但不含错误机制 → PARTIAL；含一处明确错误机制 → NEEDS_CLARIFICATION；两者同时出现则按技术错误计分、遗漏写入 missing_points |
| Kafka 边界 | 无 | 至少一次语义 / 幂等 producer 仅生产端 / Kafka 事务只覆盖 Kafka 内部读写 / 自增主键无法去重业务消息 / offset 只有 seek 与重置，没有事务性回滚 |
| LangGraph checkpointer | 无 | 无状态图可选、但在持久化与恢复语境下是机制而非调试选项；快照写在 super-step 边界 |
| 措辞绝对化 | 无约束 | 要求"说清作用域或没有回滚 API"，不许一刀切"cannot" —— 直接对应上一轮"Kafka 不支持 offset 回滚"的过度绝对 |
| 引用条数 | 未提 | 最多 3 条，且不得为凑数而引用 |

Skill 的 `evaluation_anchors` **未改**：它仍是专业视角（"这个岗位怎么看这五个维度"），数值阶梯是跨 Skill 通用的，放在 Prompt 里才不会让每个 Skill 各写一套阈值。

---

## 4. 离线对照测试（A–J）

| 编号 | 场景 | 用例 | 结果 |
|---|---|---|---|
| A | 技术正确、简短但不完整 | `test_r26_evaluation_boundaries::test_new_sessions_freeze_the_boundary_prompt_and_its_contract` + Prompt 断言 "Omission alone is not a..." | ✅ 契约冻结、文本含该约束 |
| B | 长篇但核心原理错误 | 同上（severity 规则）+ `test_scoring_schema_stays_five_dimensions_1_to_5` | ✅ 规则存在，schema 未变 |
| C | PARTIAL vs NEAR_MISS | 同上（PARTIAL/NEAR_MISS 判据断言） | ✅ |
| D | Kafka exactly-once 边界 | 同上（`no transactional "offset rollback"`、外部系统边界） | ✅ |
| E | checkpointer 可选性 | 同上（`optional for stateless graphs`） | ✅ |
| F | 5 条逐字引用 / 上限 3 | `test_r26_quote_subset_offline::test_five_legal_quotes_trip_the_cap_and_are_trimmed_without_side_effects` | ✅ 安全裁剪、其余字段不变 |
| G | 非法引用 / 引用不支持评价 | 同文件 `paraphrased_quote` / `invented quote` 两个用例 | ✅ 拒绝而非静默删除 |
| H | 两个用户并发产生不同 owner | `test_r26_model_scope_and_ledger::test_two_owners_in_flight_keep_their_own_attribution` | ✅ 2 条 admission、owner 各自独立、均 SETTLED |
| I | API 与 Worker 目标不一致时拒绝 | `test_a_scope_pointing_at_another_database_is_refused` | ✅ 409，两个库都没有写入 |
| J | 未设置 scope 时防止写正式库 | `test_missing_scope_is_refused_before_anything_is_sent` | ✅ 403；并把 `db.session.async_session_factory` 换成"一调用就报错"的函数，证明回退路径已不存在 |

补充：`test_r26_service_scope` 用官方 `create → start → answer` 路径 + governed 桩，断言两条回答分别落在 `user:{uid}` / `answer_evaluation`，这正是第 25 轮出错的场景。

---

## 5. 兼容性与历史数据

| 项 | 结论 |
|---|---|
| 旧会话的评分 | 快照里仍是 2.3.3 + 其指纹；`expected_evaluation_prompt()` 返回 2.3.3，读取路径不变 |
| 旧评价记录 | 未修改、未重算；`docs/104` 的勘误只改文档 |
| 旧账本 | 未重写第 25 轮 8 条 admission |
| 新会话 | 冻结 `FIVE_DIMENSION_BOUNDARIES_V1` + 2.3.4（DB 实测：`FIVE_DIMENSION_BOUNDARIES_V1 \| 2.3.4 \| 2.3.3`） |
| 中间态（声明契约但 2.3.3） | 拒绝：`CONTEXT_PROJECTION_PROMPT_MISMATCH` |
| 评分字段与范围 | 未变（`AdaptiveEvaluation` 仍是五维 1..5） |
| 正式 API / Worker | 回归 1015 全绿 |

---

## 6. 测试与回归

| 套件 | 结果 |
|---|---|
| 业务回归（`interview_agent_wb_r26_test_20261115`） | **1003 passed / 0 failed / 1 skipped** |
| 迁移专项（`interview_agent_v21_r26b_20261115`，空库） | **12 passed / 0 failed** |
| 合计 | **1015 passed / 0 failed / 1 skipped** |
| 本轮新增用例 | 15 个（归属 5、边界 5、引用 5） |
| 本轮修改的既有用例 | 4 个文件迁到 `model_scope_for`（`test_v13_shared_governance`、`test_request_safety`、`test_evaluation_quote_reliability`、`test_jd_extract_route_attribution`）+ `test_resume_extraction_budget` 补 `user_id` |

过程中修掉的两个自身问题（记录在案，因为都属于"测试替身撒谎"）：
1. `available_factory` 是包装函数，没有 bind，`database_of` 无法解析 → 给 `model_scope_for` 增加显式 `ledger_database=` 参数（仍要与进程声明一致，不能借此换库）。
2. 并发用例用了固定 owner，多次运行累积旧行 → 改成每次随机 owner。

---

## 7. 下一轮真实模型短程方案（仅方案，未执行）

目标不是"证明分数变好"，而是验证新准则下**扣分理由是否与维度职责一致**，并与第 25 轮同题同答做前后对照。

- **样本**：4 对 = 8 份回答，全部走 `commit_answer`：
  1. RAG：`RAG-B1`（88 字 GOOD_SHORT）vs `RAG-A2`（316 字，6 处明确错误）——**与第 25 轮完全同题同答**，用于前后对照"简短正确是否还被扣 tc"；
  2. LangGraph：`LG-B2`（116 PARTIAL）vs `LG-C1`（189 NEAR_MISS）——**同题同答**，用于对照"中间档是否还五维同步"；
  3. Kafka：`KF-C1`（157 NEAR_MISS，含外部 EOS 边界错误）vs `KF-A2`（253，多处错误）——测 exactly-once 边界与自增主键；
  4. RAG：`RAG-C1`（139 NEAR_MISS）vs `RAG-C2`（157 POOR_CONFUSED）——补 RAG 中间档。
- **预算**：8 次评价 + 最多 4 次合法修复 = **HTTP 12**；Token 上限 **60,000**（按第 25 轮 3.8–4.1K/次 + 修复合计估算 ≈46K，留 30% 余量）；时间 15 分钟。
- **控制**：同题同答同模型同冻结上下文（rubric 逐字一致）；两个一次性用户共享同一 JD；质量标签不入 payload。
- **记录**：每份的五维、评语、`covered_points/missing_points/issues`、引用条数与 `structured_retry`、实际 HTTP/Token/延迟、账本 owner/route。
- **判定**：只看三类可核对的事实 ——（a）tc 扣分能否在评语里找到"准确性"理由；（b）coverage 缺口是否还出现在 tc 理由里；（c）同一对是否仍五维同步变化。**不宣称统计显著**：n=4 对只够看方向与失败模式。

---

## 8. Git 与工作区

见交付说明；本轮按关注点分别提交，未 push / merge / rebase / reset / clean。
