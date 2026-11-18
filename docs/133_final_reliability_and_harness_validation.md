# R133 最终可靠性修复与 Harness 验证

本轮只处理 R132 遗留的三个问题：`INVALID_QUESTION_GAP_SCOPE`、Main Harness 未自主调用面经工具、面经抽取把 CONTEXT 当 QUESTION。

约束执行情况：

- 模型 HTTP：**0 次**（未申请新预算，未重试任何付费请求）。
- 外部抓取：0 次；展示库/正式库：**只读**，未写入。
- 未修改 Evaluation 2.3.4，未改历史会话与历史 Prompt 快照，未新增 Skill，未批量采集，未 Push。
- 未删除任何审核失败数据（携程/快手两条仍为 FAILED）。

---

## 1. 结论

| 问题 | 状态 | 说明 |
| --- | --- | --- |
| P0 `INVALID_QUESTION_GAP_SCOPE` | **已修复（零 HTTP 验证）** | 严格校验原样保留；拒绝不再把整轮判死，服务用确定性保底替换，评价与回答保留 |
| P1 Harness 自主工具调用 | **机制无阻断，模型未调用（已定位原因）** | 工具确实在 Schema 与投影中；实际原因是"面经题目已在输入里，工具边际价值低"，不是权限或代码缺陷 |
| P2 面经抽取 CONTEXT/QUESTION | **已修复（真实样本离线复算）** | 携程快照 20 条真题目此前全部被判 CONTEXT；现 20 题可用，原失败片段可解析 |

---

## 2. P0：`INVALID_QUESTION_GAP_SCOPE` 的根因与修复

### 2.1 真实记录

会话 **#638**（展示库快照 636，`main_harness=true`，Prompt `2.4.0-harness.3`）：

| 对象 | 事实 |
| --- | --- |
| run `1266` | `interview_main_harness`，`FAILED`，`error_message=INVALID_QUESTION_GAP_SCOPE` |
| step `1267` | `interview_main` 成功，11,269 token，`main_action.kind=final` |
| 选择 | `strategy=FOLLOW_UP`，`focus_gap_ids=[]`，`target=ai_agent_application:agent`，`supporting_refs=[public_question:7, answer:571, evaluation_run:1265]` |
| 评价 `1265` | `state=INSUFFICIENT_EVIDENCE`，4 条 `missing_mechanisms` → 该目标 4 个开放缺口 |
| 会话结果 | 只保存了 2 道题；第 3 道从未提交，只能人工结束 |

### 2.2 环节定位

不是"模型编造了一个不存在的 gap ID"，而是**空的 gap 范围 + FOLLOW_UP**：

1. 服务把本轮评价的 4 个缺口投影进 `adaptive.knowledge_state`，`decision_space` 因此给该目标开放 `FOLLOW_UP`。
2. Harness 投影（`lazy_main`）把 `knowledge_state.items[*]` 压成 `open_gap_count`，**不再发送 gap ID**；设计上要求模型先调用 `read_active_knowledge_gaps` 才能拿到 ID。
3. 模型没有调用该工具，直接输出 `FOLLOW_UP + []`。
4. `validate_decision` 命中既有规则：有开放缺口时 FOLLOW_UP 必须至少聚焦一个 → 422。
5. Harness 把所有 `AppError` 都当作"本轮永久终止"（`terminal=True`），服务层也只对 `ADAPTIVE_TOPIC_MISMATCH` 做保底替换，于是这一轮彻底作废。

第 4 步是**正确的**；第 5 步是缺陷。约束本身没有放宽：空范围仍然被拒，未知 ID 仍然被拒。

### 2.3 修改

| 文件 | 修改 |
| --- | --- |
| `modules/interview/adaptive.py` | 新增 `gap_scope_diagnostic()`：拒绝时记录目标、策略、提供的 focus、该目标真实开放 gap ID、题源引用，以及 `gap_ids_in_projection=INLINE/ON_DEMAND_ONLY`；新增 `RECOVERABLE_DECISION_CODES` / `SILENT_FALLBACK_CODES` |
| `modules/interview/main_harness.py` | 业务性拒绝（gap scope、topic mismatch、来源/候选不符等）不再标记 `terminal`；只有 `MAIN_HARNESS_LIMIT`、快照漂移、模型归属变化仍为永久终止 |
| `modules/interview/core_service.py` | `ADAPTIVE_TOPIC_MISMATCH` 与 `INVALID_QUESTION_GAP_SCOPE` 走同一条零模型保底：审计原决策 → 检查点保留已校验评价 → `fallback_selection` 生成合法 NEXT → 同一 Controller 事务提交 |

