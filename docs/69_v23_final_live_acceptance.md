# V2.3 最终长面试 Live 验收：第二答业务校验失败，按边界停止

日期：2026-10-29（Asia/Shanghai）。本次只有一个收费批次。**未通过完整 Live 闭环验收**，不能将此次结果写成 15/20 轮、Coach 或跨场次 Memory 已通过。

## 1. 范围、环境与前置检查

- 模型实际为 `deepseek-v4-pro-ga-260813`，Main / Evaluation 均为 thinking disabled，没有改路由、密钥或 `.env`，没有 fallback。
- 新隔离库：`interview_agent_v21_v23_live_final_20261029_a`，现有 migration 仅在这个新空库升至 `0026`；正式库保持 `0025`。没有新增 Schema 或 migration，没有重建容器或卷。
- 通过真实浏览器登录隔离 DEMO 用户，从 `/interview` 选择已发布的 DEMO AI/Agent 岗位、AI Skill、技术一面、标准风格、自适应、20 轮，正常创建 **Session 4**。
- 简历/JD 为明确标识的合成 DEMO，并非真实求职者/公司招聘事实。3 场历史观察为明确标识的 **Replay**，仅用于冻结 `CONFLICTED` 的 RAG 能力记忆。本场的 Main / Evaluation 全部为 **Live**，不是完整多场次 Live。
- Main 冻结 `ROLE_CONTEXT_V1`、Prompt `2.3.1`；Evaluation 冻结 `FOLLOWUP_SCORING_V1`、Prompt `2.3.2`；合法组合仍为 `TARGET_SOURCE_PAIR_V1`。
- 已加载 SupportingRefs / EvaluationQuotes 的单次受约束 Repair，上限分别仍为 12 / 3。本次没有触发 Repair。
- 快速离线预检查本轮实跑：`test_evaluation_quote_reliability.py`、`test_director_reference_repair.py`、`test_m2b_models_evidence.py`，**71 passed**（含隔离 PostgreSQL 测试）；不是全仓重新回归结果。
- 已读 docs/61～68 及当前生产实现。docs/67 的 Outbox/Offset 混淆出现在历史模型生成的评价/参考答案，不是当前通用 Skill 的固定规则。本次选中的冻结 JD、Skill、题源及实际两题 Rubric 没有该已确认错误；没有把历史错误答案作为本次评分依据。
- AI Skill 的现有知识目标为 RAG、Agent、LLM、Python、系统设计，没有独立 Kafka 目标；DEMO JD 包含 Kafka 异步任务的系统设计要求，不扩大题源/目标权限。由于提前失败，本次没有实际考到 Kafka 等其他方向。

### 硬预算

批次硬上限：600,000 Token / 50 逻辑请求 / 60 HTTP / 5,400 秒。隔离验收进程将**新建 Session**上限冻结为 550,000 Token；正式默认 200,000 不变。每个 HTTP 发送前，按请求字节数、最大输出和安全余量预留并检查批次余额；应用自身 Session 预算、用户配额预留和结算仍有效。没有取消任何保护。

## 2. 每轮真实对话与决策

模拟候选人回答由 Codex 在看到实际问题后写出，未预读本题隐藏 Rubric、评价或参考答案；未额外请求 DeepSeek 生成候选人回答。Codex 自身服务用量不计入下述 DeepSeek API 账本，也未取得可独立量化的 Codex Token 数据。

### 第 1 题：开场 NEXT

**Main 实际问题：**

> 在 RAG 检索链路里，如果同时使用 BM25 和向量检索，你通常如何把两路结果合并成最终排序？请说明你采用的融合方式，以及它相比直接按单一分数排序解决了什么问题。

**实际提交回答：**

> 我在检索项目里先各取BM25和Dense的前二十条，再用RRF融合。因为两路原始分数的量纲不一样，不能简单比较。我理解RRF是给排名靠前的结果更高权重再求和，不过当时融合常数k直接沿用了默认值，没有系统调参；同一文档被切成多个chunk时，我只按chunk ID去重，还没有处理相近片段重复占位。

**正式 Evaluation 4：**

