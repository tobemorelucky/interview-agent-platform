# V2.3 完整 DeepSeek Live 面试验收：失败停止记录

日期：2026-10-26。**完整 Live 验收未通过；未达到15轮或20轮，不生成虚构终局报告。**

本轮授权：最多50逻辑请求、60 HTTP、650,000 Token、40分钟，仅一场收费面试。实际在第三答后的下一题 Main 输出失败，按约定停止，没有重新开场或筛选成功样例。

## 1. 环境、数据与统计范围

- 新隔离数据库：`interview_agent_v21_v23_live_20261026_a`，正常升级至 Alembic `0026`；正式库保持 `0025`。
- 原 PostgreSQL / Redis 容器已健康运行，inspect确认原镜像及 `interview-agent-platform_pgdata` / redisdata 挂载；未重建容器或卷。
- DEMO 用户1：`v23.live@example.com`；人工构造的计算机硕士简历、RAG / Agent 项目和 AI Agent 岗位，均有 DEMO 标记。
- 初始 Profile 使用明确 Replay 通过现有 Evidence Selection 建立；没有伪装成真实简历、真实招聘网页或 Live Profile 验收。
- 无公司真实面经 Fixture：工作台明确提示专属资料不足，以现有专业 Skill / 岗位 Requirement / 简历 Fact 出题，不宣称公司真题。
- 本轮没有准备能力历史：开始时五个合法目标均 UNKNOWN，MemoryGuidedPlan 无历史复测任务。故本轮不能验证“长期记忆影响策略”的因果关系。
- **被测 Main、Evaluation 均为真实 `openai_compatible / deepseek-v4-pro-ga-260813`**，thinking=disabled，timeout180秒，max_output_tokens3000；Coach原配置为6000但本轮未调用。
- 未修改任何 route、thinking、`.env`、API key、fallback、Prompt、Schema 或 validator。预检确认全部相关路由为同一指定 DeepSeek，配置无 fallback。
- Token 仅统计平台发出的实际 DeepSeek HTTP usage；**Codex 生成模拟求职者答案的消耗不在其中**。没有额外调用模型充当考生。
- 没有价格或账单核验，不将 Token 等同于确定人民币费用。

## 2. 工作台与会话创建

真实浏览器登录 → `/training?job_id=1&recommended_skill_id=ai_agent_application`，确认岗位、Skill、FULL、标准风格。

发现产品不一致：`TrainingPage.vue` 轮数 input 的 HTML max=12，而 `CreateSessionRequest.question_count` 允许1–40。为遵守现有服务端契约而不修改页面，本轮用浏览器当前账号的 Authorization 通过**原有创建接口**提交20轮，再回到原面试页面启动和逐题作答；因此“20轮完全通过工作台按钮创建”未通过，不隐瞒该差异。

创建后 Session **1** 固定：FULL、AI/Agent Skill、岗位/JD/ProfileVersion1、合法 Blueprint、MemoryGuidedPlan、训练前状态和版本。

冻结预算：20题、每主问题最多2次追问/深挖、max_rounds40、7,200秒、**200,000会话模型Token**。本批650,000是授权上限，不覆盖会话自身的200,000限制。没有调高任何限制。本次实际失败并非预算耗尽。

## 3. 实际对话表（不是20轮成功表）

轮次是回答尝试，不是HTTP或Message数。第3答 Evaluation 模型成功且保留审计，但尚未正式提交 InterviewEvaluation。

| 回答轮次 | Question / Answer / 正式Evaluation ID | 实际问题摘要 | Codex模拟回答摘要 | 目标 / 已提交策略 | 评价与结果 | 归属本轮Token |
| --- | --- | --- | --- | --- | --- | ---: |
| 1 | 1 / 1 / 1 | RAG 从文档切分、召回排序到生成回答的完整链路 | 按段落/约500字切分与重叠，保存片段ID；BM25+向量、RRF、来源引用；承认没做系统评测 | rag_retrieval / NEXT开场，后续FOLLOW_UP | 五维4/4/3/5/4，PARTIALLY_DEMONSTRATED；正式提交下一题 | 31,994 |
| 2 | 2 / 2 / 2 | 向量召回的模型、索引和查询实现 | BGE中文embedding；片段向量和ID入库、同模型编码query、HNSW；未系统调参或设计换模型重建 | rag_retrieval / FOLLOW_UP，后续FOLLOW_UP | 五维4/4/3/5/4，PARTIALLY_DEMONSTRATED；正式提交下一题 | 24,681 |
| 3（失败尝试） | 3 / 3 / 未正式创建；Evaluation AgentRun7成功 | 余弦还是内积，为什么选择这种度量 | 余弦比较语义方向；L2归一化后内积与余弦排序等价；强调度量与模型训练及索引配置一致，承认没做独立对照实验 | rag_retrieval / 已有FOLLOW_UP；Main拟NEXT到agent但未提交 | Evaluation模型五维5/4/4/5/5；下一题Main超长引用列表，一次repair仍失败；Answer FAILED | 23,646 |
| 4–20 | 未发生 | 未生成 | 未回答 | 未决策 | 不填充虚构数据 | — |