为什么保底范围只放这两个码：其余业务拒绝（如 `UNKNOWN_DIRECTOR_CANDIDATE`）沿用既有"报错 + 同幂等键重试、复用评价"契约，`test_v211_director_contract.py` 依赖该行为，**未改动**。

### 2.4 严格性保留证据

- `validate_gap_scope` / `validate_decision` 的判定条件一行未改。
- `tests/test_gap_scope_contract.py` 的单元级契约（非法 focus、空 focus、跨目标 gap）全部通过。
- 新增测试证明：拒绝后 **Evaluation 只跑 1 次、Main 只决策 1 次**，回答与评价保留，会话仍为 `IN_PROGRESS`，审计写入 `INVALID_QUESTION_GAP_SCOPE`，run 为 `FAILED` 且 `terminal=false`。

---

## 3. P1：Harness 为什么没有自主调用面经工具

### 3.1 工具确实被暴露

- Schema：`MainHarnessAction` 的判别联合包含 `read_active_knowledge_gaps`、`inspect_current_skill`、`inspect_job_requirement`、`search_reviewed_interview_experiences`，首轮 `schema.title=MainHarnessAction`（即允许先调工具再作答）。
- 提示：`2.4.0-harness.3`（#638）、`2.4.0-harness.4`（#639）都写明了何时该查、返回什么、为空怎么办；`.4` 还额外加了"count>0 且确有必要时先查一次"。
- 数据：#638/#639 的 `snapshot.context_availability.reviewed_experiences.count=1`，冻结可见性正常。

### 3.2 三次真实 Main 的动作

| run | 会话 | Prompt | 动作 | 工具步骤 |
| --- | --- | --- | --- | --- |
| 1265 | #638 开场 | harness.3 | `final` / `NEXT` / `requirement:153` | 0 |
| 1267 | #638 第 2 题 | harness.3 | `final` / `FOLLOW_UP` / `public_question:7` | 0 |
| 1268 | #639 开场 | harness.4 | `final` / `NEXT` / `requirement:153` | 0 |

两场会话 `main_harness_tool` 步骤数均为 0。

### 3.3 实际原因

1. **信息冗余**：这两场快照里 17 条已审核面经题目**全文已经在输入中**（`snapshot.public_questions`）。工具返回的是同一批题目的 3 条 + 轮次/可信度/来源链接，对"下一题问什么"的边际信息很小。
2. **没有强制**：提示明确"只在确有必要时查"，模型选择了直接作答；这符合设计，不应为了调用次数去强迫。
3. **一次决策即定**：模型一旦返回 `final` 就结束本轮，不会再补一次工具调用。

结论：**不是权限、Schema 或数据缺失导致工具不可达**；是"预加载已足够"的自然结果。若希望工具承担不可替代的角色，正确做法是让 Wire 不再内联全部题干（按需提供），而不是加"必须调用"的硬约束——本轮未做该改动。

### 3.4 新增零 HTTP 覆盖（`tests/test_r133_decision_recovery.py`）

| 场景 | 断言 |
| --- | --- |
| 模型主动选择工具 | 首轮返回 `kind=tool`，Harness 产生 1 条 `main_harness_tool` 步骤 |
| 工具返回真实审核面经 | `status=OK`、`code=REVIEWED_EXPERIENCES`、`trust=UNTRUSTED_DATA`，含问题与来源 ref |
| 依据工具结果生成合法问题 | 最终题的 `source_question_id` 等于工具返回的 `public_question:<id>`；`run.output_json.tool_audit.used_tool_result=true` |
| 工具为空时回退 | `EMPTY / NO_REVIEWED_EXPERIENCE_FOR_SCOPE`，模型仍可正常出题，`used_tool_result=false` |
| 直接提问不强制 | `tool_called=false`，会话正常推进 |
| Schema 暴露 | 工具名与 `topics` / `max_items` 出现在 JSON Schema 中 |

