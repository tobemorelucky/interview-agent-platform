# 第 24 轮：Evaluation 正式链路修复与评测集 V2

- 基线 HEAD：`14689ea`；本轮提交：`49dec9d`、`d9f1e3f`、`02aa726`、`b021d35`
- 真实模型 HTTP：**0**（本轮全部经 Replay/Mock 边界；账本中的 provider 均为 `replay` 与既有 stub 名）
- 功能冻结未变：不新增 Agent、不启用 Coach Harness、不推进 L3/L4、不 push/reset/clean
- 受保护资产零改动：正式库 `0025`（19/41/5）、18 轮原件 `0026`（3/18/1/49）、JD 快照库 `0026`、MinIO 主 bucket 48 对象

## 1. 两个 P1 的根因与修复

### P1-1 `context_projection.py` 的 StopIteration

**缺失对象**：`interview_session_questions.candidate_refs_json.adaptive_decision.target_knowledge_id`
在 `context_json.adaptive_blueprint.targets[]` 中不存在时，评分投影无法解析「当前考察目标」。

**触发条件**（可复现）：持久化题目的 target 与场次冻结蓝图不一致。第 24 轮的实际触发是测试夹具把**另一场** session 的
target 写进了本题；真实等价场景包括：夹具/迁移引入的历史题、蓝图重建后旧题复用、人工修数。

**原行为链**：`next(t for t in blueprint['targets'] if ...)` → `StopIteration` → PEP 479 在协程边界转成
`RuntimeError: coroutine raised StopIteration` → `code` 退化为 `"RuntimeError"`、`retryable=True`（`stream()` 的判据是
「非 AppError 或 status != 404」）、消息里没有任何 id，无法区分「数据不一致」与「provider 故障」。

**修复**（`frozen_target()` / `frozen_by_id()`）：

| 情况 | 错误码 | HTTP |
|---|---|---|
| 缺少 active target | `CONTEXT_ACTIVE_TARGET_REQUIRED` (409) | 0（投影早于预授权） |
| target 不在冻结集合 | `CONTEXT_TARGET_NOT_FOUND` (409) | 0 |
| target 存在但缺 `sources` | `CONTEXT_TARGET_SOURCES_MISSING` (409) | 0 |
| 候选组合引用非法 target | `CONTEXT_CANDIDATE_TARGET_NOT_FOUND` (409) | 0 |

- **不静默替换**：找不到就失败，绝不退化为「任意一个 target」——那会用错 rubric 评分而记录仍像合法。
- **不误触发重试**：四个码进入 `TERMINAL_CODES`，`stream()` 的 `retryable` 对它们返回 `False`。
- 回归：`apps/api/tests/test_context_projection_target_refs.py`（6 单测 + 2 集成）。集成用例复刻了实际故障：
  篡改 Q1 的 target 后调用真实评分入口，断言 `provider.calls` 不增加、无评价落库、`state_version` 不变。

### P1-2 `job_requirement_id` 类型/引用错误

**根因**：`user_job_targets.requirements_json` 里的 `id` 是外部标签 `"R2"`，`target_requirements()` 原样复制进冻结
快照；模型照抄 `"R2"` 进 `job_requirement_id`，而 `QuestionArtifact.job_requirement_id: int | None` 在**模型调用之后**
才校验，于是 4,945 tokens 已花掉、业务失败。

**修复**（`modules/job_target/requirement_refs.py`）：

1. `normalize_external_requirements()`：导入期一次性映射 `R1→1`，把标签保存在同行的 `external_ref`，返回 `{label: id}`
   映射表供审计。**全数字字符串（`"2"`）拒绝**——无法区分标签与身份，猜测即事故；**混合整型/字符串拒绝**。
2. `validate_requirement_refs()`：运行期只校验不转换。非正整数 → `REQUIREMENT_REF_TYPE_INVALID`；重复 →
   `REQUIREMENT_REF_DUPLICATE`；不在允许集合或记录的是另一版 JD 指纹 → `REQUIREMENT_REF_OUT_OF_SCOPE`。
