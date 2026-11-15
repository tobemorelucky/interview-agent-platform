# 第 27 轮：Evaluation 2.3.4 真实模型验证（4 对 / 8 份 / 8 HTTP）

日期：2026-11-15　交接 HEAD：`7d8c7ce`　真实模型 HTTP：**8**（计划 8，上限 12）　Token：**37,382**（上限 60,000）　耗时：**62.4 s**（上限 900 s）

本轮只测试，不改产品代码、不改 Agent、不改评分提示词、不新增功能、不执行任何数据删除。证据见 `data/sandbox_test_pkg_20261114/r27_live_eval.json`、`r27_live_analysis.json`、`r27_frozen_pairs.json`。

---

## 1. 开始前检查（全部通过才发请求）

### 1.1 `0e89a1f` 与 `7d8c7ce` 的差异

两者都在**同一条线性历史**上，不是代码分叉：

```
7d8c7ce 12:49:03  docs(workbuddy): note the runtime-target precedent for the ledger guard   ← 实际 HEAD
0e89a1f 12:48:20  docs(memory): record round 26 in the independent project memory          ← 上轮报告末尾所列
3ce6d52 12:48:09  docs(workbuddy): round 26 attribution root cause
267e011 12:48:00  feat(evaluation): 2.3.4 fixes the five-dimension boundaries and the quote cap
b2a5358 12:47:53  fix(governance): bind model attribution per call
```

`git merge-base --is-ancestor` 对五个提交均为 YES；`git diff --stat 0e89a1f..HEAD` 只有
`docs/105_workbuddy_model_scope_and_eval_boundaries.md | 2 ++`（**纯文档补注**）。
所以上轮报告把记忆提交写在最后，只是**列表顺序的表述问题**，运行代码完全一致，未切换或重置任何代码。

- P0 归属修复实存：`infrastructure/models/scope.py`（`ModelScope` / `require_scope` / `assert_ledger_target` / `model_scope_for`）、`governance.py::governed_call` 已删除静默回退，`modules/interview/core_service.py::call_agent` 每次调用绑定 scope，两个新错误码在 `TERMINAL_CODES`。
- Prompt 2.3.4 实存：`adaptive_prompts.py::EVALUATION_BOUNDARIES_PROMPT`（7,452 字符），`context.py` 冻结 `FIVE_DIMENSION_BOUNDARIES_V1` + `evaluation_boundaries=True`，`context_projection.py` 拒绝混合版本。冻结提示词中含 `technical_correctness = accuracy`、`Omission alone`、`Coverage never`、`at most 3`、Kafka/LangGraph/RRF 边界。
- **DB 实测**：8 条评价 `prompt_version` 全为 `2.3.4`，4 对上下文 `boundary_contracts` 全为 `FIVE_DIMENSION_BOUNDARIES_V1`，`diff_fields=[]`。

### 1.2 Provider / 模型 / 无付费端点

| 项 | 实测值 |
|---|---|
| `settings.llm_provider` | `modelscope`（`use_modelscope=True`） |
| `modelscope_connection()["base_url"]` | `https://api-inference.modelscope.cn/v1`（**免费 API-Inference**，非 API-Provider 转发） |
| `modelscope_connection()["model"]` | `deepseek-ai/DeepSeek-V4.1-Flash-1017` |
| 凭据 | `ms-` 前缀 39 位 ModelScope token（`.env`） |
| 主出题 | `FixedQuestionReplay`，8 次 `AdaptiveSelection` 全为 Replay，**零 HTTP** |
| 付费端点 | 仓库内 `dashscope` / `aliyuncs` / `ark.cn` 命中 **0 个文件**；`interview_model_base_url` 为空；`.env` 的 `LLM_BASE_URL`（火山方舟）在 `use_modelscope=True` 时被 `_chat_provider` 短路，不会生效 |

脚本在 `run()` 里对 `base_url`、`model`、冻结版本、账本目标做**发送前断言**；负向测试把 `--database-url` 指向历史库 `interview_agent_wb_r25_live_20261115`，返回「拒绝执行：不是本轮 sandbox 数据库」，0 请求。

### 1.3 沙箱与账本守卫

