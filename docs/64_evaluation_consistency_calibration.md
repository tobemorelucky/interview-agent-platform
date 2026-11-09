# Evaluation 评分一致性与校准草稿（离线）

日期：2026-10-27。范围：只读分析、12条草稿样本、公开Agent输入边界及隔离PostgreSQL Replay回归。真实模型请求 **0**。无生产Prompt/Schema/评分阈值/Controller/Memory规则/数据库迁移/模型配置修改。只新增本文、校准JSON和专项测试文件；不改docs索引，不commit/push。

## 1. 结论与证据等级

**证实：**
- A/B当前问题、原始回答、rubric、desired_difficulty、interviewer_evaluation整对象、输出Schema逐值相同。
- 新System是旧System完整前缀加Role Context说明，不是删除旧Adaptive评分说明。旧2.1.0、新2.3.1均没有逐等级数字锚点。
- V1的agents.PROMPTS[answer_evaluation]确实有Scale 1/2/3/4/5解释，但本次两版Adaptive冻结Prompt都独立定义，未继承这段；不能说是V2.3.1删除。
- 两版都发现相同核心技术缺口，没有resolved_gap_ids，都推荐FOLLOW_UP；差异是五维各+1及知识观察状态。
- 新版保留当前gap ID及正文，不存在把它删掉导致判定已解决的问题。
- 两版实际current_question都只有question_id和question，没有parent_question_id/question_kind/提示条件；旧history有题面、status、knowledge_id，没有父回答；旧observations有一段旧回答引用，不等于完整父问答。
- 新版当前目标的candidate_facts仍为1..7、对应真实Evidence不变，requirements由[1,2,3,5]变为[2]，public_questions均空。不是把所有候选人资料全删掉。
- 新增真实service测试证实：当前gap可达模型，但完整父回答追踪标记、显式提示/父关系均不可达。不能把fixture注解冒充生产字段。

**合理推测而非因果证明：**
旧NEEDS_CLARIFICATION标签和上一观察可能锚定负面评分；新版“只按当前题和rubric”附加说明可能改变背景权重；缺少逐等级锚点和模型随机性也可能贡献差异。N=1且Prompt与投影一起变化，不能拆出各因素作用。

**不能判定：**
优化是否普遍使分数上升、旧版或新版是否更准确、补回哪一个字段就能消除差异。没有多样本重复Live或人工审核标签支持这些结论。

## 2. 原始样本逐字段核对

本地ignored原始证据：
- .runtime/acceptance/v231_ab_live_20261027_b.json：两条完整request_messages、Schema、usage及business_output。
- .runtime/acceptance/v231_ab_final_20261027_b.json：原Question20、parent19及正式结果。
- docs/63_v231_live_context_ab_acceptance.md：已完成Live批次，不是本轮重跑。
本轮未修改上述文件。测试在本地证据存在时逐值核对；其他机器没有ignored证据时会明确skip此一项，不影响固定12条样本可用性。

当前题：
> DEMO Replay 第2题：RAG里为什么不能直接相加BM25和向量原始分数？检索不到依据时如何处理？

原始回答（E03逐字保留）：
> 我把 BM25 原始分数和向量余弦相似度直接相加，分数越大越相关，没有标注集。检索不到资料时让模型按经验补齐并生成引用。没有考虑量纲、排名融合和无证据拒答。

Rubric，两版完全一致：
1. 不同分数量纲不可直接比较
2. RRF按排名融合
3. 真实引用与无证据拒答

知识目标ai_agent_application:rag_retrieval；新版增加显式target：
topic=rag_retrieval，label=检索与RAG，purpose=验证机制、实际应用和边界条件，scope=THIS_INTERVIEW_NOT_STABLE_SKILL。
旧choices已有该目标，但混在下一题空间内。题源仍为原目标范围；本题持久化来源是skill:ai_agent_application@1.0.0，question_kind=FOLLOW_UP，parent_question_id=19。Question20未记录interviewer_guidance；没有证据表明本题提供过额外实质提示。但它确实是追问，不能当作独立首问。