归属口径：第1轮包括首题Main；每轮包括该答Evaluation和用于提交下一题的Main；第3轮包含失败Main和repair。完整题目、回答与已提交评价保存在隔离库及 ignored 最终状态文件，不是事后编写的固定批量答案。模拟考生每次先读取 `.q-body` 的实际题目，再形成回答；未展开当前题参考答案。

### Main 的真实目标—题源组合

| Main HTTP | 模型实际候选组合ID | 目标 / 来源 | 策略 | 最终裁决 |
| --- | --- | --- | --- | --- |
| 1 | `C_1bfb56688d2e4f02bb5f2421` | rag_retrieval / fact:1 | NEXT | 接受并生成Question1 |
| 3 | `C_730a9c038fcb0d2bbad5e47d` | rag_retrieval / skill:ai_agent_application@1.0.0 | FOLLOW_UP | 接受并生成Question2，父题依据保留 |
| 5 | `C_730a9c038fcb0d2bbad5e47d` | rag_retrieval / 同上 | FOLLOW_UP | 接受并生成Question3，追问消费2/2 |
| 7（8为repair） | `C_6fbaf249a8e8a389386dd23b` | agent / requirement:3 | NEXT | 候选在合法集合内，但Schema失败；**未到Controller提交** |

失败Main拟问：“换个方向，你在训练助手里用 Python 实现了 Agent 工具调用，并提到采用 Schema 校验和工具白名单。能具体说一下一次工具调用从模型给出参数到真正执行之间，你的程序做了哪些校验和权限控制吗？”

内容与已冻结Agent岗位要求和项目相符；不能因为内容合理就将未提交问题算成有效第4题。

## 4. 真实失败根因

`adaptive.py:AdaptiveSelection.supporting_refs`：最多12项。

Main在HTTP7给出17项：answer:3、evaluation_run:7、7条fact、3条question、4条requirement、1条skill。离线对原输出调用 `AdaptiveSelection.model_validate` 精确复现：

```text
supporting_refs
List should have at most 12 items after validation, not 17
type=too_long
```

所有17条都属于本轮 allowed_refs；组合ID也在实际 candidates 中。因此**本次不是 UNKNOWN_DIRECTOR_SOURCE，也不是伪造证据ID或错误组合映射**。JSON语法正常、HTTP200、finish_reason=stop，没有timeout或输出截断。

`infrastructure/models/structured.py:generate_structured` 触发原有一次repair。但repair提示要求保留全部事实字符串/引用，且 `_fact_inventory` 校验字符串与关联路径不变；它仅向模型传递 `validation_error="ValidationError"`，没有给出精确超长字段和数量。repair输出仍保留17项，最终 `structured_output_failed`。在该约束下，删除多余引用也会触发事实清单变化，不能把这当成可以无损修复的普通JSON语法问题。

本轮只定位，不扩大上限、不静默截掉引用、不改Prompt/repair或重跑。后续应单独评审“决策所需最小引用选择”及结构修复与事实保留边界，再进行离线回归；这不是本次已实现修复。

## 5. 实测Token统计

