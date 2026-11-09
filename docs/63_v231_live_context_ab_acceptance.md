# V2.3.1 DeepSeek Live Context A/B 验收

日期：2026-10-27（Asia/Shanghai）。本轮仅一个有界真实批次，未改业务代码、Prompt定义、模型路由、密钥、会话上限或正式库。未commit/push。

## 1. 结论与边界

**真实Token节省及业务链路通过；质量等价不能宣称通过。**

- Evaluation配对input：10,851 → **3,045**，减少 **71.94%**。
- Main配对input：10,666 → **7,678**，减少 **28.01%**。
- 新版本正常API：创建 → Live Main → 按实际题目作答 → Live Evaluation → Controller合法结束 → 原幂等键重试，通过。
- 6逻辑 / **6 HTTP / 42,192 Token**，无Repair、fallback、超时、未知usage或非法模型引用；6条账本全部SETTLED。
- 两个Evaluation都识别了关键错误和缺口，但新版本五维各高1分，知识状态也不同，存在偏宽迹象，不能以“都通过Schema”掩盖质量差异。
- 当前200,000会话预算及原96KB planning guard未变；**不建议据此直接开始正式15–20轮Live验收**。

授权硬上限为6逻辑、8HTTP、100,000 Token、600秒；每次发送前按请求UTF-8大小、输出上限及缓冲预留，未知usage沿用保守规则。最后业务保存时批次耗时 **119.258秒**，6个HTTP耗时合计 **37.843秒**，随后主动停止验收API，没有并行批次或剩余额度追加调用。

## 2. 实验设计：可比性及局限

### 数据和历史

最终隔离库：`interview_agent_v21_v23_live_ab_20261027_b`，现有migration升级至0026，DEMO user/profile/company/job均ID1。简历和JD是明确人工构造的测试材料，不是真实求职者或公司招聘数据。

准备阶段通过现有service和明确标记的Replay模型建立三场历史：两次RAG独立正确观察，再一次相反观察；既有能力规则产生CONFLICTED。另建Session4，用Replay完成一题并留下待评价的第二答。**这些历史和前置题不是Live质量证据**。

冻结MemoryGuidedPlan包含：

```yaml
knowledge_id: ai_agent_application:rag_retrieval
state: CONFLICTED
intent: INDEPENDENT_VERIFICATION
priority: 8
needs_recheck: true
reason_tags: [INDEPENDENT_RECHECK_REQUIRED]
memory_revision: 3
observation_refs: [evaluation:1, evaluation:2, evaluation:3]
fingerprint: be30a5ce25f139cbd0b0a2e13c88741dda046bec0ceec94f391b989e0598c00a
```

### 旧分支不是被改写的旧Session

当前创建API不允许选择历史投影。本轮采用此前说明的**公开Agent输入边界反事实配对**：从Session4同一完整业务状态取得副本，旧分支使用代码中保留的Main2.2.1/Evaluation2.1.0及原投影；新分支使用冻结的ROLE_CONTEXT_V1/Prompt2.3.1。

旧输入副本不写回Snapshot，不提交其正式Evaluation或问题；只有新分支结果交给真实Controller提交。旧分支调用仍有AgentRun/StepRun、真实usage和配额记录，不能隐藏费用。

这不是两个可直接由生产创建接口选版本的独立旧/新Session，也**没有续跑任何历史旧Session**。本次证明旧Prompt/原投影调用边界仍可使用；旧Session完整运行兼容仍来自已有离线PostgreSQL测试，不冒充本轮重新完成的Live证据。

### 三个实验

- A：同一已持久化Answer5、Question20、rubric、当前目标与真实open_gaps，先旧Evaluation，再新Evaluation。
- B：两支使用**同一份A的新Evaluation实际结果**、相同回答、有效知识状态、Blueprint、完整Memory计划和合法候选。该选择在脚本执行前固定，不是事后挑选结果。先旧Main，再新Main。
- C：正常登录及创建/start/messages-stream接口，新建Session5，question_count=1。Codex只读取实际题目后作答，不预读rubric或参考答案。1题预算使Evaluation之后可合法结束，不额外调用第7次Main。