当前gap：
- id：gap_a24cb817530491e5d2481596
- text：RRF排名融合与无依据拒答

专业锚点（两版逐值相同）：

| 维度 | 实际锚点 |
|---|---|
| technical_correctness | 机制和术语是否准确，是否区分模型与程序责任 |
| coverage | 是否覆盖当前问题要求的链路与关键步骤 |
| depth | 是否说明设计理由、失败边界和验证依据 |
| relevance | 是否针对实际问题和已建立的项目背景 |
| communication | 是否清晰表达流程、因果和取舍 |

### 实际输出，不作金标准

| 项 | 旧2.1.0 | 新2.3.1 |
|---|---|---|
| 五维（正确/覆盖/深度/相关/表达） | 1/1/1/3/3 | 2/2/2/4/4 |
| 知识观察 | NEEDS_CLARIFICATION | PARTIALLY_DEMONSTRATED |
| exact answer quotes | 2条 | 3条，多“没有考虑量纲、排名融合和无证据拒答” |
| missing_mechanisms | 2条，将量纲与RRF合并 | 3条，分别列出 |
| resolved_gap_ids | [] | [] |
| 建议 | FOLLOW_UP | FOLLOW_UP |
| 显式factual issues | 原始分数相加、无证据编引用 | 显式issues仅编引用；正文仍提及相加和量纲缺口 |

旧评价理由原文：
> 回答明确承认了错误做法：直接相加 BM25 原始分数和向量余弦相似度，并在无依据时让模型按经验补齐并生成引用。这直接暴露了量纲不可比、缺少排名融合和无证据拒答三个关键问题，但回答本身没有给出任何正确机制，因此技术正确性、覆盖面和深度都很低。回答针对当前问题，表述清晰，但仅停留在承认错误，未展示正确理解。

新评价理由原文：
> 回答直接承认了错误做法：直接相加 BM25 与向量原始分数、无标注集、检索不到时让模型按经验补齐并生成引用，并明确表示未考虑量纲、排名融合和无证据拒答。这体现了对自身方案缺陷的认知，但并未解释为什么不能直接相加（量纲不同、分布差异、不可比），也未说明 RRF 的排名融合机制，更没有给出检索不到依据时的正确处理方式（拒答或明确无依据）。因此技术正确性、覆盖度和深度均不足。回答与问题高度相关，表达清晰。

两版covered_points都把错误方案的陈述理解为“承认”；新版额外给“认识缺陷”更多部分展示权重。实际上“没考虑”不等于解释机制，连续原文引用也不等于引用内容在技术上正确。旧版对相关性/表达给3分，新版4分，两者理由都认为相关且清楚；缺少维度分档锚点使此差异难以裁定。

状态规则本身有重叠：NEEDS_CLARIFICATION可用于错误/歧义，PARTIALLY_DEMONSTRATED可用于有部分证据但缺机制，未明确“仅知道有问题”是否属于正向证据。当前服务端校验exact quote、目标、gap ID和未解决gap，**不做技术语义裁判**；因此新版状态能合法通过不代表技术判断可信。

最小评分修复建议（未实施）：先人工审阅E01/E02/E03对照，再决定是否为新版本补充逐维分档解释及正向状态边界，明确“正确陈述的机制”与“错误方案/仅提到术语”不同。不要整体复制V1通用量表或把所有维度一起降分；不把历史NEEDS_CLARIFICATION重新注入评分，不硬编码本次答案应得旧分数，也不引入第二模型或语义相似度裁判。是否需要改变生产规则应由后续校准证据决定。

## 3. 完整字段路径差异（实际A/B wire，非猜测）

删除一个父节点即包含其全部子字段，避免重复列出28个候选的每个数组下标。

顶层删除：
- evaluation（本样本原值null）
- history（2条题面/status/knowledge_id）
- preferred_source（原为空对象）
- requested_action（原NEXT）