- 五维：技术正确性 4 / 覆盖 4 / 深度 3 / 相关性 5 / 表达 4。
- 状态：`PARTIALLY_DEMONSTRATED`，正式入库。
- 识别到分数尺度不同、按排名融合，以及 k 未调参、相近 chunk 重复占位的边界；尚缺具体公式、融合减少单路偏差/漏召回的机制、重复片段影响。
- `answer_quotes` 3 条，均为当前原始回答中的连续片段；没有 Repair。
- 保存 3 个 open gaps：
  1. `gap_ebf04b4e255cc571e5412c3e`：未说明 RRF 的具体计算公式及 k 值作用；
  2. `gap_a304838ff6b4c044b3f363f8`：未解释融合如何降低单一检索方式的偏差或漏召回；
  3. `gap_e966b66c767a0f23b3615679`：未说明相近 chunk 重复占位对最终排序的具体影响。

**Main 后续选择：**

- `FOLLOW_UP`，`MISSING_MECHANISM`；目标 `ai_agent_application:rag_retrieval`。
- 合法组合 `C_2a1341950f83b2a1ceb5144f`；对应 `skill:ai_agent_application@1.0.0`，开场和追问均使用这个组合。
- 开场 supporting_refs 为 `[skill:ai_agent_application@1.0.0, requirement:6]`（2 条）。
- 追问 supporting_refs 为 `[skill:ai_agent_application@1.0.0, answer:4, evaluation_run:9]`（3 条）。
- Controller 接受并提交 Question 5，父题为 Question 4；状态版本从开场 1 经回答阶段进入 3。
- 追问直接针对候选人承认的 k 默认值，不是无关随机切题。没有 `UNKNOWN_DIRECTOR_SOURCE`。

### 第 2 题：RRF 参数追问，失败停止

**Main 实际问题：**

> 你提到 RRF 的融合常数 k 直接沿用了默认值。请具体说明 RRF 的计算公式，以及 k 值变大或变小时，对最终排序通常会产生什么影响。

**实际提交回答：**

> RRF对一个chunk的得分是各路1除以k加上该chunk在这一路的排名之和，排名从1开始；没有召回到的那一路不贡献分数。k大时不同名次的权重差更平缓，多路同时出现的结果更占优势；k小时头部名次的优势更明显。它用排名避免直接比较BM25和向量分数，但会丢失原始分数差距。我没有线上指标，只能用固定的人工相关性样本比较Recall和排序效果，不能说默认值一定最好。

**实际模型结果（未成为正式 Evaluation）：**

- 五维：5 / 5 / 4 / 5 / 5；本题 Rubric 的公式、排名起点、未召回贡献、k 影响均被识别。
- `state=DEMONSTRATED_IN_SESSION`，`missing_mechanisms=[]`，`missing_points=[]`。
- `resolved_gap_ids` **只有** `gap_ebf04b4e255cc571e5412c3e`。
- 3 条 answer_quotes 均可在当前原始回答中定位，数量合法。
- 模型理由指出当前问题的全部 Rubric 被覆盖，同时建议后续再讨论重复片段；没有宣称实际回答已经解决另外两个旧缺口。

**Controller 前置业务校验拒绝：**

`adaptive.py::resolve_observations` 将所有未显式解决的旧 gap 保留，再合并新 gap；当观察为 `DEMONSTRATED_IN_SESSION` 且仍有 remaining gaps 时，抛出 `UNRESOLVED_KNOWLEDGE_GAP`。

本题旧 gap 2、3 仍存在，因此不能把当前知识目标整体标为充分展示。拒绝发生在 Evaluation 的业务校验阶段，**不是下一题 Main 失败**；没有调用第三次 Main，也没有创建第三题。

精确区分：

1. **Schema 层通过**：引用数量、格式及评分范围合法；不是 answer_quotes 越界。
2. **知识观察契约不一致**：模型评价“当前追问题已答好”，却输出了服务端要求“该知识目标无剩余缺口”的整体 demonstrated 状态。
3. Prompt 同时要求“仅按当前题/Rubric 评分”及“存在任何旧 gap 不得 demonstrated”；本次模型遵守了前者的局部语义，没有满足后者的知识目标级状态约束。父语境和三个 gap 都已实际发送，不能归因于字段被删掉。
4. 旧 gap 2、3 不是本追问题干要求的内容，不能为使状态通过就伪造其已解决，更不能强迫改低五维分数。
5. 服务端保留缺口并硬拒绝，符合现行契约；这暴露了长面试中**本题完成度与知识目标完成度的边界问题**。尚未在本轮修改或修复。