实际wire核对：A的answer、rubric、专业evaluation_anchors逐值相等；新输入没有MemoryGuidedPlan/approved memory/recent performance。B的 **28个组合ID、target/source关联、strategies、allowed_refs和完整Memory计划逐项相等**，没有裁剪候选空间。

## 3. 逐HTTP真实记录

全部为 `openai_compatible / deepseek-v4-pro-ga-260813`，当前Ark endpoint，thinking=disabled、输出上限3000、原timeout180秒；全部HTTP200、finish_reason=stop、reasoning_tokens=0、cached_tokens=0。

| HTTP | 实验/角色 | 输入契约 / Prompt | input | output | total | 秒 | supporting_refs | Controller |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | A旧 Evaluation | LEGACY / 2.1.0 | 10,851 | 490 | 11,341 | 8.124 | 不适用 | 业务校验通过，仅对照不提交 |
| 2 | A新 Evaluation | ROLE_CONTEXT_V1 / 2.3.1 | 3,045 | 530 | 3,575 | 8.136 | 不适用 | 新分支正式提交 |
| 3 | B旧 Main | LEGACY / 2.2.1 | 10,666 | 251 | 10,917 | 5.230 | 3 | 业务校验通过，仅对照不提交 |
| 4 | B新 Main | ROLE_CONTEXT_V1 / 2.3.1 | 7,678 | 312 | 7,990 | 5.631 | 3 | 新分支正式提交 |
| 5 | C新 Main | ROLE_CONTEXT_V1 / 2.3.1 | 4,564 | 216 | 4,780 | 4.494 | 2 | 提交Question22 |
| 6 | C新 Evaluation | ROLE_CONTEXT_V1 / 2.3.1 | 3,171 | 418 | 3,589 | 6.228 | 不适用 | 提交Evaluation6，结束Session5 |

合计 **input39,975 / output2,217 / total42,192**。没有真实Coach、Search或Fetch请求。没有读取账单/赠送额度，不把上述Token换算成已确认人民币费用或声称账单必为零。

每个请求保存实际system/input指纹、投影/Prompt版本、消息、业务JSON、耗时、usage、结构化校验、Repair标记及最终处置；不存Key、Authorization或hidden reasoning。

指纹前缀（完整值在原始记录）：

| HTTP | system_fingerprint | input_fingerprint |
| --- | --- | --- |
| 1 | 1f9e6c22bdf76 | 812a2c42f0a6 |
| 2 | 79e1c75ba31d | 044999d74c53 |
| 3 | 1cbd6806da7d | 4dab6c8bcf2f |
| 4 | 41ec8c7dfc8e | dbd56133d4da |
| 5 | 41ec8c7dfc8e | 59ed103e6abd |
| 6 | 79e1c75ba31d | 6fc6ef8a2447 |

指纹采用项目digest对消息/文本确定性JSON编码计算，不是供应商返回的tokenizer指纹。

## 4. 实验A：错误回答的评价差异

当前问题：为什么不能直接相加BM25和向量原始分数，检索不到依据时如何处理。rubric完整保留：分数量纲不可直接比较、RRF排名融合、真实引用和无证据拒答；当前未解决gap为“RRF排名融合与无依据拒答”。

相同实际回答：

> 我把 BM25 原始分数和向量余弦相似度直接相加，分数越大越相关，没有标注集。检索不到资料时让模型按经验补齐并生成引用。没有考虑量纲、排名融合和无证据拒答。