- 新建 5 个 0029/68 表空库：live、rehearsal、rehearsal2、replay、ledger（库总数 238 → 243，无删除）。
- `settings.model_ledger_database` 在进程内声明为本次沙库；`assert_ledger_target()` 在预留前校验。
- 0 HTTP 彩排：`replay` 8/8 `SUBMITTED`、幂等 0 新评价；`ledger` 模式 8 条 admission 全部 `SETTLED`、owner `user:1..8`、route `answer_evaluation`、各 1,500 tokens（真实网关 + 固定回复）。
- 受保护资产前后一致：正式库 `0025/19/41/5/0`；18 轮原件 `0026/3/18/1/49`；快照库 `0026/1621/1761/219/147`；第 25 轮 live 库 `6/6/0/8`；MinIO 主 bucket 48 对象；三闸 OFF。

---

## 2. 样本与前后对照的严格性

`r27_frozen_pairs.json` 的**前两对是第 25 轮（2.3.3）冻结文件的逐字复制**（去掉附加的 `case_key` 后 `==` 为 True），并复用了 2.3.3 的三个夹具公司名与 JD，使模型载荷里的 `company_name` 也一致。新增两对沿用同一冻结题目/rubric/JD/技能，只替换配对样本。

| 对 | 样本 | 字数 | 说明 |
|---|---|---|---|
| R25-RAG-P1 | RAG-B1 GOOD_SHORT / RAG-A2 POOR_LONG_WRONG | 88 / 316 | 反向长度 3.59×，2.3.3 同题同答 |
| R25-LG-P1 | LG-B2 PARTIAL / LG-C1 NEAR_MISS | 116 / 189 | 2.3.3 同题同答 |
| R27-KF-P1 | KF-C1 NEAR_MISS / KF-A2 POOR_LONG_WRONG | 157 / 253 | exactly-once 边界，反向长度 1.61× |
| R27-RAG-P2 | RAG-C1 NEAR_MISS / RAG-C2 POOR_CONFUSED | 139 / 157 | 中间档 vs 低档，反向长度 1.13× |

**注意用户描述与数据集标签的一处差异**：`RAG-C1/C2` 实际是 NEAR_MISS vs POOR_CONFUSED（中高 vs 低），不是两个中间档；真正的「PARTIAL vs NEAR_MISS」是 `LG-B2/C1`。已按用户指定的样本 ID 执行，如实说明。

跨轮语义比对（2.3.3 live 库 vs 2.3.4 live 库，对 5 个重叠样本）：`company_name`、`job_title`、`requirements`、技能指纹、评分锚点、round/style、blueprint targets、gap_scope **全部相同**；唯一差异是 `adaptive_policy` 增加 `evaluation_boundary_contract` 且 `answer_evaluation.version` 从 `2.3.3` 变为 `2.3.4`——这正是本轮要测的自变量。

盲测：8 份载荷 `forbidden=[]`（无质量标签、预期分数、人工纠错清单），`rubric_lines=5`，`answer_chars` 与冻结字数逐一相符，题目/答案/rubric 落库逐字一致。

---

## 3. 8 份真实五维评分与具体评语

五维顺序：技术正确性 / 覆盖 / 深度 / 相关 / 表达。