adaptive删除：
- version、allowed_refs、candidates、choices、current_answer_ref、current_evaluation_ref、topic_coverage
- knowledge_state.version、knowledge_state.observations
- knowledge_state.items[active].knowledge_id/state/answer_quotes/answer_spans/missing_mechanisms/resolved_gap_ids/question_id/answer_id/evaluation_id
新增adaptive.target：knowledge_id/topic/label/purpose/scope。
不变：active_knowledge_id、knowledge_state.items[active].open_gaps逐值完整。

snapshot删除：
- adaptive_policy（version及两个prompt_fingerprints）
- budgets
- candidate_profile_version_id、profile_version_number、profile_producer
- company_id、job_id、job_description_version_id、jd_version_number、jd_fingerprint
- coverage_insufficient
新增source_semantics=CONTEXT_ONLY_SCORE_CURRENT_QUESTION_AND_RUBRIC。
改变requirements：4条→当前RAG对应1条；保留该条全部字段。
不变：company_name、job_title、candidate_facts、candidate_evidence、public_questions、interviewer_evaluation整对象。
序列化键顺序变化不等于字段语义改变。

新System追加原文：
> Role context V1 contains only this goal's open_gaps and scoped source context.
> Sources explain the current question, not extra requirements to penalize an answer for.
> Grade ONLY the current question and rubric; no next-question candidates or historical
> ability assessments are provided. Use adaptive.target to understand professional scope.

可能影响评分的信息：旧负面观察、完整父题面及历史引用、无关JD要求是否被误当额外要求、新增来源范围说明。没有证据证明评分用的当前rubric或五维anchors被删掉。

## 4. 12条校准样本及审核要求

文件：apps/api/tests/fixtures/evaluation_calibration_v1.json。
所有review_status=DRAFT_UNREVIEWED、reviewers=[]、ground_truth=false。E03来自真实保存的问答与rubric，其余为明确标记的技术设计样本，不冒充真实用户记录。
context.parent_question/parent_answer/substantive_hint等是**校准注解**，不是已支持的生产DTO；测试不把它们塞入模型请求伪装实现。
分数范围按正确/覆盖/深度/相关/表达顺序，区间是待复核建议，不是生产阈值或容忍误差。区间重叠不能证明两个输出等价。

| ID | 场景 | 五维参考范围 | 可接受状态（草稿） | 独立观察资格 |
|---|---|---|---|---|
| E01 | 明显正确/RAG机制 | 4–5 / 4–5 / 4–5 / 4–5 / 4–5 | DEMONSTRATED_IN_SESSION | 可计一次，仍需既有聚合 |
| E02 | 仅识别问题/不知道机制 | 1–2 / 1–2 / 1–2 / 4–5 / 3–5 | PARTIALLY_DEMONSTRATED / NEEDS_CLARIFICATION | 不可 |
| E03 | 真实AB/错误方案 | 1–2 / 1–2 / 1–2 / 3–5 / 3–5 | NEEDS_CLARIFICATION | 不可 |
| E04 | 答非所问 | 1–2 / 1–1 / 1–1 / 1–2 / 2–4 | INSUFFICIENT_EVIDENCE | 不可 |
| E05 | 自相矛盾 | 1–2 / 1–2 / 1–2 / 3–5 / 2–3 | NEEDS_CLARIFICATION | 不可 |
| E06 | 追问后改善 | 4–5 / 4–5 / 3–5 / 4–5 / 3–5 | DEMONSTRATED_IN_SESSION | 不可 |
| E07 | 实质提示后正确 | 4–5 / 4–5 / 3–5 / 4–5 / 3–5 | DEMONSTRATED_IN_SESSION | 不可 |
| E08 | 跨知识点混答 | 2–3 / 1–2 / 1–2 / 2–3 / 2–4 | PARTIALLY_DEMONSTRATED | 不可 |
| E09 | 信息不足/需要澄清 | 1–2 / 1–2 / 1–2 / 3–4 / 2–4 | INSUFFICIENT_EVIDENCE / NEEDS_CLARIFICATION | 不可 |
| E10 | 历史冲突/当前独立正确 | 4–5 / 4–5 / 4–5 / 4–5 / 3–5 | DEMONSTRATED_IN_SESSION | 可计一次，仍需既有聚合 |
| E11 | 独立正确/ToolCalling | 4–5 / 4–5 / 4–5 / 4–5 / 4–5 | DEMONSTRATED_IN_SESSION | 可计一次，仍需既有聚合 |
| E12 | 部分正确/Kafka ACK缺口 | 2–3 / 2–3 / 1–3 / 4–5 / 3–5 | PARTIALLY_DEMONSTRATED / NEEDS_CLARIFICATION | 不可 |

