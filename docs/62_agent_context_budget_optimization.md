# V2.3.1 Agent Context Budget Optimization

日期：2026-10-26。仅模型输入投影及调用适配；收费模型调用 **0**。未改模型路由、密钥、`.env`、评分、Memory规则、Controller、合法组合契约、会话200,000上限或数据库Schema。

## 1. 基线、测量口径与真实浪费

使用 docs/61 同一批保存的7个HTTP请求，而不是另选较小简历：4 Main + 3 Evaluation，原实测 input **75,942**、output **2,919**、total **78,861 Token**。Main原Prompt2.2.1，Evaluation2.1.0。

本机没有可用的DeepSeek tokenizer（tiktoken/tokenizers/transformers也未安装）。没有安装新依赖或联网下载tokenizer。以下明确区分：

- **优化前usage：服务商实际返回值**，包含缓存输入，不再重复加缓存Token。
- **优化后：估算而非usage**。对同一请求分别计算UTF-8字节比、CJK/标识符/数字/标点词法单元比，以原实测input校准；中性取两种估算均值。
- 节省端采用较低估算×0.8，偏高采用较高估算×1.2，作为敏感性范围，**不是统计置信区间或DeepSeek分词精度保证**。整请求的BPE边界、不同JSON字段的分词率、中文比例及后续输出变化均会产生误差。

主要事实：

1. Evaluation实际收到4/4/3份choices及28/28/19个下一题candidates；两种结构重复表达同一目标/来源空间。它们用于Main选下一题，不承担当前五维评分或本题原始引用核验。
2. Main中每条候选重复label/topic、source fingerprint及来源语义；多个候选关联同一知识点或题源，适合共享定义，但不能删除组合。
3. knowledge_state.items和observations反复包含相同answer_quotes、answer_spans和完整open_gaps。偏移用于服务端原文定位，不需要模型反复读取；历史缺口、解决记录和引用本身仍需保留。
4. training.before、请求键及请求指纹服务于反馈/审计，不是Main应据此推断候选人能力的评分依据。
5. 历史题目投影仅约182→557紧凑JSON字符，不是主要成本。本轮保留全部题目历史，不引入摘要模型或历史截断。

词法校准的**粗略区块归因**：三个Evaluation中，candidates+choices分别约5,570 / 5,330 / 3,370 Token；不能当成逐区块精确usage。Main中候选空间约2,050–3,430估算Token、Skill配置约1,630–1,860，知识状态约34→2,010；这些并不全是可删除内容，真正必要部分仍然保留。

## 2. 两个角色的输入契约

### Evaluation：只评当前题

保留：

- 原始answer和当前问题逐值不变；rubric完整保留。
- active_knowledge_id，冻结知识目标的名称、主题、用途和范围。
- 当前知识目标的真实来源范围：相关Requirement、公开题目、CandidateFact及其真实Evidence；没有用模型摘要代替原始证据。
- 五个专业evaluation_anchors及共享1..5评分契约；不按面试风格改分。
- 当前目标所有未解决gap的真实ID和文本，供resolved_gap_ids精确引用。

删除：下一题choices/candidates、调度/冷却、allowed_refs、历史题目、其他目标状态、完整历史观察、training.before，以及无关身份/指纹包装。

MemoryGuidedPlan、approved memory、recent performance仍先由既有role sanitizer排除；新投影再使用明确字段白名单。当前目标只传open_gaps，不传其历史“能力等级”或旧评分，避免锚定。新增说明：**来源用于理解当前题，不是额外评分清单；只按当前问题和rubric评分**。

为避免误删评分依据，本版保留该知识目标的所有冻结来源，而非仅凭关键词挑一个excerpt。它未必已达到理论最小Token，但不以瘦身为理由丢失支持材料。

### Main：完整动作空间，避免重复展开

每个candidate仍包含原`candidate_id`、`target_knowledge_id`、`source.ref`和原strategies，顺序不变。共享字典：

```text
adaptive.candidates: 原有合法组合，每个组合保留明确关联
adaptive.targets[knowledge_id]: label/topic/purpose/scope/priority
adaptive.sources[source_ref]: source_type/source_id/kind/来源语义
snapshot: JD要求、简历事实及真实证据、公开题目、Skill/轮次/风格
```

源正文不在共享字典重复复制，仍通过Snapshot的requirements、candidate_facts/evidence、public_questions对应。保留通用Skill与REPORTED_TOPIC的语义区别，不把它们说成公司逐字真题。