| 统计项 | 实测结果 / 边界 |
| --- | ---: |
| 第1–5轮 | 实际只发生3次回答尝试、2次正式有效Answer/Evaluation；消耗80,321，不能标成5轮成本 |
| 第6–10轮 | 未执行 |
| 第11–15轮 | 未执行，无15轮检查点 |
| 第16–20轮 | 未执行 |
| Main总Token | 44,586（4逻辑、5HTTP，含失败与repair） |
| Evaluation总Token | 35,735（3逻辑、3HTTP，其中第3次未正式提交业务Evaluation） |
| Coach总Token | 0（未调用，不代表正常Coach零成本） |
| 本批总Token | **80,321** |
| 输入 / 输出 | **77,226 / 3,095**，约96.15% / 3.85%，24.95:1 |
| reasoning tokens | 返回值均0，没有保存隐藏推理 |
| cache hit tokens | 返回合计10,240，包含在输入Token中，不再次扣减usage |
| 平均每次回答尝试 | 26,773.67（包含失败尝试与开场） |
| 每个已正式有效轮次分摊本批成本 | 40,160.5（含失败开销，不是正常稳态单轮成本） |
| 最小 / 最大回答尝试Token | 23,646 / 31,994；最小项恰为失败尝试，不应据此评价效率 |
| 逻辑 / HTTP | **7 / 8**；repair1次 |
| 模型累计HTTP等待 | **52.560秒**，没有并行 |
| 计量启动至失败停止 | 378.335秒，包含浏览器操作和Codex回答准备；不等同于模型延迟 |
| 停止时剩余批次预算 | 43逻辑、52HTTP、569,679Token，约2,021.665秒；失败后不继续使用 |
| Session实际模型Token / 原上限 | 80,321 / 200,000；预留0，未报告消耗0 |
| 按前15轮估算的完整15轮终局成本 | **无法提供：未执行到第15轮** |
| 实际20轮完整终局成本 | **未测得，不能以80,321代替** |

### 逐请求记录

| HTTP | Route | 输入 | 输出 | 合计 | 延迟ms | 结果 |
| ---: | --- | ---: | ---: | ---: | ---: | --- |
| 1 | Main开场 | 8,984 | 251 | 9,235 | 5,715 | 提交首题 |
| 2 | Evaluation1 | 11,227 | 521 | 11,748 | 8,778 | 验证成功并正式提交 |
| 3 | Main下一题 | 10,779 | 232 | 11,011 | 4,351 | 提交Question2 |
| 4 | Evaluation2 | 12,157 | 669 | 12,826 | 10,479 | 验证成功并正式提交 |
| 5 | Main下一题 | 11,627 | 228 | 11,855 | 4,314 | 提交Question3 |
| 6 | Evaluation3 | 10,623 | 538 | 11,161 | 7,709 | 已验证审计可复用，未生成正式Evaluation3 |
| 7 | Main拟切题 | 10,722 | 328 | 11,050 | 7,077 | 17引用，Schema不通过 |
| 8 | Main受控repair | 1,107 | 328 | 1,435 | 4,137 | 仍17引用，停止 |

Main平均HTTP耗时5.119秒（含repair），原始非repair四次平均5.364秒；Evaluation平均8.989秒。非repair Main没有默认high reasoning长等待问题。

### 上下文与预算判断

Main输入字符数依次23,993→27,846→29,649→27,176；Evaluation29,091→31,051→27,160，**这段短样本不是单调增长**。Main系统指令2,909字符、Schema紧凑序列化约1,517字符、Snapshot紧凑序列化约7,117字符保持稳定；新增历史、知识缺口、当前回答和Evaluation会增加输入，但合法候选在冷却后由29个缩为20个，抵消部分增长。请求中的history确实从0增长到1/2/3题，没有在这3题范围丢失历史。

输入占96%以上，说明此样本成本主要是输入上下文，不是thinking或冗长答案。不能依据3次尝试证明20轮不存在上下文截断或重复追问。

仅供容量规划：若不考虑增长/失败，以前两次正式完成的调用均值外推，开场9,235 + (N−1)×11,433（后续Main）+ N×12,287（Evaluation），得到15轮约**353,602**、20轮约**472,202**，**另加未测得的Coach成本和repair预留**。这不是“前15轮实测估算”，更不是完整15/20轮实测结果；样本小、不可承诺。

当前Session200,000上限不足以按该样本假设承载15–20轮。建议先修复结构化引用失败，再单独评审输入成本和产品轮数预算；本轮不调高配置。后续测试可以据此规划约35–50万基础Token量级及额外终局/失败预留，但不能在现有会话上限下保证完成。

## 6. 数据库、账本与恢复核验

- Session1：`IN_PROGRESS`、state_version6、active_question_id3。
- Question3条；Answer3条（2 COMPLETED、1 FAILED）；正式Evaluation2条，无重复有效记录，没有错误创建Question4。
- Evaluation3的AgentRun7成功且保存已验证输出；Main AgentRun8失败。未执行失败第三答重试，不宣称实际完成恢复；既有复用逻辑和该审计记录保留。
- `answer_processing=null`，model_call_reservations为空、reserved_model_tokens=0、unreported_model_tokens=0；无残留RUNNING处理lease。
- 8条ModelAdmission全部SETTLED，actual/charged合计均80,321，与真实HTTP usage逐批对齐，无未知usage或未结算预留。
- 成功第一答原question_id / content / idempotency_key通过真实SSE接口重放，返回原持久化事件，HTTP模型计数仍8，Question/Answer/Evaluation及state_version均未改变。
- Snapshot指纹只读重算通过。外部来源、题源和私人数据均属于本隔离库，本轮没有读取其他真实用户资料。
- 正式库前后均0025，64表行数及内容指纹全部相同。历史Session33未访问、未修改。
- 未完成面试，因此能力记忆行0、FinalInterviewReport0、MemoryProposal0；本场知识观察只记录已提交的两条PARTIALLY_DEMONSTRATED，没有提前创建长期能力结论或批准用户事实。
- 没有终局训练前后反馈，**没有创建下一场Snapshot来假装已读到完整训练结果**；该步骤未验收。

