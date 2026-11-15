# 第 28 轮：2.3.4 阶段性冻结、真实缺陷复核、V3 困难样本集与全链路准备

日期：2026-11-15　交接基线：`5329036`　**真实模型 HTTP = 0**

本轮不修改五维字段、评分逻辑、Prompt、Schema、数据库迁移；不改历史 2.3.3 / 2.3.4 评分；不新建数据库；不启用 Coach Harness；不推进 L3/L4；不 push/reset/clean；不修改其他编码 Agent 的专属文件（`AGENTS.md`、`CLAUDE.md`、`.claude/` 均只读未动）。

---

## 0. 基线核查

| 项 | 实测 |
|---|---|
| `git rev-parse HEAD` | `5329036a982015394224ad59c56a2df5763eed83` |
| 已跟踪改动 | 0 |
| 2.3.4 冻结 | `adaptive_prompts.py::EVALUATION_BOUNDARIES_PROMPT` 版本 `2.3.4`，system 7,452 字符，指纹 `e7704357b5fc37ba…`；含 `technical_correctness = accuracy`、`Omission alone`、`Coverage never`、`at most 3` |
| 边界契约 | `context.py` 冻结 `FIVE_DIMENSION_BOUNDARIES_V1` + `evaluation_boundaries=True`；`context_projection.py` 拒绝混合版本 |
| 历史评分 | 2.3.3（`r25_live_eval.json`，6 条）与 2.3.4（`r27_live_eval.json`，8 条）逐字保留，未覆盖、未重标 |
| 闸门 | `purge_execution_enabled=false`、`interview_coach_harness=false`（`core/config.py:200,205`） |
| 资产 | 正式库 `0025/19 sess/41 eval/5 report/0 adm`；18 轮原件库 `0026/3/18/1/49`；快照库 `0026/1621/1761/219/147`；r25 `6/6/0/8`；r27 `8/8/0/8`；库总数 243；MinIO 主 bucket 48 对象。**本轮未创建任何数据库、未写入任何上述库** |

---

## 1. Evaluation 2.3.4 最终阶段性评估

### 1.1 冻结条件

- **冻结对象**：Prompt `2.3.4` + `FIVE_DIMENSION_BOUNDARIES_V1` + 五维 `1..5` + 现有 Schema/迁移。本轮只读校验，未改动。
- **可比条件**：RAG-B1/A2、LG-B2/C1 两对逐字复制第 25 轮（2.3.3）冻结对，并复用其三个夹具公司名；跨轮语义比对 5 个重叠样本的 `company_name / job_title / requirements / 技能指纹 / 评分锚点 / round / style / blueprint targets / gap_scope` 全部相同，唯一差异是新增边界契约与 Prompt 版本。
- **实际样本量**：2.3.3 = 6 份评价 / 8 次 HTTP（含 2 次 Repair）；2.3.4 = 8 份评价 / 8 次 HTTP（0 Repair）。两者**只有 5 个同题同答重叠样本**，且评价份数不同（6 vs 8）、配对不同（3 对 vs 4 对）、其中 3 份 2.3.4 样本无 2.3.3 对照。
- **结论边界**：**不能宣称统计显著改善**。以下只描述这 8 次实际观测，不构成整体质量结论，也不构成「新版优于旧版」的概率判断。

### 1.2 已确认改善（限于观测样本）

| 现象 | 2.3.3 | 2.3.4 | 说明 |
|---|---|---|---|
| 技术正确但简短被扣技术分 | RAG-B1 `tc=4`，理由全是「未说明/未给出」 | RAG-B1 `tc=5`，评语「技术表述无误」、`issues=[]` | 覆盖缺口只落 `coverage=3 / depth=3`，tc/coverage 解耦 |
| 五维整体档位化 | LG-B2→LG-C1 同步 +1（2/2/2/3/3 → 3/3/3/4/4） | (0,+2,+1,+1,+1)，LG-C1 出现 tc↓/coverage↑ | 不再被同一理由拖动 |
| 引用超限 Repair | 2/8 HTTP（KF-A1、KF-A2 各 1 次） | 0/8，8 份 `answer_quotes` 全 ≤3 | 见 §2 第 7 项，尚不能当作稳定率 |
| 技术解释绝对化 | 「Kafka 不支持 offset 回滚」 | 「没有回滚 API，seek/重置只能重读记录」 | EOS 作用域、checkpointer 可选性同样更精确；**但这些事实写进了 2.3.4 Prompt，只证明解释不再出错，不证明模型脱离提示词独立掌握** |