3. 接入点：`context.py::target_requirements()`（私域）在**建会话时**即拒绝；公共 JD 路径补 `job_description_version_id == jd.id`
   断言（防后续放宽查询而跨版本绑定）。
4. 拒绝发生在 `build_context`，早于任何模型调用；测试断言 `interview_sessions` / `experience_agent_runs` /
   `model_admissions` 计数在失败前后完全不变（即 provider 调用数 0）。

回归：`apps/api/tests/test_requirement_reference_contract.py`（9 单测 + 1 集成，含「映射后建会话成功且 `external_ref` 落库」）。

## 2. 正式 Evaluation 测试路径

链路（全部是产品自己的路径，无直接 INSERT）：

```
MockInterviewService.create  → SessionController.create     冻结 Snapshot（含 blueprint / 预算）
MockInterviewService.start   → validate_decision → SessionController.start   题目由 Controller 落库
MockInterviewService.answer  → prepare_answer → Evaluation Agent → commit_answer
                                                                    ↳ InterviewEvaluation 行 + 状态推进 + 完成判定
```

夹具 `apps/api/tests/eval_pair_support.py`：

| 组件 | 作用 |
|---|---|
| `FixedQuestionReplay` | 用数据集的固定题面/要点，但仍从**本场合法候选空间**选题，`validate_decision` 照常校验 topic/source/difficulty |
| `ScriptedEvaluation` | 返回一份显式评分 artifact，并记录收到的 payload（用于验证盲测与落库一致） |
| `seed_case` | 一份已发布 JD + 公司（**不含用户**） |
| `seed_user` | 一次性候选人（简历 + 真实 Profile Agent 生成画像） |

**为什么成对评测必须是两个用户而不是同一用户两场**（本轮实测发现，已写入代码注释）：
`build_context` 恒定调用 `CapabilityMemoryService.refresh()` 并冻结 `memory_guided_plan`；会话完成时 `complete()` 必然
重新投影该用户的 capability memory。同一用户跑第二场时，刚被验证的 topic 会被降权
（`deprioritized_topics`）；在 `question_count=1` 下蓝图 `limit=1`，该 topic 甚至可能**整个掉出蓝图**，导致评分目标
不存在。因此 GOOD / POOR 各用一个一次性用户，共享同一份 JD 与同一题面 → 冻结上下文等价、历史互不污染。

**提交一次回答会额外触发什么**：

| 场景 | 模型请求 |
|---|---|
| `question_count=1`，唯一一题 | 仅 Evaluation 1 次（Controller 直接 COMPLETE，不再请求下一题） |
| 多题会话的非末题 | Evaluation 1 次 + Main 下一题 1 次 |
| 重复提交同一 answer（同幂等键） | 0 次（`reusable()` 复用已验证 artifact，实测确认） |
| 评价失败后重试 | 1 次（失败未落评价，同幂等键恢复） |

**不会**自动触发 Coach（`final_interview_reports` 由独立接口生成，测试断言为 0）；**会**在会话完成时投影该用户的
capability memory——这是既有产品行为，测试断言新记忆行只可能属于本夹具用户（`user_id NOT IN (...)` 为 0 行）。

## 3. 评测集 V2

`08_V2_题目与评分要点.jsonl`（3 题）+ `09_V2_评测样本.jsonl`（18 份，9 对）：

| 层 | 样本 | 说明 |
|---|---|---|
| GOOD_FULL | 3 | 完整且正确（≈229–321 字） |
| GOOD_SHORT | 3 | 短而正确（75–88 字） |
| PARTIAL | 3 | 主要正确、缺重要细节 |
| NEAR_MISS | 3 | 大面积正确、仅 1 处关键技术错误 |
| POOR_LONG_WRONG | 3 | **最长**且含 4–6 处明确错误 |
| POOR_CONFUSED | 3 | 明显概念混淆（阶段/术语混用） |

长度控制（9 对全部有对照，比例为「低质量侧 ÷ 高质量侧」）：