完整保留MemoryGuidedPlan及其版本/指纹/观察引用；保留当前回答、相关Evaluation、完整已问题目历史、最新状态/未解决gap，以及逐次观察中的answer_quotes、missing_mechanisms、resolved_gap_ids和问题/评价ID。

仅删除重复的answer_spans/位置、历史观察中重复累计的open_gaps、Skill注册Manifest、纯服务端指纹与版本包装、Main不使用的评分anchors及training.before/请求键。Main仍保留Skill正文、专业策略、轮次、风格和公司资料不足时的回退说明。

**没有裁剪候选ID、允许策略、allowed_refs或Memory计划；没有创建别名替换原有题源契约。** 共享字典不授予任意target×source交叉组合权限。

## 3. 服务端验证、冻结与幂等

- 新Session冻结 `adaptive_policy.context_projection=ROLE_CONTEXT_V1`，Main/Evaluation Prompt均为 **2.3.1**。
- 历史Session没有该字段，继续走原投影。旧Prompt常量保留；未知投影版本或投影/Prompt不匹配明确拒绝，不猜测兼容。
- 模型DTO是深拷贝。原InterviewContextSnapshot、Blueprint、Memory计划和Controller状态没有被精简或更新。
- `resolve_selection`、`validate_decision`、SupportingRefsRepair及Controller使用完整服务端payload。模型返回的组合仍必须属于当轮合法集合，不能通过共享字典自由拼接。
- supporting_refs仍上限12，必须真实，Repair仍最多一次、只能选原合法引用子集、不能改变其他字段。本轮没有改这些规则。
- AgentRun输入Hash继续取完整的role-sanitized payload；包含冻结的新版本标记。`digest`排序字典键，重建字段顺序不会改变Hash。新wire DTO也确定性排序字典，语义列表不排序。
- `reusable`仍要求同Session/Answer、输入Hash、模型、Provider、Prompt、成功且通过校验的阶段结果全部一致。没有通过只比较压缩字符串来扩大复用范围。
- 新Session的预留改用**同一角色投影**，加system/schema及原有双输出/repair缓冲；旧Session的预留行为不变。Controller原预算判断、在途预留、未知usage保守计费和实际usage结算保持原实现。

## 4. 七个相同输入的前后对照

所有新输出仅为固定Replay：用原7个Live业务JSON验证新投影对应的Schema及原始完整业务校验，全部通过。没有用Replay证明“真实模型决策质量无下降”。Main全部合法组合ID/关联/策略与allowed_refs逐项一致；Memory计划完整；Evaluation原始回答、rubric和open_gaps逐值一致。

| HTTP | Agent | 原实测input Token | 新input中性估算 | 新input敏感性范围 | 原/新消息chars，仅辅助 |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | Main开场 | 8,889 | 6,683 | 5,213–8,220 | 23,971 / 17,600 |
| 2 | Evaluation1 | 10,913 | 3,845 | 3,006–4,718 | 28,254 / 9,280 |
| 3 | Main2 | 10,961 | 8,218 | 6,474–10,013 | 28,345 / 20,909 |
| 4 | Evaluation2 | 12,051 | 3,916 | 3,091–4,761 | 30,512 / 9,555 |
| 5 | Main3 | 11,805 | 8,673 | 6,860–10,525 | 29,820 / 21,762 |
| 6 | Evaluation3 | 10,666 | 3,748 | 2,989–4,512 | 26,719 / 9,592 |
| 7 | Main4 | 10,657 | 8,013 | 6,389–9,648 | 26,921 / 20,279 |

| 汇总 | 原实测input | 新估算input | 中性估算减少 |
| --- | ---: | ---: | ---: |
| Main | 42,312 | 31,587 | 25.35% |
| Evaluation | 33,630 | 11,509 | 65.78% |
| 全部input | 75,942 | 43,096 | 43.25% |

若仅为离线对照而保持原output2,919不变，total从78,861变为估算46,015，减少 **41.65%**；范围36,941–55,316。不是新Live usage，也不代表响应耗时同比下降。一个正常后续“Evaluation＋下一题Main”均值约从23,250降到13,036 Token（中性、固定原output假设），约减少43.9%。

### 固定与动态区块

固定区块口径：system+schema+Snapshot；动态区块：当前题/答案/评价、历史、知识观察、当轮候选与调度。候选虽来自固定Blueprint，但可用集合动态变化，故归入动态块。真实JD/项目数量改变时固定块也会改变，此处“固定”只指同一场样本。