### 1.3 未改善 / 变差（必须保留）

- 预期带命中 6/8；5 个重叠样本两轮都是 4/5，唯一出带的 `RAG-A2` 从 9 **恶化到 12**（`tc=1`，但 `relevance=4 / coverage=2 / depth=2`）。
- `LG-C1` 与 `RAG-C1` 同属「一处明确的单一错误」，分别判 `tc=2` 与 `tc=3`；`KF-C1` 判 `tc=2`。严重度锚点未稳定。
- 顺带发现**历史数据集自身的缺陷**：V2 的 POOR 样本 `expected_band=[1,8]` 含不可达下界。五维各 1..5，总分物理范围是 **5..25**，1–4 不可能出现。历史 V2 结果不改写，只在 `12_V3_manifest.json` 记录，V3 已把全部区间修到 5..25 内。这不是修改评分，而是修正标注口径。

**不为了提高预期带命中率而调整评分**：本轮没有改 Prompt / 锚点 / 字段 / 逻辑，也没有重标任何旧结果。

---

## 2. 七项真实缺陷的人工复核

证据文件：`r27_live_eval.json`（2.3.4 原始评语与 artifact）、`r25_live_eval.json`（2.3.3 对照）、`r27_frozen_pairs.json`（冻结答案与人工清单）、`docs/106`。严重程度：**高**=污染评价证据链或可能误导能力结论；**中**=影响分数解释/成本/标注可信度；**低**=措辞或可观察性。

### 缺陷 1｜RAG-A2 的技术错误被改写后写入 `covered_points`　**高**

- **实际回答**：「RRF 的优点是**完全不需要调参，k 固定 60 就行**……」（316 字，人工标注 6 处错误，其中第 3 处即此句）。
- **模型评价**：`covered_points` 写入「**指出 RRF 的一个好处是不需要针对分数尺度调参**」；`factual_or_reasoning_issues` 只列了 5 条，**恰好漏掉这一条**，评语还写「『RRF 无需调参』的直觉方向尚可」。
- **人工复核**：候选人说的是「完全不需要调参、k 固定」，这是错误；模型把它改写成「不需要针对分数尺度调参」——这是一个**候选人没说过的正确命题**，且恰好对应 rubric 第 2 点。答案全文没有出现「名次 / rank」，也没有把「无需归一」归因为名次融合，因此不存在给这个 rubric 点打勾的原文依据。结果：`coverage=2`（2.3.3 为 1）、`relevance=4`（2.3.3 为 3），是总分从 9 升到 12 的主要来源之一。
- **机制判断**：**推测（未验证）**——2.3.4 把 `coverage` 明确定义为「rubric 被回答覆盖的比例」，可能促使模型用 rubric 条目去匹配答案表层词，把错误句「归一化/无需调参」补成正确的 rubric 点。不能当作已证实的根因。
- **对照**：2.3.3 同一答案的 `covered_points` 是「提到 RRF 这一术语及 k 参数的存在」这类**中性描述**，未出现改写式给分。所以这是 2.3.4 观测中新出现的表现，不是简单继承。
- **影响面**：`interview_report/statistics.py` 把 `covered_points` 带进 Coach 报告；但 `capability_service.py` 用 `knowledge_observations.state` + `factual_or_reasoning_issues` 判 `disputed`，本例 issues 非空且 state=`NEEDS_CLARIFICATION`，**不会**误标 `VERIFIED`。所以风险集中在报告证据链，不在能力记忆准入。
- **建议**：下一阶段把「`covered_points` 每条必须能在答案中找到逐字依据」列为硬判据；本轮不改代码。

### 缺陷 2｜是否把候选人没说出的正确知识当成已回答　**高**