对同一已保存真实输出，仅离线重新执行 `AdaptiveEvaluation.model_validate` 和 `resolve_observations`，稳定复现同一个错误；没有重新收费请求或伪造输出。

## 3. 逐 HTTP 真实用量与延迟

| HTTP | Agent / Prompt | 输入 | 输出 | 合计 Token | HTTP 延迟 | 结果 |
|---|---|---:|---:|---:|---:|---|
| 1 | Main 2.3.1 开场 | 6,446 | 254 | 6,700 | 4.942s | 提交 Q1 |
| 2 | Evaluation 2.3.2 首答 | 3,316 | 536 | 3,852 | 9.433s | 正式评价提交 |
| 3 | Main 2.3.1 追问 | 7,830 | 269 | 8,099 | 4.647s | 提交 Q2 |
| 4 | Evaluation 2.3.2 第二答 | 3,799 | 620 | 4,419 | 8.716s | Schema 通过，业务观察拒绝 |
| **总计** | **4 逻辑 / 4 HTTP** | **21,391** | **1,679** | **23,070** | **27.738s 累计 HTTP 延迟** | **立即停止** |

- Main：14,799 Token，累计 9.589s；Evaluation：8,271 Token，累计 18.149s。
- Repair：0 请求 / 0 Token；Coach：0；下一场 Main：0。
- 全部 HTTP 200 / finish_reason stop / reasoning_tokens 0；服务商报告 cached_tokens 0。
- 从验收服务启动至失败保存：208.706s（包括浏览器操作及 Codex 构造回答，不是纯模型延迟）。
- 实际费用金额未读取服务商账单，本报告只陈述真实 usage，不编造人民币费用。

### 15 轮检查点、每 5 轮曲线和上下文增长

未达到第 5 / 15 / 20 个有效评价，**无相应检查点和 Token 曲线**，不得补造或按两次请求外推实测长场结果。

可报告的只有：开场累计 6,700；第 1 个有效答评及其下一题累计 18,651；第二答失败后累计 23,070。

Main 两次输入 6,446 → 7,830；Evaluation 两次输入 3,316 → 3,799。只能观察短程增长，无法由此证明 15～20 轮预算或长期输入稳定性。

## 4. PostgreSQL 现场与预算一致性

全部为停止后实际只读检查，未修正历史行：

- Session 4：`IN_PROGRESS`，`state_version=4`，`active_question_id=5`，`completed_at=NULL`。
- 2 条 Question：Q4 `COMPLETED`；Q5 `ANSWERED`；没有第三题。
- Answer 4：`COMPLETED`，幂等键 `FINAL-LIVE-Q-4`。
- Answer 5：`FAILED`，错误 `UNRESOLVED_KNOWLEDGE_GAP`，幂等键 `FINAL-LIVE-Q-5`。两条原始回答均已保存。
- **正式 Evaluation 只有 1 条**（ID 4）；第二答的高分原始输出不计为正式评价。
- AgentRun 8/9/10 成功；AgentRun 11 失败。同一次调用的 StepRun 11 为 `SUCCEEDED`，表示模型及结构化阶段成功，不能与最终业务 AgentRun 的 FAILED 混为一谈；Step 的 output_json 只有调用 metadata，不是可直接复用的正式合格 Evaluation。
- 所有 4 条 ModelAdmission 均 `SETTLED`，每条 actual_tokens=charged_tokens，总和 **23,070**。
- Session 总用量 **23,070**；reserved_model_tokens=0，model_call_reservations={}，unreported_model_tokens=0；没有重复扣减或未知保守用量残留。
- `answer_processing=NULL`，`budget_exhausted=false`；没有因本次失败错误 COMPLETE 或扣完题目预算。
- 没有重复有效 Answer/Evaluation 或重复 Question。**本场没有实际发送同键重试**，不能用“当前没有重复”替代完整幂等实测；相关离线 PostgreSQL 回归通过，收费失败后未为了覆盖幂等继续请求。
- 本场不是 Evaluation 已完整业务成功而 Main 失败；第二答的无效观察不能冒称已验证 Evaluation 供后续直接复用。
- Snapshot 从开场到失败读取完全一致，指纹 `1a13ee8039d4b94b9ee76fcefd4ada54ded126444e67622c1784975a09a540fc`。