| 角色 | 固定区块减少（UTF-8 / 词法规模） | 动态区块减少（UTF-8 / 词法规模） |
| --- | ---: | ---: |
| Main | 23.37% / 25.32% | 28.61% / 23.35% |
| Evaluation | 18.60% / 19.18% | 91.31% / 89.72% |

这是可复算的结构规模，不将其标注成精确区块Token。新Prompt增加角色协议说明、Schema未删字段；不是通过省掉评分定义获取这些比例。

## 5. 15 / 20轮敏感性预算

假设确实产生N个有效回答且没有提前合法结束：约N次Main（含开场，最后一答后不再出题）+N次Evaluation，另加独立Coach。不能为凑轮数禁止Controller正常结束。

公式：`opening + (N−1)×后续Main均值 + N×Evaluation均值 + growth×(N−3)×(N−2)/2`（增长项N>3时才计算）。均值来自上表三档估算，output先保留原样本水平。growth取每轮新增合计 **200 / 450 / 900 Token**，明确计入知识观察、历史、答案/gap与来源复杂度增长，而非假设每轮恒定。

growth及Coach/异常预留是**规划敏感性参数，不是20轮实测拟合值**。现有短样本无法校准长程Token增长或精确输出长度；更多项目/来源、较长回答、更多Repair可能超出表中范围。

| 情景 | 轮数 | Main+Evaluation估算 | Coach预留 | 异常/重试预留 | 整批合计估算 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 节省端 | 15 | 171,726 | 15,000 | 10,000 | 196,726 |
| 中性 | 15 | 228,989 | 25,000 | 20,000 | 273,989 |
| 偏高/预算保守端 | 15 | 302,686 | 40,000 | 40,000 | 382,686 |
| 节省端 | 20 | 239,238 | 20,000 | 12,000 | 271,238 |
| 中性 | 20 | 327,924 | 35,000 | 25,000 | 387,924 |
| 偏高/预算保守端 | 20 | 448,311 | 55,000 | 50,000 | 553,311 |

Coach当前仍读取完整Snapshot、问题和评价及review_projection，另受200,000输入字节上限及6,000输出Token限制；本轮未优化Coach，也没有新的真实Coach usage。表中的预留不是实测费用。终局Memory/统计的确定性更新不新增模型调用；没有暗设每轮额外Memory模型请求。

### 200,000上限与建议

该Session上限累计Main/Evaluation实际usage、失败/Repair与未知消耗，再考虑在途预留；**不是模型上下文窗口，也不覆盖独立Coach的全部费用账本**。新投影没有修改它。

另一个未改变的限制：原96,000字节`planning_payload` guard仍在模型投影前执行；较大的原始状态可能先被它安全拒绝。本轮没有放宽或绕过它，没有以短样本推断20轮一定可用，也没有完成新的20轮Replay或Live验收。

按上述增长并留约40,000用于下一请求保守准入的敏感性头寸，约支持 **14 / 11 / 9个有效回答**；这是规划量级，实际准入取决于当轮UTF-8/Schema/输出缓冲、合法结束条件与96,000字节原服务端planning guard，不能当作保证。

如产品明确要求稳定演示15–20答，200,000仍不足以保证。建议先做短程Live A/B验证，再单独评审：中性15轮可考虑约300,000会话预算，中性20轮约400,000–450,000；偏高20轮可能需550,000–600,000或改为更短训练，而不是无限加预算。Coach及整批授权仍需另计，不能用批次授权覆盖Session规则。本轮**未提高任何上限**。

## 6. 回归、失败定位及数据保护

隔离库：`interview_agent_v231_context_20261026_a`，全新创建，仅在该库执行原有Alembic upgrade至0026，无新migration。测试fixture及进程级DATABASE_URL均固定到此库。

本轮新增输入/服务契约用例覆盖：当前评分依据不丢、共享字典完整性、Memory计划不变、未解决gap及其解决、未知投影/Prompt不兼容拒绝、AI工具Schema/权限/失败恢复评分输入、字典顺序重建后Evaluation复用、同键幂等及非零预留覆盖实际wire输入并正常释放。

既有回归继续覆盖Kafka多场验证/冲突/撤销、专项训练及结果、合法来源/非法组合/未知引用、12/13/17条引用与一次受约束Repair、Main失败不推进/Evaluation复用、V1/V1.2系统设计、Skill、配额、共享资料、报告及隐私。