- **确认 1/8**：缺陷 1 的 RAG-A2 即明确一例（`candidate_did_not_say` 类知识被计入 `covered_points`）。
- **逐份复核**：`RAG-B1 / LG-B2 / LG-C1 / RAG-C1 / KF-C1 / KF-A2 / RAG-C2` 共 7 份的 `covered_points` 可逐句对应到答案原文；`KF-A2` 的 2.3.3 版本甚至自带「（但结论错误）」的限定，2.3.4 更保守，只写「识别到主题」。
- **人工结论**：这是**证据边界**问题，不是普遍幻觉；但一次出现就足以污染该条评价的覆盖分与报告引用。V3 已为**每一份**样本显式增加 `candidate_did_not_say` 字段，并由契约测试断言这些字符串不在 `blind_context.answer` 内。
- **边界声明**：`correct_points` 只记「候选人真正说对的内容」；模型推断「候选人可能知道」的正确知识一律不得计入覆盖。此规则写入 `12_V3_manifest.json::strict_distinction_rule`。

### 缺陷 3｜核心机制错误时 tc 很低但总分仍较高　**中**

- **实测**：RAG-A2 `tc=1`、总分 `12/25`（2.4/5 均分）；KF-A2 `tc=1`、总分 `8/25`。
- **产品目标核查（只读代码）**：五维总分**不是**准入门槛。`controller.py:512` 与 `interview_report/statistics.py:44` 只把它汇总为 `completion_json.overall_average` / Coach 报告 `statistics.overall_average`；`capability_service.py:64` 明确注释「difficulty and score don't prove independence」，能力状态由 `observation.state + 逐字引用 + open_gaps + disputed` 决定。全仓库未找到以总分为通过/淘汰阈值的逻辑。
- **人工结论**：**不是 P0 逻辑错误**，但会把一份技术不可用的答案在报告里呈现为「接近中等」，属于报告口径失真。`RAG-A2` 出带的 3 分增量主要来自 `coverage/depth/relevance`，其中 `coverage=2` 又与缺陷 1 的改写式给分有关，因此**先修证据边界、再谈分数分布**，不要用调 Prompt 去凑带。
- **附带问题**：V2 的 `[1,8]` 下界不可达（见 §1.3）。下一阶段应使用 5..25 内的区间，并优先用相对关系与严重度梯度做判据。

### 缺陷 4｜LG-B2 / LG-C1 的 PARTIAL / NEAR_MISS 关系是否人工确认　**中**

- **分数关系已确认**：两轮都满足 NEAR_MISS > PARTIAL（2.3.3：17 > 12；2.3.4：18 > 13），方向一致。
- **但标签语义未经独立复核，且自相矛盾**：
  - `docs/103 §3` 定义 PARTIAL =「主要正确、缺重要细节」、NEAR_MISS =「大面积正确、仅 1 处关键技术错误」；2.3.4 Prompt 进一步写「PARTIAL = 遗漏但不含错误机制」。
  - 而 `LG-B2`（标 PARTIAL）的人工错误清单列了 **2 处机制错误**：「把 checkpointer 当可选优化」「恢复就是重新跑一遍图」。按上述定义它**不是纯 PARTIAL**。
  - 更关键：`docs/103 §4` 的人工复核明确写「LangGraph 的 checkpointer 是**可选**的，不挂载也能跑图；只在需要持久化/恢复时必要」，而 V2 对 LG-B2 的错误解释却写「checkpointer 是持久化与恢复的**必要机制**」——**两处人工标注互相冲突**。
  - 2.3.4 模型评语反而更精确：「**本题问的正是持久化与恢复**，checkpointer 恰恰是持久化、thread 级恢复和人工介入的机制」，把必要性限定在题目语境，未宣称所有 LangGraph 场景绝对必需。
- **人工结论**：PARTIAL/NEAR_MISS 的**相对顺序**可用作方向性参照，但**标签本身不能当金标**；2.3.4 的状态词是 `NEEDS_CLARIFICATION`，与 V2 的 `NEAR_MISS` 也不是同一套词汇。V3 已改用「错误严重度（SIDE_DETAIL / KEY_MECHANISM / CONCEPT_SWAP / UNSUPPORTED_CLAIM）+ 显式相对关系」代替档位标签。

### 缺陷 5｜同类单一错误在不同主题下技术扣分是否一致　**中**