| 样本 | 标签 | 五维 | 总分 | 预期带 | 模型评语（原文节选） |
|---|---|---|---|---|---|
| RAG-B1 | GOOD_SHORT | **5/3/3/5/4** | **20** | [17,22] ✅ | 「回答准确给出了 RRF 的核心公式 1/(k+rank) 累加、k≈60……**技术表述无误**。但覆盖上只答到 rubric 前四点中的部分：未说明 k 越小越偏向头部排名……深度停留在机制陈述，缺少失败边界与验证依据。」`issues=[]` |
| RAG-A2 | POOR_LONG_WRONG | **1/2/2/4/3** | **12** | [1,8] ❌ | 「核心机制几乎全部说错。RRF 被描述为 log(bm25+cosine)，实际是按名次累加 1/(k+rank)……k 的方向说反……『RRF 一定优于加权求和』是绝对化错误结论。仅『分两步』的流程框架和『RRF 无需调参』的直觉方向尚可。」5 条 issues |
| LG-B2 | PARTIAL | **2/2/2/4/3** | **13** | [10,15] ✅ | 「正确指出 StateGraph 管理共享状态……但**本题问的正是持久化与恢复**，checkpointer 恰恰是持久化、thread 级恢复和人工介入的机制，这是核心机制误判。恢复描述『重新跑一遍图』回避了从 checkpoint 快照恢复。」 |
| LG-C1 | NEAR_MISS | **2/4/3/5/4** | **18** | [15,20] ✅ | 「覆盖面较好。但结尾补充的『checkpointer 是在节点函数内部、节点逻辑执行前写入快照』是明确的机制错误：checkpoint 在 super-step 边界写入……属于关键机制错误，拉低技术正确性。」 |
| KF-C1 | NEAR_MISS | **2/4/4/5/4** | **19** | [15,20] ✅ | 「主线机制正确且结构清晰。但结尾声称 Kafka exactly-once 能保证端到端只处理一次并包括写入外部数据库，这是关键机制错误：**Kafka 事务的 exactly-once 作用域限于 Kafka 内部的读-处理-写以及 offset 提交，无法原子覆盖任意外部数据库写入**。」 |
| KF-A2 | POOR_LONG_WRONG | **1/1/1/2/3** | **8** | [1,8] ✅ | 「消费者默认是至少一次；enable.idempotence 是生产者侧幂等……数据库自增主键对同一业务消息重放会生成不同主键，无法去重；**offset 没有回滚 API，seek/重置只能重读记录**；先提交 offset 再处理业务会丢消息。」6 条 issues |
| RAG-C1 | NEAR_MISS | **3/4/4/5/5** | **21** | [15,20] ❌高1 | 「覆盖了大部分 rubric。但关键方向说反了：RRF 中 k 越小越偏向头部排名……属于**一处非致命但明确的机制错误**，因此技术正确性降为 3。」 |
| RAG-C2 | POOR_CONFUSED | **1/1/1/2/3** | **8** | [1,8] ✅ | 「把 RRF 与 softmax 加权求和、cross-encoder 重排混为一谈……RRF 只用名次做 1/(k+rank) 累加……cross-encoder 是对候选做相关性打分，RRF 是名次融合，两者职责不同。」3 条 issues |

**配对差值**：RAG-B1−RAG-A2 = **+8**（2.3.3 为 +10）；LG-C1 比 LG-B2 高 **5**（2.3.3 也是 +5）；KF-C1−KF-A2 = **+11**；RAG-C1−RAG-C2 = **+13**。四对方向全部正确，且三对是「错误答案更长」的反向长度控制。

---

## 4. 人工复核（逐项）