E03新增真实父问题19和已保留gap；原完整父回答的引用与准备脚本一致。E06/E07相同技术机制，对照追问/实质提示条件；E10历史冲突只作隔离压力条件，当前正确不能被历史扣分，也不能一次回答自动抹平冲突。
E01/E02是核心区分对照：前者解释不可比性、RRF公式及拒答，后者只知道“不应如此”但不会解释或解决。E03仍采用错误方案，不等同E02的正确约束意识。

### E01 明显正确/RAG机制

- technical_correctness：机制准确且未声称唯一融合方案。
- coverage：覆盖三项本题要求。
- depth：给出公式、边界及验证方法但没有展开实验。
- relevance：直接回答本题。
- communication：流程清晰、表达紧凑。

正确展示：解释了不可比性及支配风险；给出RRF公式、按ID合并及缺席处理；明确证据不足拒答。
仅提及：无。
必须识别：无预设关键错误，不可凭空找错。

### E02 仅识别问题/不知道机制

- technical_correctness：约束意识不等同正确解释，不能给高技术分。
- coverage：只覆盖结论层未覆盖机制。
- depth：没有因果或实现细节。
- relevance：直接承认本题知识边界，仍然相关。
- communication：表达清楚不应随技术分一起低。

正确展示：无可验证正确机制。
仅提及：知道不能随便相加与编造引用，但无解释或实现；提到RRF但明确不会。
必须识别：无预设关键错误，不可凭空找错。

### E03 真实AB/错误方案

- technical_correctness：核心方案有明确技术错误，不因识别缺口而视为解释正确。
- coverage：三项要求均无正确方案。
- depth：没有正确机制，仅陈述错误方案和遗漏。
- relevance：确实围绕当前问题，不等同技术正确。
- communication：陈述清晰，不能因技术错误自动降表达分。

正确展示：无可验证正确机制。
仅提及：量纲、排名融合、无证据拒答仅作为未考虑事项出现。
必须识别：未校准直接相加异构原始分数；无依据补写并生成引用。

### E04 答非所问

- technical_correctness：没有可评价的可靠性机制，不能推断本人不会。
- coverage：本题关键项均未覆盖。
- depth：无本题实现或推理。
- relevance：转向背景与动机。
- communication：语句完整但未组织技术回答。

正确展示：无可验证正确机制。
仅提及：Java项目未说明消费机制。
必须识别：无预设关键错误，不可凭空找错。

### E05 自相矛盾

- technical_correctness：原则与实际策略冲突，不能只择正确半句。
- coverage：提到ACK但幂等及恢复不成立。
- depth：不能解释失败窗口。
- relevance：围绕题目。
- communication：矛盾影响因果清晰度。

正确展示：提到提交后ACK这一原则。
仅提及：重投递只被声称，没有可行恢复链路。
必须识别：同一回答同时主张提交前和提交后ACK；先ACK后失败可能丢失业务处理；没有幂等措施。

### E06 追问后改善

- technical_correctness：当前答案正确，不因追问就扣分。
- coverage：覆盖所有本题要求。
- depth：解释两个失败窗口，未涉及外部副作用边界。
- relevance：针对上一缺口。
- communication：顺序明确。