| 样本 | 主题 | 单一错误 | 2.3.3 tc | 2.3.4 tc |
|---|---|---|---|---|
| LG-C1 | LangGraph | checkpoint 写入时机 | 3 | **2**（判「关键机制错误」） |
| RAG-C1 | RAG | k 方向说反 | 未测 | **3**（判「非致命但明确的机制错误」） |
| KF-C1 | Kafka | EOS 作用域 | 未测 | **2**（判「关键机制错误」） |

- **人工复核**：三者都是「主干机制正确、只追加/夹带一处明确错误」，删掉该句后其余结论仍成立；按 2.3.4 自己写的「a wrong side detail caps technical_correctness at 3」，`RAG-C1` 的判法更贴合，另两处的「关键机制」定性偏重。2.3.4 新增的严重度阶梯**本应**让这一点确定，实际没有：Prompt 没有定义「核心机制」与「次要细节」的边界，由模型逐次自行判断。
- **结论**：尚不能判定为稳定回归（2.3.3 只测过其中 1 份），但**一致性未达成**。V3 用 `severity_consistency_groups` 把它变成可证伪指标：同类 `SIDE_DETAIL` 三份与同类 `KEY_MECHANISM` 三份各自 `tc` 极差 ≤1，且每组内 SIDE_DETAIL 的 `tc` 严格高于 KEY_MECHANISM。

### 缺陷 6｜2.3.4 Prompt 增长带来的 Token 成本是否值得　**中**

| 项 | 2.3.3 | 2.3.4 |
|---|---|---|
| system 字符 | 3,748 | 7,452（+3,704） |
| 单次 input tokens | 2,405–2,869 | 3,501–3,725 |
| 实测 | 8 次 HTTP / 29,827 tokens（6 份评价 + 2 次 Repair） | 8 次 HTTP / 37,382 tokens（8 份评价 + 0 Repair） |

- **推算（非实测，因两轮评价份数不同）**：2.3.3 单次评价约 3,780 tokens，2.3.4 约 4,673，增加约 **893 tokens/次（+23.6%）**；2.3.3 两次 Repair 共约 7,146 tokens。若 2.3.3 也做 8 份评价且维持 2 次 Repair，约 8×3,780+7,146 ≈ **37,386**，与 2.3.4 实测 37,382 基本相当。
- **人工结论**：**在「2.3.3 会触发约 25% Repair」的前提下，Token 成本大致抵消；若 Repair 率为 0，2.3.4 纯多花约 23.6% tokens**。真正稳定省下的是 HTTP 次数与 Repair 的不确定性，不是 token。当前样本不足以估算 Repair 率，因此不能下「值得/不值得」的总体结论。
- **不改 Prompt**：本轮不缩短提示词，也不调整引用上限。

### 缺陷 7｜0 次 Repair 是否只在当前样本中成立　**中**

- **事实**：2.3.4 的 8 份 `answer_quotes` 为 3/3/2/2/3/3/3/2，全部 ≤3，`structured_retry` 全 0。
- **结构未变**：`KnowledgeObservation.answer_quotes` 的 `max_length=3` 与 `EVALUATION_QUOTE_SUBSET_V1` 子集修复路径**完整保留**；`test_r26_quote_subset_offline.py` 的离线重放证明修复仍会触发并安全裁剪。0/8 是**模型行为观测**，不是结构性消除。
- **可比性限制**：两轮样本集不同。2.3.3 的两次 Repair 发生在 `KF-A1`（2.3.4 未复测）与 `KF-A2`（两轮都有）；**唯一同题同答的 Repair 对照是 KF-A2：1 → 0，n=1**。其余 7 份 2.3.4 样本没有 2.3.3 对应结果，其中 3 份是 2.3.4 新增。
- **人工结论**：可以说「在这 8 份上 Repair 为 0」，**不能**说「Repair 率已下降」或「问题已解决」。V3 的长篇/概念偷换样本特意保留了触发 >3 条引用的机会，供下一阶段检验。

---

## 3. 新增 V3 困难样本集

### 3.1 文件与来源