正式库及原有 Live/校准库共 12 个受保护库的逐表内容指纹，与准备前一致。正式库仍有 64 张表、1,699 行（含元数据），指纹 `cf4faece3c47468fb8cd9aa05427477f619450042a7f4516788f5e44fc40fcaa`。单独的离线 reliability 测试库有正常测试新增记录，不属于正式数据。没有修改旧失败 Session 或旧用量。

## 5. 浏览器实际结果

- 独立 Playwright 浏览器完成登录 → 选择岗位/AI Skill/20 轮 → 创建 → Live 首题显示。
- 两次回答使用同一真实后端 SSE 接口提交，并非静态 DEMO 或 Replay Controller。
- 失败后实际刷新恢复原 Session，页面显示“本轮处理未完成，回答已保存；可以安全重试原回答”，保留两条回答和第二题，展开技术详情可见错误码；没有假装已有第二条正式评价。
- 刷新没有调用模型，没有新建有效记录；浏览器 error console 为 0。
- 截图：`output/playwright/v23-final-opening.png`、`output/playwright/v23-final-stopped-rendered.png`（ignored）。
- 失败后没有点击重试。本批专用 API :8007 和前端 :5177 已停止，未停止 PostgreSQL/Redis 或其他项目进程。

## 6. Coach 与跨场次 Memory：未执行，不能宣称通过

本场没有合法 COMPLETE，因此 **未调用 Coach、未生成 Final Report、未创建下一场 Session、未调用下一场 Main**。

- 本场前后能力记忆仍为 RAG `CONFLICTED`、revision 3；来源为明确 Replay 的 Evaluation 1/2/3，冲突未被错误消除。
- 本场首答有效观察保存在当前 Session 知识状态中，但本场尚未完成，未混入已完成场次的能力聚合；没有为通过验收人工刷新或升级 VERIFIED。
- 第二答父语境确为 Q4 / Answer 4，来自同用户同 Session；`assistance_status=UNKNOWN` 保持原值，未作为无提示或独立验证证据。
- 冻结 MemoryGuidedPlan 包含 RAG CONFLICTED / INDEPENDENT_VERIFICATION / revision 3 / evaluation:1,2,3。开场选择了相关目标，但不能仅凭目标重合断言记忆产生了因果效果。
- 未验证“本场成果进入下一场 MemoryGuidedPlan”，也未验证完整 Coach 引用、训练中心反馈或长场的主题切换。

## 7. 修改范围、证据与下一步边界

本轮只新增验收包装 `apps/api/scripts/accept_v23_final_live.py` 和本报告；另有 ignored 的临时 Vite 代理配置与运行审计。没有修改业务代码、生产 Prompt/评分规则/Schema/Memory/Controller，没有 commit/push。验收包装的只读核对曾因 Snapshot 关联列名假设错误报 SQL 错误，修正只读查询后完成；不影响业务事务或调用计数。

主要 ignored 证据：

- `.runtime/acceptance/v23_final_preparation_20261029_a.json`：预检查、Replay 身份、旧库指纹、路由与初始能力。
- `.runtime/acceptance/v23_final_live_20261029_a.json`：4 次真实请求业务输入/输出、逐 HTTP usage、状态审计。不含认证 Header/密钥或隐藏 reasoning。
- `.runtime/acceptance/v23_final_live_20261029_a_readonly_final.json`：停止后只读 DB、账本、旧库指纹、同输出离线复现。

**综合结论：本次完整 V2.3 Live 闭环未通过。** 已验证前两次合法 Main、首答正式 Evaluation、第二答失败时的预算与状态保护、刷新恢复；没有验证长面试、Coach 与下一场 Memory 闭环。

下一步只建议先做离线设计审查：明确五维当前题评分、当前追问 gap 完成度和知识目标全部 gap 完成度的不同语义，使观察状态与未解决缺口契约一致。保留原始分数和真实未解决 gap；不能放宽来源校验、伪造已解决 gap、或把本题正确机械改成低分来过关。若后续需要业务校验修复，也应独立确认范围和预算，不能复用本次授权继续刷模型。

秋招演示可如实展示动态 RRF 追问、冻结来源、真实 usage 和失败保护；**当前不能承诺 15～20 轮 Live + Coach + Memory 的稳定产品闭环**。本次问题是阻断性可靠性风险，不只是参考答案措辞问题。