正确展示：原子去重及业务提交；提交后ACK；明确两个崩溃窗口。
仅提及：无。
必须识别：无预设关键错误，不可凭空找错。

### E07 实质提示后正确

- technical_correctness：可按当前正确机制给分，但不能当作独立掌握。
- coverage：当前覆盖充分。
- depth：说明失败窗口，机制由提示提供并非独立推导。
- relevance：针对题目。
- communication：表达有顺序。

正确展示：当前答案完整解释提交边界与去重。
仅提及：无。
必须识别：无预设关键错误，不可凭空找错。

### E08 跨知识点混答

- technical_correctness：参数校验正确但权限和执行状态缺失。
- coverage：仅覆盖一部分当前要求。
- depth：未解释越权和失败路径。
- relevance：大量跨知识点名词分散焦点。
- communication：能理解但结构松散。

正确展示：非法参数在执行前拒绝。
仅提及：RRF、Redis、Kafka并未证明权限与恢复能力。
必须识别：无预设关键错误，不可凭空找错。

### E09 信息不足/需要澄清

- technical_correctness：只有目标没有可验证技术依据。
- coverage：未覆盖可操作机制。
- depth：无细节。
- relevance：回答指向可靠性目标。
- communication：语言可理解但笼统。

正确展示：无可验证正确机制。
仅提及：不丢不重只是目标。
必须识别：无预设关键错误，不可凭空找错。

### E10 历史冲突/当前独立正确

- technical_correctness：答案技术正确，历史冲突不能减本题分。
- coverage：覆盖全部要求及外部事务边界。
- depth：解释真实失效窗口和边界。
- relevance：直接回应。
- communication：逻辑清楚。

正确展示：原子幂等、位点边界、故障恢复；区分Kafka事务与外部数据库边界。
仅提及：无。
必须识别：无预设关键错误，不可凭空找错。

### E11 独立正确/ToolCalling

- technical_correctness：没有让模型绕过权限或编造执行。
- coverage：覆盖三项要求。
- depth：区分错误类型、幂等和提交边界。
- relevance：针对工具执行链路。
- communication：结构清楚。

正确展示：白名单、Schema、资源权限；有界重试与幂等；持久状态与真实结果的程序边界。
仅提及：无。
必须识别：无预设关键错误，不可凭空找错。

### E12 部分正确/Kafka ACK缺口

- technical_correctness：基本顺序正确但缺幂等和原子性。
- coverage：未覆盖重复投递处理。
- depth：承认窗口但没有解决方案。
- relevance：紧扣问题。
- communication：准确表达已知边界。

正确展示：处理成功后ACK及失败重试。
仅提及：保证不丢的结论尚缺异常边界证明。
必须识别：成功后ACK不足以保证业务副作用不重复。


### 人工复核流程

1. 技术审阅者先不看旧/新版评分，独立标出正确机制、错误、术语提及、缺失点及提示条件。
2. 逐维审查范围，尤其不能让技术错误同步污染表达/相关性，也不能让术语识别同步抬高技术/深度。
3. 审查状态：准确定位引用不代表正确掌握；明确部分展示与错误待澄清的分界。
4. 审核独立性须与分数分开：一次独立正确也不是VERIFIED；提示后当前答对不计独立掌握。
5. 记录审阅者、分歧与最终版本；当前JSON不得未经人工确认改称可信Gold。评分锚点如需正式改变，另行版本化、回归和授权。

## 5. 父问答/提示契约缺口与最小建议（未实施）

实际路径：
core_service读取active及完整questions
→ adaptive.planning_payload只构造current_question(question_id, question)、rubric=standard_answer.splitlines()
→ skill_engine.agent_payload删除style与interviewer_guidance
→ context_projection.model_payload新Eval只保留当前题、原答、rubric、难度、当前目标/来源/open_gaps
→ AnswerEvaluationAgent.adaptive().call
→ AdaptiveEvaluation/resolve_observations校验
→ Controller提交正式观察
→ capability_service按持久化question_kind及interviewer_guidance独立判assisted。