| 文件 | 内容 |
|---|---|
| `data/sandbox_test_pkg_20261114/2026校招JD_真实面经_模拟回答测试包/10_V3_SYNTHETIC_题目与评分要点.jsonl` | 5 道题：3 道逐字沿用 V2 题目与 rubric（`source=V2_..._UNCHANGED`），2 道新增 `Q_FASTAPI_01` / `Q_REDIS_01` |
| `11_V3_SYNTHETIC_困难样本.jsonl` | **16 份**人工撰写的模拟回答 |
| `12_V3_manifest.json` | 版本、来源、标签/类别/严重度定义、8 组相对关系、3 组严重度一致性指标、盲测契约、就绪状态 |
| `apps/api/tests/test_r28_v3_dataset_contract.py` | 10 条契约断言（0 HTTP / 0 DB / 0 模型） |

**来源声明（不可混同）**：V3 **全部是人工撰写的合成模拟回答**，不是真实 JD、不是真实面经、不是真实候选人记录、不是真实 Evaluation、不是模型 Capture。独立新文件 10/11/12，**不覆盖 V2 的 08/09**（契约测试断言 V2 仍为 18 份且不含任何 V3 标识）。V3 不含任何模型评分结果，不能用于宣称质量提升。

### 3.2 标签与覆盖

| 类别 | 份数 | 主题分布 |
|---|---:|---|
| A 技术正确但回答简短 | 3 | RAG / FastAPI / Redis |
| B 技术正确但遗漏部分要求 | 2 | Kafka / LangGraph |
| C 大部分正确但含一处关键机制错误 | 3 | RAG / LangGraph / Kafka |
| D 大部分正确但只存在次要细节错误 | 3 | RAG / FastAPI / Kafka |
| E 长篇、切题但核心机制错误 | 2 | FastAPI / Redis |
| F 表达流畅但偷换概念 / 补充未经证明的结论 | 3 | RAG / LangGraph / Redis |

5 个 topic：`rag_retrieval / agent / kafka_reliability / python / redis`。8 组相对关系中 6 组为 `SAME_TOPIC_STRICT`、2 组刻意标 `UNCERTAIN`（跨主题，且 2.3.4 对「正确但夹带无据结论」没有锚点，不给方向性判据）；≥3 组构成反向长度控制（错答案更长 ≥1.2×，由契约测试真实断言）。

### 3.3 每份样本的人工整理字段

`original_question` + `rubric` + `reference_facts`（原始题目与参考事实）／`stated_points`（回答实际表达的知识点）／`correct_points`（**候选人真正说对的**）／`missing_points`／`errors[{claim, why_wrong, severity, severity_reason}]`／`severity` + `severity_reason`／`pair_id` + `expected_relation` + `relation_note`／`expected_band` + `band_uncertainty`（参考区间及不确定性）／`possible_model_errors`（可能出现的模型评价错误）／`candidate_did_not_say`（候选人没说、模型可能推断的正确知识）／`answer_sha256` + `char_count`。

### 3.4 人工参考绝不进入模型上下文

- `blind_context` **只允许** `question / rubric / answer` 三个键；契约测试逐份断言键集合相等，并断言 `forbidden_in_model_context`（含 `quality_label`、`expected_band`、`reference_facts`、`correct_points`、`errors`、`candidate_did_not_say` 等）不出现在序列化载荷里，且样本 ID / 标签字面值不泄漏。
- 每份 `human_reference_must_not_enter_model_context=true`；`expected_band` 明确写为「人工方向性先验，不是真值、不是通过判据、不用于调 Prompt」。
- 契约测试同时断言 `candidate_did_not_say` 中的字符串**不在**答案原文里，防止「推断知识」被误当原文依据。

### 3.5 就绪状态（合法输入条件）

- `Q_RAG_01 / Q_LG_01 / Q_KF_01`：`official_chain_runnable=true`。第 27 轮实测蓝图 target 集合恰为 `agent / kafka_reliability / rag_retrieval`。
- `Q_FASTAPI_01`：`false`。`topics.py` 的 `python` 正则为 `\bpython\b|\bgil\b|协程`，当前夹具 JD 未产出 `python` target；且 `yield` 依赖清理时序依 Starlette 版本而定，题目标了 `version_sensitive=true`，断言必须标版本。
- `Q_REDIS_01`：`false`。`java_backend` 技能 `priority_topics` 含 `redis`，但当前夹具蓝图未产出 `redis` target；需要含 `redis / 缓存 / 分布式锁` 的合法 JD Requirement 语句。
- 这 2 个新主题**本轮只作为数据准备**，不进入任何正式链调用。