| 维度/诊断 | 旧 | 新 |
| --- | --- | --- |
| 技术/覆盖/深度/相关/表达 | 1 / 1 / 1 / 3 / 3 | 2 / 2 / 2 / 4 / 4 |
| knowledge state | NEEDS_CLARIFICATION | PARTIALLY_DEMONSTRATED |
| action recommendation | FOLLOW_UP | FOLLOW_UP |
| 原gap解决 | 无，resolved_gap_ids=[] | 无，resolved_gap_ids=[] |
| 原文依据 | 两段精确引用 | 三段精确引用 |

两版都明确指出：BM25与余弦分数不可直接相加、缺少RRF机制、无证据时不得编造引用；没有把该错误方案判为充分掌握，也没有把旧gap静默解决。

**质量疑点必须保留：**新版本把“承认没有考虑”列为覆盖点，将其视为部分展示；五维全部上升1分。新版本factual_or_reasoning_issues列表只单列了伪造引用风险，虽然评价正文与missing_points仍明确指出分数量纲问题。不能仅凭JSON合法或引用精确，就断言这种PARTIALLY_DEMONSTRATED分类合理或与旧版等价。

可能相关因素：旧输入包含本场此前的NEEDS_CLARIFICATION/观察，新版只保留未解决gap，按设计排除了历史状态锚定；同时Prompt增加了角色协议说明。移除历史状态也可能是在消除旧版偏严锚定，而不一定是新版变差；模型单次输出也不完全可复现。**N=1、Prompt版本和输入一起变化，无法把这1分差异因果归于某一删除字段。**

结论：错误/缺口识别及精确引用通过；数值评分和知识状态的一致性存在风险，需要后续有界、专项校准，不能宣称质量无下降。本轮没有为追求一致而改Prompt、评分规则、模型输出或追加调用。

## 5. 实验B：Main合法选择与实际追问

两版真实结构化选择相同：

```yaml
candidate_id: C_6521e4d3e28ce5a79fa409e9
resolved_knowledge_id: ai_agent_application:rag_retrieval
strategy: FOLLOW_UP
resolved_source: skill:ai_agent_application@1.0.0
reason_tags: [MISSING_MECHANISM]
supporting_refs: [answer:5, evaluation_run:29, skill:ai_agent_application@1.0.0]
```

引用数组顺序不同，不影响合法性。真实原始模型只返回candidate_id，知识目标与题源由程序精确解析，不由模型自由组合。

旧版实际问：

> 你提到没有考虑量纲和排名融合，那具体说说：BM25 的原始分数和向量余弦相似度为什么不能直接相加？如果改用 RRF，它又是怎么把两路结果合并起来的？

新版实际问：

> 你提到 BM25 原始分数和向量余弦相似度直接相加。请具体说明：为什么这两类分数在量纲和分布上不可直接比较？如果改用 RRF，它又是如何按排名而不是原始分数来融合的？

两者均针对真实错误回答和评价缺口，未随机转向其他项目，没有未知来源；当前源是通用Skill，不是公司真实面经。新版expected_points更详细，但不能用字数推断质量更高。

只有新版结果进入Controller，生成Question21，来源/目标/策略校验通过。旧版只做完整业务校验，不强行把对照结果写回新Snapshot。

完整冲突Memory计划确实进入两版Prompt，模型均选择相关目标。但当前回答本身也有相同缺口，reason_tags为MISSING_MECHANISM，**无法证明是Memory导致该选择**；本轮只证实计划完整可用、模型选择合法且相关，不声称记忆因果增益。

## 6. 实验C：正常API的一题Live交互

通过真实HTTP登录（不绕过鉴权）及原创建/start/SSE接口；DB依赖与core仅指向新隔离库和计量Provider，未Mock Controller或持久化。

Session5，question_count=1，新冻结ROLE_CONTEXT_V1/Prompt2.3.1，200,000 Token/7200秒上限未变。

Main实际问：

> 在 RAG 检索链路里，如果同时使用 BM25 和向量检索，你通常会在哪个阶段做融合？为什么不在更早或更晚的阶段融合？