- 父关系和提示在服务器并非完全不存在，Question表有parent_question_id、question_kind，candidate_refs_json可有interviewer_guidance。
- 旧history仅有题目，不含关系/父回答；新Eval删history。两版都没有可靠完整提示条件。
- skill_engine注释说明interviewer_guidance是style生成的UI帮助，不作评分证据。**不建议原样恢复它**，它混合风格与帮助；评价独立性与记忆assisted是两个职责。
- 当前场景题面自足，所以不能证明缺失父回答导致此次+1；遇到“那你怎么处理这个窗口？”等指代性追问，缺失语境会更危险。
- E07只能证明有一份待评价的提示注解、以及生产当前没有此通道，不能声称生产已完成提示感知校准。

未来最小方案：新增版本化、本题限定的scoring_context，仅携带确定性父题/必要父答节选及可追溯位置、当前焦点gap、已实际展示的实质提示及类型/来源。与style、历史能力状态和下一题候选分离；禁止把历史低分放入。上下文缺失标UNKNOWN而不是assisted=false。不由LLM猜提示状态。**本轮未新增该字段、未改Prompt/Schema/Controller，也未改变V2.2聚合。**

## 6. 离线测试及边界

TDD：先添加草稿结构测试，缺少fixture时实际失败（FileNotFoundError）；补充fixture后1 passed；再逐步加入公开Agent与真实service特征测试。生产代码未改，红绿只用于样本交付完整性，不能冒充修复了评分语义。

新增测试：
- 12条完整草稿结构、逐维理由、审阅状态、对照区分。
- 两版公开AnswerEvaluationAgent.call各处理12条固定样本：原答、rubric、anchors不变；当前gap可达；必要当前来源保留；Memory推断不可达；新版无下一题候选。旧版是公开Agent输入反事实，不改存储旧Session来构造版本。
- 实际保存Live A/B逐值核对，原始Schema和anchors相同，system前缀不变、分数/状态确有差异。
- 正常service两次答题：父回答追踪标记不在新Eval wire；gap在；同键重试不再次调用模型；Session完成且Snapshot不变；当前较高Replay分数不使追问观察变成独立验证，真实CapabilityMemoryService返回PRACTICING、independent_sessions=0。

隔离库：interview_agent_v231_context_20261026_a，启动前只读确认Alembic0026；复用原PostgreSQL16容器，不建容器、不迁移、不清理库。DATABASE_URL和M1_TEST_DATABASE_URL仅在测试子进程均指向此库，密码不输出。测试追加专用seed，不触碰Live验收库或正式数据。
初次只读连接错用interview账号，得到role不存在；随后使用容器现有POSTGRES_USER成功列出库。此失败未改账号/权限。一次权限审核超时未执行测试，重试一次成功。普通权限uv缓存访问受限，最终数据库测试经审批运行。

最终相关回归：**87 passed / 0 failed / 0 skipped，31.74秒**。其中新增本文件配套测试6项（两版输入边界参数化计2项），既有相关回归81项；包括最终明确核对AI样本使用AI Skill、Java样本使用Java Skill的评分锚点。此前86项及34.63秒的87项是中间结果，不累加计数。
命令范围：最初uv run --project apps/api --no-sync python，最终直接用既有apps/api/.venv/Scripts/python.exe避免uv缓存权限问题（进程内pytest，-p no:cacheprovider），选中calibration、v231_context_projection、v211_director_contract、v21_adaptive、v22_capability_rules/integration、director_reference_repair、v23_training。最终补跑两次权限审查超时均未执行；直接虚拟环境无需提权即可完成同一测试集，不修改环境配置。
未运行前端/浏览器/收费模型或整个仓库全量，不沿用旧530/43为本轮结果。

## 7. 下一次Live设计与预算（未授权，未执行）

### 实验有效性前提