### 3.6 契约测试发现并修正的问题

首轮测试抓到我给低档样本写了 `[1,9]`、`[3,11]` 等**物理不可达**区间（总分最小 5）。已把 4 份修正为 `[5,10] / [5,10] / [5,12] / [5,11]`，并在 manifest 记录 V2 `[1,8]` 的同类缺陷。另修掉一条我自己写的恒真断言（`or True`）。最终 **10 passed / 0 failed**，且断言均为可失败断言。

---

## 4. 当前各模块真实测试覆盖情况

判定口径：**正式 Live** = 有真实模型 admission 且正式落库；**Mock/Replay** = 模型响应被脚本替换或库内为 `DEMO_V21_NOT_LIVE` / `offline`；**未验证** = 无真实模型端到端证据。

| # | 环节 | 代码 / 数据 | 正式 Live 证据 | Mock / Replay | 未验证 |
|---|---|---|---|---|---|
| 1 | 公开 JD 原文及来源核验 | `data/manual_sources/2026-10-05/`（9 项：4 JD + 5 面经，9/9 有 `source_url` 与 `content_sha256`，`identity_reviewed_at=2026-10-05`，`LOCAL_BODY_AND_ORIGINAL_PUBLIC_PAGE`）；正式库 4 个 `PUBLISHED` JD + 5 `interview_experiences` | **有真实原件**，但 `live_search_used=false`，是 `MANUAL_SOURCE_IMPORT` | — | README 明确：READY **不表示**已入库、内容真实性已被独立证实、或岗位仍开放 |
| 2 | JD Research / 公开数据采集 | `public_career/research`、`experience` 采集；SearXNG 容器在跑 | **无** | Replay / 离线采集 | `live_search_used=false`；18 轮原件库 `experience_collection_tasks=0`、`experience_source_items=0` |
| 3 | Requirement 与 Skill 识别 | `job_target/requirement_refs.py`、`skill_engine.py`、技能 JSON | `docs/97`（6 HTTP / 11,470 tokens，1 份真实归档公开 JD + 1 份合成对照 JD）、`docs/99`（6 HTTP / 13,177 tokens） | `test_r24_*` 用 Replay 验证映射与版本归属 | **未与新收集的 9 份真实 JD 联动验证**；Skill 推荐缺真实候选人材料对照 |
| 4 | ResumeProfile | `candidate_profile`、`resume` | `docs/88`（12 HTTP / 75,706 tokens，真实 UI + Worker 生成画像）、`docs/97`、`docs/99` | 大量离线测试 | **输入全是合成简历**；正式库 38 份简历可见「（测试简历）」，5 份 `PROCESSING` 且 `raw_text` 为空。**无真实简历** |
| 5 | Job Matching | `job_matching/` | **无真实模型 Live 证据**：18 轮原件库 `job_match_runs=0`；`docs/99 §4` 写「确定性证据矩阵，浏览器有展示，无真实用户材料对照」；`docs/24` 的 M6 验收明确「不调用 Live Search/真实模型」 | 快照库 120 个 `job_match_runs`，但其 `model_admissions` 全是 `DEMO_V21_NOT_LIVE` / `test\|offline` | 真实 JD × 真实简历 × 模型排序的完整链路 |
| 6 | Interview Main | `core_service.py`、`agents.py` | **有**：18 轮原件库 27 条 `interview_main` 真实 admission（310,294 tokens），18 题真实场 | r25 / r27 出题阶段全为 `FixedQuestionReplay` | — |
| 7 | Evaluation | `adaptive.py`、`adaptive_prompts.py` | **最充分**：18 轮原件库 20 条（210,044 tokens，2.3.3）、r25 8 条（29,827）、r27 8 条（37,382，2.3.4）；两版本均有真实评分与正式落库 | 全量回归用 Scripted/Replay | 新主题（python / redis）的评分质量；困难样本（V3） |
| 8 | Coach | `interview_report/` | **有真实调用但不完整**：18 轮原件库 2 条 `interview_coach`（68,248 tokens）、`reports=1`、`report_attempts=4`；`docs/83` 明确 Coach 真实调用被一条误判规则拒绝，第 19/20 题与报告记为未完成；`docs/88` UI 中查看过 Coach | `docs/84/85/93` 的 Harness 验收（Harness 默认 OFF） | 稳定产出最终 Coach 报告的完整 Live 闭环 |
| 9 | CapabilityMemory | `modules/memory/capability_service.py` | **有但样本小**：18 轮原件库 3 行；r27 8 行全属一次性合成用户；`docs/88` UI 批准记忆并读取下一场 MemoryGuidedPlan | 快照库 140 行为 DEMO | 真实用户跨场、独立验证、撤销/重检 |
| 10 | Training | `interview/training.py`、`training_candidates.py`（从已落库 session/evaluation 派生，无独立 training 表） | **无真实模型全链路 Live 证据**：正式库无 `%train%` 表；`docs/89` 明确 HTTP=0；`docs/90/95` 是 UI/幂等验收 | `test_private_training.py`、Replay | 「真实面试 → 真实训练建议」的端到端 |