执行中发现并处理：

- 首个新增测试先红（Evaluation仍收到choices），实现投影后绿。
- 一处旧Replay测试假设每条candidate都展开label/source，按新公开共享字典协议解码；未删业务断言或模拟Controller。
- 输入顺序重建测试最初使用作答持久化前ASKED的DTO，无法复用ANSWERED阶段的Hash；改为真实相同阶段的DTO后验证通过，未放宽复用Hash。
- 初次扩大回归仅设置fixture数据库，旧报告删除路由读取默认连接而返回404，不是预期409；暂停批次核对正式库全表指纹未变，随后同时设置进程/fixture隔离连接重跑。没有为过测试修改路由或关闭所有权。

最终结果与范围：

- 后端非迁移回归分批及补充用例累计 **530 passed / 0 failed / 1 skipped**（重叠定向回归不重复累计）。跳过项为未提供可选 `V142_RECORDED_DATABASE_URL` 的旧V1.4.1真实Snapshot只读复现，不是本轮PostgreSQL缺失。
- 10个历史migration测试模块未选入本轮回归，不计入passed或pytest skipped；未改数据库结构，本次只在新库验证既有upgrade head。
- 真实PostgreSQL执行了本轮全部需要数据库的角色投影、状态、幂等/恢复等测试，不以Mock数据库替代。
- 相同7个历史Live输入：固定输出Replay结构及完整业务校验7/7通过。
- 前端 **43/43**，生产build通过；Python compile、`git diff --check`通过。本轮没有新跑Playwright，前端无本轮改动；不把此前浏览器结果冒充本轮重跑。
- 正式库仍0025/64表，前后指纹完全一致。成功短程及失败Live库的Session、Snapshot、Answer、Evaluation、Question、quota逐项一致。

## 7. 本轮实际文件与复算

业务新增/修改：

- `apps/api/src/interview_api/modules/interview/context_projection.py`：版本化、只面向模型的角色DTO。
- `agents.py`：调用新投影；完整payload继续用于Hash、引用校验、Repair及复用。
- `context.py`、`adaptive_prompts.py`：新Session冻结投影版本及2.3.1 Prompt，旧版本保留。
- `core_service.py`：仅新会话的调用预留适配真实角色DTO，不改Controller预算判定。

测试/分析/文档：

- `apps/api/tests/test_v231_context_projection.py`（新增）、`test_director_reference_repair.py`、`test_v211_director_contract.py`、`test_v21_adaptive.py`、`v21_support.py`。
- `apps/api/scripts/compare_v231_context.py`（新增），本文件及docs索引。

工作区中其余既有tracked/untracked修改均保留，不能把它们计成本轮新实现。没有新HTTP API、前端业务字段或数据库结构。

离线复算（输出必须新文件，不覆盖历史）：

```powershell
uv run --project apps/api --no-sync python apps/api/scripts/compare_v231_context.py --meter .runtime/acceptance/v23_short_live_20261026_a.json --checkpoint .runtime/acceptance/v23_short_final.json --output .runtime/acceptance/v231_context_compare_new.json
```

本轮保存对照 `.runtime/acceptance/v231_context_compare_20261026_b.json`，包含两种代理比值、固定/动态规模及全部预算假设；历史输入未改。正式库/历史保护结果保存在 `.runtime/acceptance/v231_*protected.json` 与 `v231_formal_final_20261026_a.json`。

回归必须在启动pytest前同时设置`DATABASE_URL`与`M1_TEST_DATABASE_URL`为专用隔离库，不能只设置一个；不要打印连接密码。全部命令依旧使用uv/pnpm。

## 8. 下一次Live建议：需另行授权

具备开始有界Live A/B的离线条件，但**尚未证明新投影的真实模型生成质量、分数一致性、实际Token或延迟**。

建议用保存的同输入固定场景，对原/新投影各调用一次，选择2个Main场景（RRF缺口追问、Agent非法工具请求处理）及1个Evaluation场景；6逻辑请求、最多8 HTTP、100,000 Token、10分钟，单批次、隔离Session/数据，仍保留会话本身约束。预留比预计实耗大，是为了每次请求前的保守预算准入及一次受控Repair，不是必须消耗完。

比较合法组合、具体缺口关联、评分依据及真实usage，而非要求文本逐字一致。原/新模型输出均不得固定为Replay或挑选成功结果。该A/B不是完整15–20轮或完整跨场次Live验收；本轮未执行，等待单独授权后才能启动。