## 7. 产品质量的具体观察

已验证：首题贴合DEMO RAG项目，追问先向量召回实现再距离度量，没有改写同一个项目背景题；原有2次追问上限生效，失败Main已拟NEXT切Agent题。问题与实际回答关联明确。

待改进 / 未验证：

1. **P1完整流程阻塞**：Main的过量引用使合理切题仍无法提交。结构化输出repair与“引用全保留”规则不能解决该错误。
2. 工作台轮数HTML上限12与后端40不一致，20轮创建需走同一受鉴权API；没有修改页面规避。
3. 第2次Evaluation将“RRF合并前的归一化或分数处理细节”列为缺口，表述容易误导：RRF按排名融合，不要求两路原始分数归一化。总体评分合理并不意味着每条缺口都准确。
4. 三次Evaluation的reference_answer均为null，不能宣称此次Live已验证完整参考回答功能。
5. 第3次Evaluation已对核心度量问题给出高分并标记缺口解决，仍建议FOLLOW_UP；Main利用冷却改拟NEXT，说明独立评分建议与最终状态控制确实不同，但这次NEXT未提交。
6. Evaluation实际模型输入中没有memory_guided_plan / 历史能力规划层；未见以旧能力弱点扣分。由于本场无历史能力，不能据此证明所有跨场次场景无偏差。
7. 只运行3次回答尝试，不能评价15–20轮重复率、长期覆盖、难度稳定或完整Coach与Memory闭环。

## 8. 浏览器记录与工程文件

Playwright CLI：真实Vue5177、FastAPI8007、PostgreSQL、Redis和Live DeepSeek。没有route.fulfill或Replay替换Main/Evaluation。完成登录、岗位/Skill工作台、创建、首题、3次真实页面回答、SSE等待、失败后的刷新恢复，以及成功回答的幂等SSE重放。

- `.playwright-cli/`：实际操作快照（ignored）。
- `output/playwright/v23-live-evaluating.png`：评价等待。
- `output/playwright/v23-live-stopped.png`：失败恢复状态。
- 浏览器console核对0 errors / 0 warnings，不意味着业务通过；真实业务失败明确记录在页面及AgentRun中。

新增验收辅助文件，不改业务源码：

- `apps/api/scripts/accept_v23_live.py`：新隔离库的明确DEMO准备；同一批次计量、保留原Provider与governance、硬预算、失败停止。
- `apps/api/scripts/inspect_v23_live.py`：只读事务状态/计数/配额/快照核验。
- 本文及文档索引；58追加Live结果链接，原Replay通过事实保留。

本轮运行了脚本py_compile、真实Snapshot与账本核验、原输出Pydantic离线失败复现、浏览器与正式库指纹对比。**未重跑515后端/43前端，不把上轮结果当成本轮重跑结果。**检查脚本初次误用能力版本表user_id字段，已按memory_id外键修正，未修改数据库或业务数据；该只读脚本错误不计入模型请求。

持久化证据（全部ignored）：

- `.runtime/acceptance/v23_live_20261026_a.json`：8次HTTP的usage、时延、DEMO请求内容及业务结构化输出，不包含认证header或隐藏推理。
- `v23_live_stopped_20261026_a.json`、`v23_live_final_20261026_a.json`：失败和幂等后只读状态。
- `v23_live_formal_before_20261026_a.json`、`v23_live_formal_after_20261026_a.json`：正式库保护核验。

Live临时API已正常停止，防止后续误触发收费。没有启动第二场、执行Coach、迁移正式库、改动模型配置、commit/push或进入V2.4。隔离库和失败记录完整保留。

## 9. 最终结论

**不具备宣称V2.3完整Live通过的条件。**实际是2个正式有效回答轮次 + 第3答失败尝试；合法题源组合契约没有再次出现UNKNOWN_DIRECTOR_SOURCE，但暴露了新的结构化引用数量与repair冲突。

后续先授权离线最小修复并回归；需要新增Live批次时再单独授权。本次剩余预算不自动续用。