先人工复核草稿，不使用旧/新版输出作标签。固定同一业务评分输入，旧2.1.0/新2.3.1各用自己的真实冻结契约，不改历史Session。
四个优先样本：E03关键错误、E12部分正确、E07提示后改善、E04答非所问。
**E07生产提示通道缺失是已知限制**：若下轮仍不补契约，只能测试“提示后这段当前回答的独立评分”，不能声称测到Evaluator识别了实际提示。要比较提示感知，先单独批准最小上下文契约修复；不能把fixture.context直接塞进线上DTO。
A/B仍同时改变投影与附加说明，只能评估整个冻结版本效果，不能单独归因裁剪。
按预登记顺序AB/BA交错，不失败后刷成功。第二次重复同输入至少覆盖四类，保存每次输出而非挑选结果。

### 输入估算方法与误差

不是tokenizer精确计数：以docs63实际Evaluation usage旧10851/新3045作模板锚点，将每个样本的当前题/原答/rubric替换进两版完整request_messages，保留system及输出Schema，分别用UTF-8字节比和CJK/英文标识符/标点词法单元比缩放，取均值。
这只是同体量目标来源模板代理，不是已构造新Session的真实usage。Java/AI各目标来源、必要父语境、序列化、模型分词及服务商隐藏开销会改变结果；正式执行前需以实际冻结wire再做准入预留，不可把未知当0。本轮未安装tokenizer。

| 样本 | 旧输入估算 | 新输入估算 |
|---|---:|---:|
| E01 | 10910 | 3093 |
| E02 | 10854 | 3047 |
| E03（原始A/B实际usage锚点） | 10851 | 3045 |
| E04 | 10831 | 3029 |
| E05 | 10848 | 3042 |
| E06 | 10892 | 3078 |
| E07 | 10878 | 3067 |
| E08 | 10870 | 3060 |
| E09 | 10823 | 3022 |
| E10 | 10906 | 3090 |
| E11 | 10952 | 3128 |
| E12 | 10852 | 3046 |

E03最终保留原始题与rubric时，其实际历史输入锚点就是10851/3045；其余样本均是代理估算，绝不称为实测。
四组配对约55,599 input；全12样本配对约167,214 input。旧80,000总额度对带重复、输出和repair的完整校准不足。

建议分开授权，绝不自动串行收费：
1. **小规模配对试验**：4样本×旧新各1=8逻辑，最多10HTTP，140,000 Token，10分钟。input约55.6k，按输入上浮50%约83.4k，正常输出硬上限8×3000=24k；2次repair另约30k（需以实际repair wire核对）→约137.4k。若实际准入预留不够则在请求前停止，不削评分依据适配预算。
2. **可观察波动的重复试验**：4样本×旧新×2=16逻辑，最多18HTTP，280,000 Token，15分钟。input约111.2k、上浮50%至166.8k，正常output最大48k，2repair按最大旧输入及修复材料保守预留约60k→274.8k。正式修复wire预留若更大则停止重新报预算；不购买/充值。
3. 全12样本首次配对已需24逻辑，超出“少量”目标；不建议本轮之后直接全跑。

观察指标：逐维绝对分差/有方向分差、参考范围命中（仅草稿诊断，未审核不算准确率）、状态一致/分歧表、必须识别错误的漏检/误报、仅提术语误当机制、有效quote与gap、实际input/output/repair usage及延迟。
统计同版本重复波动，再比较跨版本差异；样本与重复数仍很小，不声称统计显著性或质量无下降。技术错误样本与表达/相关分数应独立检查。
每请求保留预算、实际计量、结束原因与失败；最多一次受控repair，所有HTTP计账，非法引用或状态问题立即停止。

## 8. 交付边界

本轮增加的是**可重复的离线评分输入校准材料、真实A/B可复核对照和语境缺口证据**，不是新评分算法，也不是模型评分准确性认证。
必要的下一步是人工技术复核草稿、决定是否补齐本题父语境契约，再单独授权有限Live校准。不应立即启动长面试或把新评分强制调回旧分。