| 对 | 高质量侧 | 低质量侧 | 比值 | 类型 |
|---|---|---|---|---|
| RAG-A | 259 | 316 | 1.22 | 反向（差答案更长） |
| RAG-B | 88 | 113 | 1.28 | 反向 |
| RAG-C | 139 | 157 | 1.13 | 接近 + 略反向 |
| LG-A | 321 | 276 | 0.86 | 接近（差 ≤15%） |
| LG-B | 88 | 116 | 1.32 | 反向 |
| LG-C | 189 | 144 | 0.76 | 常规（唯一未受控） |
| KF-A | 229 | 253 | 1.10 | 反向 |
| KF-B | 75 | 65 | 0.87 | 接近（差 ≤15%） |
| KF-C | 157 | 170 | 1.08 | 反向 |

→ **8/9 对**受控：6 对为严格反向（差答案比好答案长 1.08–1.32 倍），2 对长度差 ≤15%；仅 LG-C 仍为常规方向。
上一轮是 1/3 受控，本轮明显改善。

契约测试对该条件的断言为「|比值−1| ≤ 0.20 **或** 比值 > 1.05」，并要求反向对 **≥ 4**——只断言「至少 8 对」而不
排除「好答案永远更长」的话，这条测试会退化成一个恒真的空断言。

每份样本含：`sample_id` / `pair_id` / `question_id` / `quality_label` / `char_count` / `human_check_points` /
`errors[{claim, why_wrong}]` / `expected_band` / `expected_relation`，以及仅供盲测的 `blind_context`
（**只有** `question_id` + `answer_text`）。契约测试 `test_r24_v2_dataset_contract.py`（8 项）断言：
标签/分带/参考要点/错误清单不得进入 blind payload、每对一高一低且分带不重叠、错误必须给出正确解释、
`char_count` 与 `sha256` 与正文一致、题目主题必须是该 skill 真正能问的 `priority_topics`。

## 4. 上一轮结论的人工复核（**必须纠正**）

第 24 轮报告写过「13/13 关键错误全部准确指出」。逐条复核原文后，该结论**过度绝对**，纠正如下。

### 4.1 Kafka 自增主键：**结论对，推理错**

原文（G3 POOR 评语）：「数据库自增主键无法对业务消息去重，**因为同一消息重投时主键相同会插入失败**，但不同消息也会
得到不同主键，无法识别业务幂等键。」

- 「结论」正确：自增主键不能做业务去重。
- 「理由」错误：自增主键由数据库生成，同一条消息重投会得到**新的** id，插入**成功**（产生重复行），根本不会「主键相同
  插入失败」。只有把业务键（订单号+事件类型）设为主键/唯一约束，重投才会冲突。

→ 该条只能算「结论正确、推理不成立」。这是模型在**解释错误原因**时编造机制的实例，属真实质量问题。

### 4.2 LangGraph checkpointer：**判断对，措辞过度绝对**

原文（G2 POOR 评语）：「**否认 checkpointer 的必要性**……与 checkpointer 持久化与断点恢复的实际机制相反。」

- 事实：LangGraph 的 checkpointer 是**可选**的，不挂载也能跑图；它只在需要持久化/断点恢复/人工介入时才必要。
- 因此「不需要 checkpoint」单独看并**不是错话**；该 POOR 的真正错误是「进程重启重跑一遍即可」——把重跑当成恢复，
  且无视多轮上下文。模型抓对了方向，但把「可选」说成「与之相反」，措辞过度绝对。

→ 判为「方向正确、表述不严谨」，不能当作精准纠错计入。

### 4.3 RRF 排名倒数求和：**正确**

G1 评语与 `missing_points`（「k 越小越偏向头部排名」）均与事实一致；模型自己的 `reference_answer` 也给出
`1/(k+rank)`、k≈60 与「不依赖原始分数尺度」的正确表述。**无需纠正。**

### 4.4 Kafka exactly-once 对外部系统的边界：**上一轮根本没测**