### 样本量声明（必须）

快照库 `interview_agent_v231_context_20261026_a` 有 1,081 份简历 / 1,726 个 JD 版本 / 1,761 条评价 / 219 份报告 / 6,207 个 agent run，**但其 `model_admissions` 全是 `DEMO_V21_NOT_LIVE` 与 `test|offline`**：这是复制/回放现场，**不能按行数当作独立真实样本量**。真正带真实模型结算的独立现场只有：18 轮原件库（1 份简历、1 个 JD 版本、3 场会话、18 条评价）、r25（6 条）、r27（8 条，合成用户）以及 docs/97/99 的少量真实模型质量试验。V3 的 16 份是**待评测输入**，尚未产生任何模型评分。

---

## 5. 下一阶段最小可行端到端方案（**不执行，等待单独批准**）

### 5.1 先决条件（当前**不具备**完整业务测试条件）

1. 真实 JD 原件与来源核验：已有 4 份，但需确认授权/使用范围、补来源存档时间，并确认其中至少 1 份与目标候选人岗位匹配（现有 4 份均为 AI/科技岗）。
2. **真实简历缺失**：需 1–2 份脱敏真实简历（保留结构，去掉姓名/电话/邮箱/证件号）+ 候选人授权与岗位意向。这是 Job Matching 与真实用户链路的硬前提。
3. **人工参考答案 / 评分金标缺失**：`apps/api/tests/fixtures/evaluation_calibration_v1.json` 仍为 `DRAFT_UNREVIEWED`，`independent_gold_review_complete=false`，`range_policy` 自述「参考范围是待复核标注，不是生产阈值或自动评分器」。需人工复核后才能当准绳。
4. 合法输入条件：`PUBLISHED` JD version、`APPROVED` InterviewExperience、已生成的 CandidateProfile、合法的 Requirement→Skill 映射、`MODEL_LEDGER_DATABASE` 指向新 sandbox、`interview_coach_harness=false`、`purge_execution_enabled=false`。新主题还需补 `python` / `redis` 的蓝图 target。
5. 真实来源材料需**只读复制**进新 sandbox，不改正式库。

### 5.2 建议方案：`R28-REAL-JD-MVP`（1 条闭环，不追求样本量）

范围：1 份真实归档公开 JD → JD 解析与 Requirement/Skill 识别 → 1 份脱敏真实简历 → ResumeProfile → Interview Main（3 题）→ Evaluation（冻结 2.3.4）→ Coach 报告 → CapabilityMemory 观察 → Training workbench 读取。

**明确不做**：Live Search / JD Research 采集、Job Matching 模型排序（缺真实对照）、Coach Harness、L3/L4、任何删除/归档/清理、V3 全量评测。

| 项 | 预算 |
|---|---|
| 真实模型 HTTP | 硬上限 **12**（预计 1 JD 解析 + 1 画像 + 1 Skill 推荐 + 3 出题 + 3 评分 + 1 Coach ≈ 10，留 ≤2 次合法结构化修复） |
| Token | 硬上限 **70,000** |
| 时间 | 20 分钟 |
| 停止条件 | UNKNOWN / 配额错误 / 连续 2 次失败立即停；不自动充值、不切换 Provider；全部经 ModelGovernance 正式准入结算 |
| Provider | 仍为 ModelScope 免费 API-Inference、`deepseek-ai/DeepSeek-V4.1-Flash-1017` |