Codex读取该问题后动态回答要点：两路独立召回后、统一重排和生成前做RRF融合；按chunk ID保留两路排名贡献；解释过早会失去互补信号、原始分数需校准，过晚到生成后才融合会损失证据且难追溯；说明RRF局限及未有生产规模验证。

实际Evaluation：**5/5/4/5/5**，DEMONSTRATED_IN_SESSION，精确引用三段当前回答，missing_mechanisms为空。其建议DEEP_DIVE只是建议，Controller按本场1题预算合法COMPLETE，不调用第7次Main。

- HTTP事件：evaluation → complete → done。
- Session5 COMPLETED，state_version=3，turn_count=1，active_question_id=null。
- Question22、Answer6、正式Evaluation6；原幂等键重发得到相同业务事件，没有新模型调用或重复记录。
- 这是一题真实交互，不是完整长面试或Coach闭环；其Blueprint只保留1个目标，不能用C单独证明跨目标自由选择能力。

Memory聚合后仍CONFLICTED、存在OPEN历史冲突。一次新场景正确回答不会静默覆盖旧矛盾；旧Session快照未漂移。以上是“Replay历史＋Live决定/评价”，不是完整多场次Live。

## 7. Token收益、估算偏差与延迟

| 配对 | 原实测input | 新实测input | input减少 | 含output的total减少 |
| --- | ---: | ---: | ---: | ---: |
| Evaluation | 10,851 | 3,045 | 71.94% | 68.48% |
| Main | 10,666 | 7,678 | 28.01% | 26.81% |

按本次**相同输入**复用docs/62的UTF-8/词法代理校准：

| 角色 | 离线代理预测新input | 实测 | 实测相对预测 |
| --- | ---: | ---: | ---: |
| Evaluation | 3,711.04 | 3,045 | 低17.95% |
| Main | 8,322.27 | 7,678 | 低7.74% |

此前不同7请求样本的中性节省估计为Evaluation65.78%、Main25.35%；本次分别高约6.16和2.66个百分点，但状态/问题不同，不能把它当作同样本精确预测误差。不能把C的一题短Blueprint直接与旧4目标开场比较来夸大节省。

实际延迟**没有配对改善**：Evaluation8.124→8.136秒，Main5.230→5.631秒。N=1不足以判断速度变化，输出长度、调度和服务商波动都会影响耗时。Token节省不等于延迟同比下降。

## 8. 更新15/20轮预算：条件性敏感性分析

仅以新版本本次小样本估算：开场Main4,780；后续Main7,990；Evaluation两个新样本均值3,582（均含output）。N个有效回答大约需要N次Main（含开场）+N次Evaluation，最后一答不再出题。

中性基数：`4780+(N−1)×7990+N×3582`。节省/中性/偏高基数系数0.85/1/1.2；第三答之后每轮新增总输入增长仍取200/450/900，累计增长项为`growth×(N−3)×(N−2)/2`。

系数、增长、Coach与异常预留是显式规划参数，**不是从两对样本拟合出的统计上界**；不能假设每轮不增长，也不能准确预测20轮。

| 情景 | 回答数 | Main+Evaluation | Coach预留 | 异常预留 | 整批合计 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 节省端 | 15 | 160,414 | 15,000 | 10,000 | 185,414 |
| 中性 | 15 | 205,470 | 25,000 | 20,000 | 250,470 |
| 偏高/预算保守端 | 15 | 274,644 | 40,000 | 40,000 | 354,644 |
| 节省端 | 20 | 224,596 | 20,000 | 12,000 | 256,596 |
| 中性 | 20 | 297,080 | 35,000 | 25,000 | 357,080 |
| 偏高/预算保守端 | 20 | 411,576 | 55,000 | 50,000 | 516,576 |

Coach本轮没有真实调用，预留仍是假设。正常Memory规则整合不新增模型请求。未来更多项目/引用、长回答或Repair可能超出上述情景。

200,000是整场Main/Evaluation累计usage及保守结算，不是每轮额度；独立Coach另计。在约40,000下一请求准入头寸下，敏感性约支持14/12/9答；真正结束还受Controller、目标覆盖和96KB投影前planning guard限制。