第 24 轮的三个 POOR 里没有任何一条声称「EOS 覆盖外部数据库」，所以「13/13」既没有覆盖也没有验证这一点。
本轮 V2 的 `KF-C1`（NEAR_MISS）刻意植入该错误：「Kafka 的 exactly-once 能保证端到端只处理一次，包括写入外部数据库」
——正确答案是 EOS 只覆盖 Kafka 内部读写（或事务性 consume-transform-produce），不保证任意外部系统。

→ 报告纠正：上一轮**未验证** EOS 边界，不能宣称 Kafka 维度已覆盖。

### 4.5 G3 GOOD 的重复扣分：**确认存在**

该回答每句技术上都正确（评语自己也写「术语使用正确」），却给 `technical_correctness=4`；缺的是 offset 时序窗口与
死信补偿——属 coverage/depth。同一处不足在三个维度上被扣了三次。

→ 判为**跨维度重复扣分**（评分标定缺陷，非模型理解错误）。

### 4.6 三组 POOR 固定 1/1/1/2/2：**是模型自我收敛 + 锚点设计问题，不是程序钳制**

排查结论：
- 代码中没有任何分数钳制（`models.py` 只有 `CHECK 1..5`；`evaluation_repair.py` 只允许修 `answer_quotes` 数量，且
  显式禁止改动评分字段）。
- 技能 `evaluation_anchors` 是**定性描述**（如「机制和术语是否准确」），**没有数值阶梯**。

→ 所以在没有任何分数标定阶梯时，模型对「明显错误」收敛到同一地板（正确性/覆盖/深度=1，相关/表达=2），三份错误
数量不同（5/4/4）、长度不同（134/124/105）却同分。这是**规则设计缺口**（缺数值锚点）导致的分辨力丧失，
不是程序 bug，也不是单次随机。

### 4.7 纠正后的统计口径

| 口径 | 第 24 轮原报告 | 复核后 |
|---|---|---|
| 关键错误识别 | 13/13「全部正确」 | **11/13 推理完全正确**；1/13 结论对但推理错（自增主键）；1/13 方向对但措辞过度绝对（checkpointer） |
| 错误清单质量 | 未区分 | 13 条中 1 条（「未区分消息去重与业务幂等」）实为覆盖缺口而非事实错误 |
| EOS 边界 | 未标注 | **未测** |
| G3 GOOD 重复扣分 | 「疑似」 | **确认** |
| POOR 同分原因 | 「地板模板」推测 | 确认为**模型收敛 + 无数值锚点**，非程序钳制 |

## 5. 离线验收结果

### 5.1 18 份样本全部走正式链路

`r24_offline_chain.json`：18/18 均产生 **1 条** `interview_evaluations`，落库五维与返回 artifact **完全一致**，
题目 `COMPLETED`、会话 `COMPLETED`，18 个互不相同的用户，provider=`replay`、model=`R24_SCRIPTED_EVALUATION`、
prompt=`2.3.3`，每份 1 次 `AdaptiveSelection`（replay）+ 1 次 `AdaptiveEvaluation`（脚本化），真实 HTTP=0。
**注意**：其中的分数来自脚本化回放（按 `expected_band` 中点），**不是模型输出**，只用于证明「落库保真」。

### 5.2 边界用例

| 用例 | 结果 |
|---|---|
| 幂等重复提交 | 第二次返回同一批 events，评价仍 1 条，模型调用仍 1 次 |
| 评价失败 | 抛错、**0 条**评价、answer=`FAILED`、会话仍 `IN_PROGRESS`；同幂等键重试后正常落 1 条 |
| 盲测载荷 | 只含 `answer` / `rubric` / 题干；`quality_label`、`GOOD`/`POOR`、`expected_band`、参考要点均不在其中 |
| 记忆写入 | 新记忆行**只**属于本夹具用户（其他用户 0 行）；Coach 报告 0 条 |

### 5.3 回归