判据（不靠分数带）：① 每个环节的输入/输出可用 `来源 URL → sha256 → JD version → requirement → skill → 冻结快照 → 评价 → 报告统计 → 能力观察` 逐跳核对；② 人工参考答案在调用前冻结且与模型输出隔离；③ `covered_points` 每条必须有答案逐字依据；④ 不把复制库行数当样本量；⑤ 单条闭环只给**针对性结论**，不宣称统计意义。

**下一阶段最有价值的不是再跑 30 份 Evaluation，而是先把「真实 JD × 真实简历」这一对输入补齐**——因为第 2/3/4/5/10 环节的空白都源自同一处：缺真实候选人材料。

---

## 6. 尚需你收集的真实材料清单

| # | 材料 | 现状 | 需要你提供/确认 |
|---|---|---|---|
| 1 | 真实 JD 原文 | 4 份（`data/manual_sources/2026-10-05/`），1 official + 3 community；正式库 4 个 `PUBLISHED` | 使用授权与范围；来源存档（网页快照/抓取时间）；确认岗位仍适合作为测试目标；如需 Job Matching，至少 1 份与候选人意向匹配 |
| 2 | 真实面经 | 5 份 community 面经 + 18 轮本项目 Live 面试原件（基于合成简历） | 确认是否允许作为训练/出题语料；如需「真实面试逐字记录」，需 1 场真实简历 × 真实 JD 的完整记录 |
| 3 | **真实简历** | **完全没有**（现有 38 份多为测试简历，5 份 `PROCESSING` 且 `raw_text` 为空） | 1–2 份脱敏真实简历（保留教育/项目/技能结构，去掉个人标识）+ 授权 + 岗位意向 |
| 4 | 人工参考答案 / 评分金标 | `evaluation_calibration_v1.json` 为 `DRAFT_UNREVIEWED`，独立金标审核未完成 | 人工复核并签字；明确每题参考事实与可接受边界；确认后方可作为下一阶段准绳 |
| 5 | 真实候选人逐字回答 | 只有 V2/V3 的合成模拟回答与历史 Live 中基于合成简历的回答 | 至少 3–5 份与真实 JD 对应的逐字回答（含优劣混淆样本），用于检验 Evaluation 是否把「没说的知识」算成已回答 |
| 6 | 合法输入条件确认 | 3 个蓝图 target 已有；`python`/`redis` 缺 | 若要用 FastAPI/Redis 新题，需补含 `redis/缓存/分布式锁`、`python/GIL/协程` 的合法 JD Requirement，并确认技能 `priority_topics` 与蓝图候选一致 |

---

## 7. Git 与资产保护状态

- 基线 `5329036`；本轮按关注点分别提交：数据集（`10/11/12`）、契约测试、文档（`docs/107`）、项目记忆。
- **未创建任何新数据库**（库总数仍 243）；只读查询了正式库与历史库，**未写入**；MinIO 主 bucket 仍 48 对象；三闸 `purge_execution_enabled=false` / `interview_coach_harness=false` 未改；未启用 L3/L4。
- 未删除/归档/清理任何 JD、简历、面试、Memory、模型 Capture 或备份；未修改历史真实 Evaluation、未重标 2.3.3 / 2.3.4 结果；未改 Prompt、Schema、五维字段或迁移。
- 未 push / merge / rebase / reset / clean；未修改 `AGENTS.md`、`CLAUDE.md`、`.claude/` 等其他编码 Agent 的专属文件。
- **真实模型 HTTP = 0**：本轮所有测试均为本地 JSON 契约测试与只读 SQL / 文件检查，无任何出站模型请求。契约测试 **10 passed / 0 failed**；连同既有边界、引用、归属、正式链路与 V2 数据集契约共 **37 passed / 0 failed / 0 skipped**（抽样回归，未跑全量套件；产品代码零改动，上轮全量基线 1015 passed / 0 failed / 1 skipped 未被重新验证）。