即使成本实测优于离线估计，也不能保证20答。若后续质量复核通过，可单独评审约300,000支持中性15答、400,000–450,000支持中性20答；偏高20答仍可能需要550,000–600,000或缩短训练。**本轮未改上限，也没有任何后续费用授权。**

## 9. 数据、账本与恢复核对

- 新隔离库6条真实ModelAdmission全部SETTLED，actual_tokens与6个HTTP/StepRun合计一致：42,192。全部真实StepRun SUCCEEDED、structured_retry=0。
- Session4：IN_PROGRESS、state_version=5、turn_count=2、active_question_id=21；含1个Replay前置评价和1个正式Live评价。
- A的旧/新Evaluation各有一个真实成功审计，这是有意的计费配对；**正式InterviewEvaluation只有新版1条**，不是重复有效评价。
- Session5：1 Answer / 1正式Evaluation / 1 Question，无重复记录；两个Session均无遗留lease、模型预留或未知usage。
- A新Evaluation已通过`reusable`按原输入复用，run_id仍29；同键重试Session4和正常API重试Session5均未增加HTTP。
- 只读离线副本注入未知引用/组合，分别得到UNKNOWN_DIRECTOR_REFERENCE / UNKNOWN_DIRECTOR_CANDIDATE；未为测试故意多发真实请求。
- 本轮无Live失败，故没有新做“真实超时/Main失败后恢复”注入；该能力仍依据此前PostgreSQL回归，不把它冒充本轮Live实测。
- 正式库前后全表指纹一致，仍0025/64表。历史失败和成功短程库的Session、Snapshot、Answer、Evaluation、Question、quota逐项不变。
- 仅正常启动原有PostgreSQL/Redis容器，核对原ID/镜像/卷；没有Compose重建、prune或数据盘操作。

## 10. 准备问题、材料与未验证项

准备阶段曾出现Replay脚本问题：1题Blueprint在已验证主题降优先级后不再包含RAG；后续准备脚本又遗漏当前回答引用、重复题面，被既有校验正确拒绝。只修改了验收脚本，未改业务实现或关闭校验。

第一次准备库`..._20261027_a`保留；最终使用b库。b库修正后用原幂等键恢复，未删除失败审计。恢复入口明确要求该库DeepSeek StepRun数量为0；**这些全部发生在唯一收费批次之前，不是反复调用模型挑成功样例**。

本轮仅新增：

- `apps/api/scripts/accept_v231_ab.py`：Replay准备、单批预算/审计、公开Agent边界A/B及原API C。
- `apps/api/scripts/inspect_v231_ab.py`：本批专用只读核对与非法引用离线拒绝。
- 本文及docs索引。既有未提交工作全部保留。

主要本地ignored证据：

```text
.runtime/acceptance/v231_ab_preparation_20261027_b.json
.runtime/acceptance/v231_ab_live_20261027_b.json
.runtime/acceptance/v231_ab_final_20261027_b.json
.runtime/acceptance/v231_ab_formal_before_20261027.json
.runtime/acceptance/v231_ab_formal_after_20261027.json
.runtime/acceptance/v231_ab_old_success_protected.json
.runtime/acceptance/v231_ab_old_failure_protected.json
```

实际检查：6次真实模型、正常HTTP API、真实隔离PostgreSQL、4项预算边界零HTTP自检、脚本compile及diff检查。本轮未重跑530后端/43前端，也未运行新浏览器E2E，不把之前结果算成本轮重跑。

最终判断：Token节省、来源/策略合法性、正式提交、幂等和账本具备真实证据；评分/知识状态分类有待复核，旧Session完整Live续跑与长程上下文增长未验证。**不建议立即正式启动15–20轮Live**；需先决定如何复核评分偏宽及后续会话预算，再单独授权。批次已经停止，不继续开发或收费测试。