| # | 检查项 | 结论 |
|---|---|---|
| 1 | 技术正确但简短时 tc 是否被不合理扣分 | **已修复**。`RAG-B1` 零技术错误，tc 由 2.3.3 的 **4 → 5**，评语明确「技术表述无误」，`issues=[]`；覆盖缺口全部落在 `coverage=3` / `depth=3`。 |
| 2 | coverage 遗漏是否错误影响 tc | **在观测样本中已分离**。RAG-B1 是最直接的证据：2.3.3 的 tc=4 理由全是「未说明/未给出」，2.3.4 不再这样扣分。LG-C1 也出现 tc↓ 但 coverage↑ 的反向分解（3/3 → 2/4），说明两维不再被同一理由拖动。 |
| 3 | depth 与 coverage 无依据重复扣分 | **明显改善但未完全消失**。RAG-B1 的 `depth=3` 有独立理由「缺少失败边界与验证依据」；KF-A2 的 `depth=1` 理由「只给出结论式断言，无竞态窗口与验证方式」。但 `RAG-A2` 的 `depth=2` 主要复述「机制不足以支撑」，与 tc 理由重叠，属于 2.3.4「核心机制错 depth 封顶 2」规则驱动，理由表述不够独立。 |
| 4 | PARTIAL vs NEAR_MISS 差异 | **合理且更清晰**。13 vs 18；LG-B2 因「checkpointer 是调试附属品」这一核心机制错得 tc=2/cov=2，LG-C1 覆盖 5 个 rubric 点得 cov=4 但一处机制错得 tc=2。 |
| 5 | 五维是否各有事实依据 | **不再是整体档位**。2.3.3 的 LG-B2→LG-C1 是五维同步 +1（2/2/2/3/3 → 3/3/3/4/4）；2.3.4 变为 (0,+2,+1,+1,+1)。8 份中出现 5/3/3/5/4、2/4/4/5/4、3/4/4/5/5 等非单调组合。残留：完全错误的 POOR 答案在 tc/cov/depth 三维同步走低，这对「机制全错」是合理的；RAG-B1→RAG-A2 的 relevance/communication 仍同步 −1，值得继续观察。 |
| 6 | 纠错解释是否准确 | **未发现技术幻觉**；逐条核对 8 份评语中的机制纠正（RRF 名次累加、k 方向、BM25 无上界/余弦可为负、super-step 快照、thread_id、状态快照≠业务事务、生产者幂等、自增主键不能去重、seek 重读、cross-encoder 与 RRF 职责不同）均成立。**两处措辞问题**：① `KF-A2` 把「核心机制错 depth 封顶 2」写成「封顶为 2 以下」，规则复述不精确（得分 1 仍合法）；② `RAG-A2` 把答案中「RRF 完全不需要调参、k 固定 60」这一**错误**改写为「不需要针对分数尺度调参」并计入 `covered_points`，属于过度给分。 |
| 7 | Kafka / RRF / LangGraph 边界 | **正确但需注意来源**。KF-C1 准确说明 EOS 仅限 Kafka 内部、不覆盖外部数据库；KF-A2 把 2.3.3 的绝对化「Kafka 不支持 offset 回滚」改为「**没有回滚 API，seek/重置只能重读记录**」；LG-B2 只在「本题问持久化与恢复」的语境下说 checkpointer 是必要机制，**未**宣称所有 LangGraph 场景绝对必需。这三条事实都写进了 2.3.4 提示词，因此这证明的是「解释不再出错」，**不证明模型脱离提示词仍独立掌握边界**。 |
| 8 | 最多 3 条引用后 Repair 是否下降 | **下降：2/8 → 0/8**。8 份 `answer_quotes` 为 3/3/2/2/3/3/3/2，全部 ≤3；`structured_retry` 合计 **0**，`structured_repairs=0`，评价行 token 与 admission 逐条相等。第 25 轮 8 次 HTTP 含 2 次 `EVALUATION_QUOTE_SUBSET_V1`（占 25%）。**注意**：2.3.4 提示词从 3,748 → 7,452 字符，单次 input 增加约 900 tokens，所以本轮 8 次无 Repair 仍用了 37,382 tokens（上轮 8 次 29,827）；省的是 HTTP 与不确定性，不是 token。 |
| 9 | 是否通过 Controller 正式落库 | **8/8**。`create→start→prepare_answer→Evaluation Agent→commit_answer`，事件均为 `evaluation/complete/done`；8 条 `interview_evaluations`（全 `pv=2.3.4`）、8 个 session / question / answer 全 `COMPLETED`、24 个 agent run 全 `SUCCEEDED`、幂等重提 0 新评价 0 新请求。 |
| 10 | owner / route / 账本库 / Token 结算 | **全部正确**。8 条 admission：`user:1..8`、`answer_evaluation`、全 `SETTLED`、0 `UNKNOWN`/0 `RESERVED`；`admission_tokens = eval_tokens = 37,382`，逐条相等；`scope_owner_ok=True` 8/8；账本只写 `interview_agent_wb_r27_live_20261115`。8 条 `capability_memories` 全属一次性用户 `1..8`，`memory_proposals=0`、`coach_reports=0`。 |

---

## 5. 新版 vs 旧版（5 个严格同题同答样本）

| 样本 | 2.3.3 五维 / 总分 | 2.3.4 五维 / 总分 | Δ 总分 | Δ tc |
|---|---|---|---|---|
| RAG-B1 GOOD_SHORT | 4/3/3/5/4 = 19 | **5/3/3/5/4 = 20** | +1 | **+1** |
| RAG-A2 POOR_LONG_WRONG | 1/1/1/3/3 = 9 | 1/2/2/4/3 = **12** | +3 | 0 |
| LG-B2 PARTIAL | 2/2/2/3/3 = 12 | 2/2/2/4/3 = 13 | +1 | 0 |
| LG-C1 NEAR_MISS | 3/3/3/4/4 = 17 | **2/4/3/5/4 = 18** | +1 | −1 |
| KF-A2 POOR_LONG_WRONG | 1/1/1/2/2 = 7 | 1/1/1/2/3 = 8 | +1 | 0 |

**可以确认的**：技术正确性与覆盖度在「正确但简短」样本上已经解耦（tc 4→5）；五维不再整体档位化（LG-C1 出现 tc↓/cov↑）；引用超限导致的 Repair 归零；技术解释的绝对化措辞得到修正。