### 3.5 顺带修掉的一个真实缺陷

`reviewed_experience_scope()` 一直在读 `experience["reported_topics"]`，但 `build_context()` 从未提供该字段，导致 scope 的 topics 只能从题干词推断，`reported_topic` 证据永远为空。

现已在会话创建时把 `extraction_output_json.source_anchors.reported_topics` 的原文片段冻结进 `interview_experiences[*].reported_topics`（只复制已有抽取结果，不推断），并有单测覆盖。旧快照不受影响。

---

## 4. P2：面经抽取的 CONTEXT / QUESTION / ANSWER

### 4.1 真实失败

| 任务 | 快照 | 报错 |
| --- | --- | --- |
| 7（携程 Java） | 52 | `questions[0].fragment_id: ... fragment_id=ev_69eb5c4aa5ddd0fc0e2e15c6; source_kind=CONTEXT` |
| 10（快手 RAG） | 53 | `questions[0].fragment_id: ... fragment_id=ev_11c970e1efc35e0a960eb372; source_kind=CONTEXT` |

用改动前的代码复算快照 52：页面里 20 条真实题目（`项目介绍`、`spring的启动流程`、`谈谈线程池,工作原理`、`分库分表`…）**全部被判为 CONTEXT**，只有带"怎么/如何/?"的 5 条成为 QUESTION。模型按语义把它们当题目引用，于是被证据契约驳回——模型的判断是对的，分类器太窄。

快照 53 的问题写在 markdown 标题里（`## 2. RAG 有没有解决大模型胡说八道的问题`），而旧代码对所有 `#` 开头的行一律不判 QUESTION。

### 4.2 修改（`public_career/research/fragments.py`）

1. **编号标题即题目**：`## 1. 自我介绍` 这类编号标题参与判定；h1 页面标题仍为 CONTEXT，且首行永不提升。
2. **分区列表**：进入"一面/二面/笔试/HR面"分区后，短行（≤80 字、无句号、非页面控件、非状态行、非第一人称叙述）判为题目；分区外则要求邻近 3 行内存在明确问句。
3. **ANSWER 类型**：`答案：` 起始，以及答案块内的正文，标为 ANSWER，不再混作 CONTEXT，也**不能**被当成题目引用。
4. **散文守卫**：含句号且不以问号结尾、或长度 >120 字的行不判题目（避免"系统有没有召回正确证据……"这类解释被当题）。
5. **页面控件/状态词**：浏览、评论、畅所欲言、收到意向书、日期行等不参与提升。
6. `question_span()` 现在只接受 QUESTION（或 kind 匹配的 REPORTED_TOPIC），CONTEXT/ANSWER 一律拒绝，错误里带原文片段便于复核。

### 4.3 抽取与入库行为

- `resolve_fragment_extraction()` 不再因单条不支持而丢弃整篇：不支持的条目被**丢弃并记录**到 `source_anchors.rejected_questions`（含 fragment_id、声称类型、原因）；若全篇没有一条可用，仍以 `No question is supported by the cited source text` 失败。
- 抽取提示升级到 `interview_extract 1.3.0`（`1.2.0` 保留），明确 CONTEXT/ANSWER/QUESTION 的边界、列表式题目可引用、付费预览不越界推断。
- 片段上限 150 → 400（仍受 24,000 字符预算约束），避免长文后半段的题目被静默截断。

### 4.4 真实样本复算（`data/r133_audit/fragment_recheck.json`）

| 快照 | 改动后 | R132 被拒片段 |
| --- | --- | --- |
| 52 携程 | QUESTION 20 / CONTEXT 19 | `ev_69eb…`（项目介绍）→ 现为 QUESTION，解析成功 1 题，0 丢弃 |
| 53 快手 | QUESTION 8 / ANSWER 206 / CONTEXT 2 | `ev_11c9…`（RAG 有没有解决…）→ 现为 QUESTION，解析成功 1 题 |