| 范围 | 结果 |
|---|---|
| 全量后端（`interview_agent_wb_r24_test_20261115`） | **988 passed / 12 failed / 1 skipped**（6m28s） |
| 上述 12 个失败 | 全部是迁移测试的**命名守卫**（要求库名 `interview_agent_v21_*`），非产品回归 |
| 迁移用例（`interview_agent_v21_r24_20261115`） | **12 passed**（1m29s） |
| 等价合并口径 | **1000 passed / 0 failed / 1 skipped** |
| 相关子集（投影/导演/自适应/job_target/m4/私有 JD） | 123 passed |

## 6. 下一次预算估算与 ≤8 次 HTTP 方案（**未执行**）

依据第 24 轮真实 usage：Evaluation 单次 3,272–4,158 tokens（正常路径均值 ≈4,031，含 `reference_answer`），
Main 单题 4,918–5,993。

| 规模 | 真实 HTTP（不修复重试） | 上限（≤10% 结构化修复） | Token 估算 |
|---|---|---|---|
| 6 份回答（3 对） | 6 | 7 | ≈24K（20–29K） |
| 20 份回答（10 对） | 20 | 22 | ≈81K（66–91K） |
| 30 份回答（15 对） | 30 | 33 | ≈121K（98–137K） |
| 旧方案（30 题 × GOOD+POOR 且 Main 也真实出题） | 90 | 99 | ≈360K |

**为什么从 90 降到 30**：题面由 Replay 固定（Main 不烧真实额度），且 `question_count=1` 让每次提交只付 1 次评价。

**可 Replay 复用的请求**：① 每份样本的 Main 出题（0 真实 HTTP，已验证）；② 同一 answer 的重复提交（`reusable()`，0）；
③ 全部 18 份的端到端回归（离线 0 HTTP）。

**停止条件与 UNKNOWN 围栏**：每次调用经 `reserve_model_call` 预授权（预算不足**在发请求前**拒绝，`INTERVIEW_BUDGET_EXHAUSTED`，
0 token）；`with_model_reservation` 失败也结算，`request_sent` 与 usage 一起入账；`allow_unknown_retry=False` 时
`INTERVIEW_OUTCOME_UNKNOWN` 不会自动重发；配额错误立即停。

### ≤8 次真实 HTTP 短程验收方案（待批准）

| 步 | 动作 | HTTP |
|---|---|---|
| 1 | RAG 对：`RAG-A1`(GOOD_FULL) vs `RAG-A2`(POOR_LONG_WRONG，更长) | 2 |
| 2 | LangGraph 对：`LG-B1`(GOOD_SHORT) vs `LG-B2`(PARTIAL) | 2 |
| 3 | Kafka 对：`KF-C1`(NEAR_MISS) vs `KF-C2`(POOR_CONFUSED) | 2 |
| 4 | 重复提交 `RAG-A1`（验证 0 次调用、0 新评价） | **0** |
| 5 | 结构化修复预留（最多 2 次） | ≤2 |
| 合计 | 硬上限 | **8** |

- Token 上限 **40K**（6×4.2K + 2×4.2K ≈ 34K）；时间上限 **10 分钟**（单次 5.5–9.3s）。
- 中止条件：出现任一次 `INTERVIEW_OUTCOME_UNKNOWN` / `MODEL_USER_QUOTA` / 连续 2 次失败 / HTTP 达 8 / token 达 40K /
  任一 P1 复现。
- 验收判据：6 条评价全部落库且 provider 为真实模型名；每题能指出 POOR 的错误并给出**正确**解释（重点看 4.1 的
  自增主键与 4.4 的 EOS 边界是否复现）；GOOD 的 `technical_correctness` 是否仍被覆盖缺口拖低（4.5）；
  三份 POOR 是否仍同分（4.6）。

## 7. 仍未解决

1. **锚点无阶梯**导致 POOR 同分（4.6）——需产品决策，本轮按要求未改评分规则。
2. **跨维度重复扣分**（4.5）——同上，未改。
3. 模型对「错误原因」的编造（4.1）属真实质量问题，只能靠更大样本量化，不在本轮范围。
4. 数据集规模仍只有 18 份/9 对，且全部为合成；真实模型结论须待 ≤8 次方案跑完才成立。