**不能确认 / 反而变差**：预期带命中率在 5 个重叠样本上**没有提升**（两轮都是 4/5，唯一出带的都是 `RAG-A2`，且从 9 恶化到 **12**）。`RAG-A2` 是一份 316 字、核心机制全错的答案，却因「方向对题」拿到 relevance=4、coverage=2、depth=2。这不是典型的「篇幅奖励」（最短的 RAG-B1 仍得 20），而是**非技术维度把技术上不可用的答案抬出了 POOR 带**；其中还包含上表第 6 项指出的「把错误说法改写成正确说法并计入覆盖」。

**样本量声明**：4 对 / 8 份、**5 个**严格前后对照样本，单次模型调用、无重复采样，**不能据此宣称新版具有统计显著优势或整体质量提升**。以上结论只描述这 8 次实际观测。

---

## 6. 预算与状态

| 项 | 授权 | 实际 |
|---|---|---|
| HTTP | 上限 12（计划 8） | **8**（8 份正式评价，0 Repair，0 重试，0 额外请求） |
| Token | 60,000 | **37,382** |
| 时间 | 900 s | **62.4 s** |
| 单次延迟 | — | 6,162–8,140 ms（提交 6,502–8,313 ms） |
| 账本 | 全 SETTLED | 8/8 SETTLED，0 UNKNOWN，0 RESERVED |
| 停止条件 | UNKNOWN/配额/连续失败 | 均未触发，`stop_reason=None` |

`LLM_PROVIDER` 未改动、未切换 Provider、未充值、未扩大到 30 份、未开启下一轮功能开发。产品代码零改动（本轮新增的只是 `apps/api/scripts/r27_live_evaluation_acceptance.py` 测试脚本）。

---

## 7. 下一阶段建议（本轮不实施）

**建议先做一次「定点补测」，而不是直接改 Rubric，也不是直接扩到 30 份。** 理由：主要目标（tc/coverage 解耦）已在唯一个此前复现缺陷的样本上验证，但目前只有 1 个「正确但简短」样本；同时出现 2 个需要判断是否稳定的现象。

1. **先补 3 类样本，各 3–4 份，共约 12 对 / 24 份**：
   - 技术正确但简短（跨主题，确认 tc=5 不是 RAG 单题侥幸）；
   - NEAR_MISS 只含一处附加错误细节（校准「核心机制错 tc≤2」与「次要细节 tc=3」的分界——本轮 `LG-C1` 被判 tc=2、`RAG-C1` 被判 tc=3，两条都是单一方向性错误，判法不一致）；
   - 切题但机制全错的长答案（确认 POOR 出带是偶发还是系统性）。
   预算估算：**HTTP 26–28 / Token 约 120,000 / 25 分钟**（按本轮 4,673 tokens/次、约 8% Repair 余量）。
2. **Rubric 最小澄清（若补测确认问题稳定，另行批准后再改，需版本化 2.3.5）**：
   - 「核心机制不可用」时，relevance/communication 不得因切题或结构清晰而把总分抬出 POOR 带；
   - `covered_points` 不得收录被 `issues` 判定为错误的同一说法（禁止改写式给分）；
   - 明确「附加的一处错误细节」属于 side detail（tc 上限 3），只有改坏主干机制才适用 tc≤2。
   本轮**不改**任何提示词、锚点、Schema 或评分范围。
3. 不值得现在扩到 30 份：quote Repair 已归零、链路与账本已稳定，扩大规模前先解决「严重度锚点一致性」和「POOR 出带」这两个尚不稳定的判据，否则只是把同样的分歧重复 30 次。

---

## 8. 交付文件

- `apps/api/scripts/r27_live_evaluation_acceptance.py` — 本轮验收脚本（含发送前 Provider/版本/账本断言、UNKNOWN 与配额硬停、2.3.4 冻结校验、4 对样本）
- `data/sandbox_test_pkg_20261114/r27_frozen_pairs.json` — 4 对冻结样本（前两对逐字复制 2.3.3）
- `r27_replay_final.json` / `r27_ledger_final.json` — 0 HTTP 彩排证据
- `r27_live_eval.json` — 8 次真实调用全量原始证据（评语、引用、账本、载荷审计）
- `r27_live_analysis.json` — 评分、配对差值、前后对照、逐条账本对应
- 本文档