快照 53 第 8 题之后出现"剩余60%内容，订阅专栏后可继续查看"，**未**从付费预览推断任何隐藏题目；可核验的题目就是 8 条。

---

## 5. 回归结果（零模型 HTTP）

命令：

```bash
apps/api/.venv/Scripts/python.exe scripts/run-workbuddy-tests.py \
  --database interview_agent_wb_r133f_test_20261118 --skip-migration -- <14 个面试相关文件>
```

| 指标 | 结果 |
| --- | --- |
| 通过 | **142** |
| 失败 | **7（全部为既有失败，非本轮引入）** |
| 跳过 | 1 |
| 数据库 | 全新 `interview_agent_wb_r133f_test_20261118`，迁移至 head |

7 个既有失败（`git stash` 去掉本轮改动后，在两个测试文件上以全新库重跑，仍是同样 7 个失败）：

- `test_main_harness.py::test_frozen_gap_read_survives_mid_loop_failure_and_same_key_retry[feedback_echo0/1]`
- `test_gap_scope_contract.py::test_original_failure_through_service_freeze_retry_and_memory[legacy/scoped/multi_focal]`
- `test_gap_scope_contract.py::test_scoped_twenty_round_replay_completes_coach_and_next_memory[False/True]`

失败点在测试自身的候选查找（`next(c for c in candidates if ... skill: ...)`）拿到 `StopIteration`，属于 R130 之后蓝图来源变化的陈旧夹具假设，与本轮三个修复无关。按要求"只针对这三个问题"，未改动。

新增测试全部通过：

- `test_r133_decision_recovery.py`：7 项（4 项 DB 集成 + 3 项纯单元，含 reported_topics 冻结）
- `test_r133_fragment_classification.py`：7 项

前端本轮未改动，未跑 `vue-tsc`。

---

## 6. 下一次最小 Live 验收预算

只做"证明修复在真实模型上成立"的最小集合，不做批量：

| 项目 | 次数 | 说明 |
| --- | --- | --- |
| Main（harness，3 道正式题） | 3 | 其中至少 1 次应出现 `kind=tool` |
| 工具回环追加的 Main | ≤2 | 每次工具调用后模型需再决策一次 |
| Answer Evaluation | 3 | 每题 1 次（Evaluation 2.3.4 不变） |
| Coach 报告 | 1 | 面试结束后 1 次 |
| 面经抽取复测（快照 52/53 复用，无需搜索/抓取） | 6 | 每篇 extract + routing + reliability |
| **合计** | **≤15 次** | 不含任何重试；失败即停 |

Token 预算（按 R132 实测单价外推）：

- Main 约 11k/次 × 5 = 55k
- Evaluation 约 6k/次 × 3 = 18k
- Coach 约 7k
- 抽取 3 步约 3k/篇 × 2 = 6k
- **合计约 86k，建议上限 120,000 token**

若只验 P0/P1（不重跑抽取），最小集合为 **9 次调用 / ≤75k token**。

前置条件：一次验收库（独立于展示库与正式库）、真实审核面经 ≥1 条且精确关联目标岗位；若不足，P1 的工具只会返回 EMPTY，无法证明"模型用了工具结果"。

---

## 7. 改动清单与 Git 状态

修改：

- `apps/api/src/interview_api/modules/interview/adaptive.py`
- `apps/api/src/interview_api/modules/interview/main_harness.py`
- `apps/api/src/interview_api/modules/interview/core_service.py`
- `apps/api/src/interview_api/modules/interview/context.py`
- `apps/api/src/interview_api/modules/public_career/research/fragments.py`
- `apps/api/src/interview_api/modules/public_career/research/interview.py`
- `apps/api/src/interview_api/modules/public_career/research/prompts.py`

新增测试：

- `apps/api/tests/test_r133_decision_recovery.py`
- `apps/api/tests/test_r133_fragment_classification.py`

证据脚本与数据（未入库）：

- `data/r133_audit/verify_fragments.py`、`fragment_recheck.json`

未包含：`.env`、任何密钥、原始用户数据、数据库备份、临时日志。

R127～R132 的提交仍在（`2010d81` → `849f632` → `6c32b23`），本轮在其之上追加一个本地提交，未 Push。
