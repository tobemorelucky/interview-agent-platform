# 50 个项目深挖问答

使用顺序：先背每题“标准回答”，被追问再展开机制；不要把下面全部细节一次说完。文末 [D] 为验收/设计记录，[C] 为当前源码。历史方案被后续修复覆盖时，以当前源码和较新的记录为准。

## 01. 项目解决什么问题，为什么不是普通聊天机器人？

**标准回答：** 它把秋招准备中的简历、岗位、面试与复盘串起来。与普通聊天不同，画像事实有来源，面试有持久化状态，复盘和记忆也有明确的审批边界。

**详细解释：** 对话文本不是唯一真相源。用户事实、岗位要求、回答、评价分别保存；每次面试固定输入，刷新或重试不能重新“猜”当前状态。

**可能继续追问：** 哪个环节最难？为什么要保存快照？

**容易踩坑的错误回答：** “就是六个模型轮流聊天。”这忽略了确定性控制与事实来源。

依据：[D3]、[D4]、[D8]。

## 02. 为什么是六个 Agent，Multi-Agent 到底体现在哪里？

**标准回答：** 六个角色各有业务目标和输入输出，研究处理公共资料，画像建立候选事实，匹配选岗，Main 提问，Evaluation 评价，Coach 复盘。拆分是为了责任和验证边界，不是为了数量。

**详细解释：** 一个角色可以有多个模型步骤，例如研究的 extraction/quality，因此不是“一角色恰好一请求”。它们共享模型基础设施，但不会任意互相调用或接管状态机。

**可能继续追问：** 为什么 Main 和 Evaluation 不能合并？是否并行协商？

**容易踩坑的错误回答：** “Agent 越多效果越好”“有自主 Supervisor 全程调度。”没有这些依据。

依据：[D1]、[D3]、[C1]。

## 03. Agent 和程序的边界怎么划？

**标准回答：** 模型负责生成、评价和排序建议；程序负责资格、证据引用、状态、预算、计数、幂等和版本。模型输出必须经过 resolver 和业务 validator 才能进入持久化。

**详细解释：** Evaluation 的推荐动作不是最终动作，Controller 还要检查预算与当前状态；Coach 不能改统计；Matching 不能翻转硬资格。这些限制不只写在 Prompt 中。

**可能继续追问：** 模型建议 COMPLETE 但预算没结束怎么办？

**容易踩坑的错误回答：** “Prompt 告诉它不要乱来就够了。”Prompt 不是事务或权限机制。

依据：[C4]、[C6]、[C7]。

## 04. 为什么不用 LangGraph 作为核心控制层？

**标准回答：** 这条主链的难点是持久化状态和业务不变量，我选择让业务 Controller 显式管理。LangGraph 能组织调用，但不能替代数据库事务、版本校验和 Evidence 约束。

**详细解释：** 不是认为图编排无用，项目保留既有面经 pipeline。新主链没有迁入 JobPilot 的 Supervisor/Dossier 系统，避免出现图状态和业务数据库两套控制真相源。

**可能继续追问：** 流程变复杂后是否考虑图？

**容易踩坑的错误回答：** “整个仓库完全没有 LangGraph”“LangGraph 不支持持久化。”都不准确。

依据：[D1]、[D3]、[D5]、[C4]。

## 05. 为什么选择模块化单体和独立 Worker？

**标准回答：** 个人项目先保证业务边界和本地可运行，比过早拆微服务更重要。API 管在线业务，Worker 管上传后解析和较重的离线处理，共享领域实现但分开执行。

**详细解释：** 单体不等于没有模块边界，也不代表模型调用都由 Worker 执行。当前面试评价在 API 的异步流程中，需要处理 SSE 与请求取消。

**可能继续追问：** 将来哪个模块最适合独立？

**容易踩坑的错误回答：** “已经是分布式微服务平台”“所有 Agent 都异步入队。”不存在这些保证。

依据：[D0]、[C9]、[C4]。

## 06. ModelRouter 解决什么问题？

**标准回答：** Agent 使用业务 route，而不是硬编码模型。Router 将 route 映射到 Provider、模型、timeout、输出预算和 reasoning 策略，实际选择进入审计。

**详细解释：** 不同抽取、评价、生成任务可以配置不同策略，但复用统一接口和并发 guard。当前 guard 是进程/事件循环内的 Semaphore，不是跨 Worker 的分布式限流。

**可能继续追问：** API 与 Worker 能共享限流吗？怎么避免暴露 key？

**容易踩坑的错误回答：** “已经实现全局分布式配额控制。”源码只有本地 guard。

依据：[C1]。

## 07. 现在用什么模型？为什么记录还写 DeepSeek？

**标准回答：** 当前正式 LIVE route 统一配置 Doubao 2.1 Turbo。DeepSeek 是前一阶段真实验收和性能诊断用的模型，历史审计保留，不会因为切换默认模型而改写。

**详细解释：** 模型名、Prompt 版本、调用结果应按每次实际使用记录。不同模型、输入和场次的延迟不能直接拿来算提升百分比。

**可能继续追问：** Profile 为什么关闭 thinking？当前所有角色是否都低延迟配置？

**容易踩坑的错误回答：** “现在默认还是 DeepSeek”“换模型证明算法提速数倍。”与记录不符。

依据：[D6]、[D9]、[D10]、[C1]。

## 08. Structured Output 成功是否等于业务成功？

**标准回答：** 不等于。JSON 和 Pydantic 只能验证格式；还要检查引用存在、归属正确、事实支持、状态与预算。Profile 曾经格式全部通过，但业务 grounding 失败。

**详细解释：** 当前 Compatible Provider 使用 JSON object 响应形式，并将 Schema 放入请求，返回后本地解析校验；不能说 Provider 已替我们执行了全部严格业务 Schema。

**可能继续追问：** Step 成功而 Agent 失败可能吗？

**容易踩坑的错误回答：** “只要 JSON 合法就可入库”“API 的 JSON 模式消除了幻觉。”

依据：[C2]、[D6]、[D7]。

## 09. 为什么最多只修复一次结构化输出？

**标准回答：** repair 用于格式兼容，不用于无限刷业务成功。最多一次 constrained repair，并检查事实文本与关联不能被偷偷修改，之后仍走同样的业务校验。

**详细解释：** 不限制尝试会放大费用和延迟，还可能把格式错误“修”成新的事实。修复不是允许补未知 Evidence、改分数或换模型逃过校验。

**可能继续追问：** 修复时如何检查变化？业务校验失败也 repair 吗？

**容易踩坑的错误回答：** “不通过就反复提示模型直到过。”没有有界性与可靠性。

依据：[C2]。

## 10. fallback 在什么情况下允许？

**标准回答：** 仅在配置了备用模型、且属于明确的模型或 Provider 可用性错误时允许。Schema、Evidence 或业务状态失败不能靠自动换模型刷成功，实际模型和原因都要审计。

**详细解释：** 具体可用性由 Provider 的错误分类决定，不是所有 HTTP 错误或 timeout 都算。当前路径不会因为普通 model_timeout 自动绕过约束。

**可能继续追问：** 429、模型不存在和业务 422 怎么区分？

**容易踩坑的错误回答：** “任何失败都自动 fallback，用户无感。”会掩盖 contract 问题。

依据：[C1]、[C2]。

## 11. Evidence grounding 到底能保证什么？

**标准回答：** 它保证陈述有可检查的来源与引用关系，并结合确定性规则拒绝一部分无依据输出；不是证明网页内容绝对真实，也不是完整语义理解。

**详细解释：** Resume 引用原始 excerpt，公共 Claim 引用 Snapshot 中的证据；社区面经还需要审核。字面支持、身份、归属和出处是不同校验层，不能混成“可信度百分比”。

**可能继续追问：** 转载、多来源与事实真实性怎么区分？

**容易踩坑的错误回答：** “有两个网页引用就证明事实真实。”转载可能只是同一内容。

依据：[D1]、[D7]、[D11]。

## 12. 为什么把 Profile 改成 Evidence Selection？

**标准回答：** 自由生成 statement 很容易合理改写，却无法满足严格字面支持。最终让模型选证据、分类并提标签，程序从真实 excerpt 建立事实，解决生成文本与事实 contract 的冲突。

**详细解释：** 模型侧不再输出长 statement；服务端选确定性的代表证据作为兼容文本，全部证据关系仍保存。标签也必须被所引用证据规范化支持，未知引用直接拒绝。

**可能继续追问：** 这样会损失总结能力吗？

**容易踩坑的错误回答：** “降低相似度阈值就好了”“再加一个 LLM 判真假。”都不是最终方案。

依据：[D7]、[C3]。

## 13. Evidence alias 为什么安全？

**标准回答：** E01 等只是本次调用的短引用。程序按稳定顺序建立一对一映射，输出先精确还原真实 key，再跑原来的归属与支持校验；数据库中的真实证据没有变。

**详细解释：** alias 减少长 hash 与 JSON 开销，不承担持久化身份。不同调用的 E01 不可互换；未知 alias 拒绝，不能模糊找一个相近证据。

**可能继续追问：** 排序变化会影响历史吗？

**容易踩坑的错误回答：** “直接把数据库 Evidence ID 改成 E01。”会破坏跨调用 provenance。

依据：[D7]、[C3]。

## 14. PDF 为什么会把项目 Evidence 分错？

**标准回答：** PDF 提取顺序不一定等于版面阅读顺序，原来逐行继承标题 section，导致项目描述流入 general。修复的是确定性 grouped context，而不是重写已保存的原始证据。

**详细解释：** 使用真实 Evidence 和 Report 的精确分组提示辅助，不能改 excerpt 或制造事实；不可靠分类仍是 general。真实样本由 projects 只有标题，变为 23 条项目上下文证据。

**可能继续追问：** 是否支持扫描 PDF、OCR 和复杂表格？

**容易踩坑的错误回答：** “已经完整恢复 PDF 版面”“可以 OCR 所有简历。”Parser 没有这项能力。

依据：[D7]、[C3]、[C9]。

## 15. summary、strengths 为什么不进入稳定事实？

**标准回答：** 它们是归纳分析，不应和项目、技能、教育事实共用同一稳定层。当前 extraction 可以 summary 为 null、strengths 为空，Matching 和面试仍读取已验证事实。

**详细解释：** 人性化摘要可以以后基于验证事实展示，但不能反过来成为资格证明。既有历史显示内容可兼容读取，不能借修正入口新增 derived stable facts。

**可能继续追问：** 页面没有摘要会不会体验差？

**容易踩坑的错误回答：** “摘要里说精通 Java，就可以判技能满足。”这是把模型分析变成事实。

依据：[D7]、[C3]。

## 16. 为什么技能不用 0～100 分？

**标准回答：** 简历不能提供那么精确的能力测量。我使用提及、具使用证据、较强证据等有限状态，模型只提建议，程序按真实使用证据计算上限，并只能向下收敛。

**详细解释：** 只有提及最多 MENTIONED，一条有效具体使用证据最多 DEMONSTRATED，两条不同使用证据最多 STRONG_EVIDENCE。它描述证据状态，不证明真实能力强弱。

**可能继续追问：** 用户修正技能能升到最强吗？

**容易踩坑的错误回答：** “有两条描述就一定精通”“程序自动把状态补高。”都越界。

依据：[D7]、[C3]。

## 17. Profile 如何支持用户修正而不丢历史？

**标准回答：** 修正复制当前完整快照，在允许的事实字段上改变内容，记录用户确认与旧新值，再创建 N+1。规范化事实和证据关系成功后，最后切换当前指针。

**详细解释：** 旧 Version 与子记录保持不可变。用户确认是自述 provenance，不能伪装成简历原文；技能修正不会让用户自行认证高能力状态。

**可能继续追问：** 两个人同时修改同一版本怎么办？

**容易踩坑的错误回答：** “直接 UPDATE 原来的 Profile JSON。”会破坏历史面试与推荐解释。

依据：[C3]、[D2]。

## 18. Profile generation 幂等如何定义？

**标准回答：** 结合 Resume、原文/Report 内容和 Prompt 等输入形成 generation key，同一次投递或重试复用，不生成等价 Version。明确的用户重新生成使用独立 token，与自动重试区别开。

**详细解释：** 幂等不是锁住一切不许重做，而是定义哪些尝试属于同一业务意图。发布时再核验 base version，不能把晚返回的结果覆盖用户刚修正的画像。

**可能继续追问：** Prompt 更新与重新上传是否应生成新版本？

**容易踩坑的错误回答：** “Celery 的 task ID 天然等于业务幂等键。”同一意图可能有不同投递。

依据：[C3]、[C9]。

## 19. 既有 ResumeReport 是否还存在？

**标准回答：** 存在，我没有重写上传、MinIO、解析与 Report 链路。Profile 在成功 Report 后独立处理，失败不抹掉 Report；新的 context 不再无条件传完整 legacy Report。

**详细解释：** 原来 Report 总结加 Evidence 再生成 Profile 存在重复语义加工。目前只保留必要分组提示，事实依据仍是 raw Evidence；没有彻底消除旧 Report 的模型调用。

**可能继续追问：** 为什么不直接删掉旧报告？

**容易踩坑的错误回答：** “简历现在完全只有一次 LLM 调用。”旧流程仍在。

依据：[D7]、[C9]。

## 20. 删除简历为什么可能返回 Conflict？

**标准回答：** 已被 Evidence 或 ProfileVersion 引用的简历是历史 provenance，不能删除导致事实失去来源。Service 先检查数据库引用，再动对象存储，数据库外键也保护历史。

**详细解释：** 不能先删 MinIO 再发现数据库拒绝。删除保护与所有权校验是两层；这不是宣称所有跨存储操作都具有分布式事务。

**可能继续追问：** 用户要求彻底注销怎么办？

**容易踩坑的错误回答：** “CASCADE 删除整套历史最方便。”与当前追溯边界冲突；注销策略尚需单独设计。

依据：[D2]、[C10]。

## 21. Job Matching 为什么先检索再让 Agent 排序？

**标准回答：** 程序先按岗位状态和明确筛选条件选有界候选，再计算资格与结构化匹配，模型只解释和 rerank。这降低无关上下文，也避免模型自由发明候选或资格。

**详细解释：** 实际筛选能力受结构化字段覆盖限制，不能说所有地区、学历、专业条件都完整支持。公共数据不足明确返回 coverage insufficient，不临时联网补库。

**可能继续追问：** 候选上限怎么配置？只有四个岗位怎么办？

**容易踩坑的错误回答：** “把所有 JD 和简历交给模型即可。”缺少可控资格和可复现输入。

依据：[D5]、[D11]、[C5]。

## 22. Evidence Matrix 的四种状态是什么意思？

**标准回答：** SUPPORTED、PARTIAL、NO_EVIDENCE、NOT_APPLICABLE 区分材料支持程度。NO_EVIDENCE 仅表示现有材料没证明，不是用户不会；这比一个匹配百分比更可解释。

**详细解释：** 候选侧来自已验证事实和真实证据，岗位侧来自 Requirement/JD。当前对齐是确定性结构化/文本规则，不是完整语义理解，也不使用 embedding 猜技能。

**可能继续追问：** PARTIAL 如何定义？同义词能匹配吗？

**容易踩坑的错误回答：** “没有提 Kafka 就证明不会 Kafka。”只能表达证据缺口。

依据：[D5]、[C5]。

## 23. hard eligibility 与 fit 有什么区别？

**标准回答：** eligibility 是硬条件能否确认，fit 是已有事实与岗位的技术对齐。技术相关性不能覆盖学历等明确不满足；缺少可靠字段就保留 UNKNOWN，模型不能改判。

**详细解释：** 湖北移动博士要求保留，但当前真实 Profile 没有可靠结构化资格字段，所以记录 UNKNOWN。不能拿教育描述里的一个词替代已定义的资格输入，也不能把未知称为满足。

**可能继续追问：** UNKNOWN 是否允许探索？明确不满足如何展示？

**容易踩坑的错误回答：** “这个人项目好，可以忽略博士要求”“已正确证明真实用户不满足博士。”后者也没有依据。

依据：[D5]、[D11]、[C5]。

## 24. 为什么保存三层 rank，也不展示 87.4%？

**标准回答：** pre_rank 记录程序结构化排序，agent_rank 记录模型 rerank，final_rank 记录固定 policy 合成结果，方便解释来源。匹配等级要经过证据与资格约束，不给虚假精确概率。

**详细解释：** Agent 只能排允许集合里的真实 Job，不能让 NOT_SATISFIED 混进正常推荐。排序数字是顺序，不是统计意义的成功投递概率。

**可能继续追问：** 模型把证据不足岗位排第一怎么办？

**容易踩坑的错误回答：** “百分比只是模型估一下，更直观。”直观不等于可解释与准确。

依据：[D5]、[C5]。

## 25. 为什么能复用 MatchRun？何时不能复用？

**标准回答：** 同用户、相同完整冻结输入和模型/Prompt/policy，可以复用已有成功结果，并标记来源，不再调用模型。Profile、JD、Requirement、岗位状态、偏好或过滤变化时不能复用。

**详细解释：** 现实现是在有界历史范围查找成功结果，不是全站永久缓存。已有详情只读持久化结果；重用保留当前 Run 的解释边界，不修改旧推荐。

**可能继续追问：** 如何防止跨用户复用？首次匹配为什么仍慢？

**容易踩坑的错误回答：** “只按 job_id 缓存，所有用户通用。”会错用私人事实与偏好。

依据：[D8]、[C5]。

## 26. 从历史推荐开始面试，为什么不能直接复制推荐快照？

**标准回答：** 推荐解释过去，面试要面对当前可用岗位。点击时只传公司和岗位，由 M4 重新检查岗位、当前 Profile/JD 和已批准资料，再固定一个新的面试快照。

**详细解释：** 历史 Run 不变，不等于允许对已关闭岗位启动新面试。推荐输入和本场面试输入有各自版本边界，不能混为一套。

**可能继续追问：** 旧推荐的 JD 更新后如何解释差异？

**容易踩坑的错误回答：** “历史推荐是什么，面试永久沿用什么。”会漏掉岗位关闭和用户修正。

依据：[D5]、[C4]。

## 27. 为什么有 ProfileVersion 还要 InterviewContextSnapshot？

**标准回答：** ProfileVersion 只固定候选事实，一场面试还依赖 JD、要求、题库、面经和记忆。Snapshot 把本场实际输入一起固定，后续公共题目编辑或记忆批准不影响本场。

**详细解释：** 不可变实体保存版本引用；还不是不可变版本的公共题目，保存实际最小内容及 fingerprint，只有 ID 不够。Agent 每轮读快照，不拉“最新画像”。

**可能继续追问：** 快照会不会太大？是否每轮发给模型全部内容？

**容易踩坑的错误回答：** “数据库 ID 固定，就意味着内容固定。”可编辑记录仍会变化。

依据：[D3]、[D9]、[C4]。

## 28. 提问与评价为什么分开？

**标准回答：** Main 负责组织自然问题，Evaluation 负责对实际回答按五维评价并提动作建议。分开可以分别定义 Prompt、验证、审计和失败恢复，评价不能随提问自由重写。

**详细解释：** 两者都不直接推进状态。评价失败时不能用默认分数继续；评价有效而下一题失败时，需要保留可复用结果，避免重新评价同一回答。

**可能继续追问：** 会不会多一次请求变慢？

**容易踩坑的错误回答：** “分开完全没有代价。”通常有串行延迟，但换来明确责任和恢复边界。

依据：[C4]、[D8]。

## 29. SessionController 为什么是唯一状态写入口？

**标准回答：** 否则模型、旧 service 和前端可能各自推进，形成分裂状态。Controller 统一校验 Answer、Question 和 Session 的合法转换，再提交动作。

**详细解释：** 当前题以 active_question_id、状态与顺序定位，兼容 index 由程序同步，不是多个路径独立改数组下标。动态插题不应使当前题漂移。

**可能继续追问：** legacy Session 如何兼容？

**容易踩坑的错误回答：** “模型返回 NEXT，前端 index++。”数据库未必已经接受该动作。

依据：[D3]、[C4]。

## 30. FOLLOW_UP、DEEP_DIVE、NEXT 有什么区别？

**标准回答：** FOLLOW_UP 补当前回答的缺失点；DEEP_DIVE 深入技术决策或原理，生成关联父题的新问题；NEXT 切换到下一主题或主问题。动作都受 Controller 的预算和状态约束。

**详细解释：** 追问不能只是重复题干，深挖也不是长一点的同义句。模型生成措辞，但 parent、次数、轮次和是否允许由程序校验。

**可能继续追问：** 一直答不好是否无限追问？

**容易踩坑的错误回答：** “Follow-up 和 Deep-dive 只是展示名字不同。”业务意图与题目关系不同。

依据：[D3]、[D10]、[C4]。

## 31. 怎么避免技术面试一直问学历或同一项目？

**标准回答：** 程序从持久化问题重建主题覆盖，限制背景类主问题和连续技术主题，再把未覆盖优先级给 Main。题库是意图来源，不直接决定最终措辞。

**详细解释：** 前五个主问题最多一个背景，全场最多两个；已知技术主题连续三轮冷却，追问继承父题主题。权重是选题优先级，不强行在六轮里凑精确比例。

**可能继续追问：** 同义问题能完全识别吗？刷新后次数会丢吗？

**容易踩坑的错误回答：** “LLM 自己记次数”“完整语义去重已解决。”当前是确定性主题与文本 fingerprint，仍有限制。

依据：[D10]、[C4]。

## 32. 前端重试如何避免重复 Answer？

**标准回答：** 回答绑定 session、question 与稳定幂等键。请求失败后重试复用同一业务键，不因为 SSE 重连生成另一条回答，内容与题目也要一致。

**详细解释：** 幂等还要结合唯一约束和有效 Evaluation 对应校验。点击按钮被禁用只是体验措施，不能替代后端并发保护。

**可能继续追问：** 用户改了回答内容还用旧 key 怎么办？

**容易踩坑的错误回答：** “每次重试生成新 UUID，反正不会撞。”这恰好制造重复业务意图。

依据：[D3]、[D8]、[C4]。

## 33. 模型调用期间为什么不持有数据库长事务？

**标准回答：** 网络调用可能几十秒甚至超时，长事务会占连接和锁。流程是读取快照与版本、短事务认领、调用模型，再短事务重查状态和版本后提交。

**详细解释：** 新结果必须通过 version/attempt fencing；另一请求已推进就返回 conflict，不覆盖新状态。个别认领或发布阶段可以有短行锁，但不是贯穿模型调用。

**可能继续追问：** 读取后到提交之间数据变化怎么办？

**容易踩坑的错误回答：** “先锁住 Session，直到模型返回最安全。”安全但会把可用性拖垮，也未处理外部取消。

依据：[C3]、[C4]、[C5]。

## 34. 第一题为什么曾经被判预算耗尽？

**标准回答：** 不是 question_budget=10 被用完，而是调用前保守 token 预留没有按实际 usage 结算，第二次调用触发错误预算保护。旧分支还允许未评价回答进入结束，两个问题叠加。

**详细解释：** 修复后预留只是调用期间占额，有实际 usage 就对账释放；未知 usage 的失败保守计费，不假设免费。预算不足也不能把未评价回答伪装成成功。

**可能继续追问：** 字符、UTF-8 字节和 token 为什么不能混算？

**容易踩坑的错误回答：** “把题目预算调大”“给 Session 52 加特判。”都没有修复结算和状态不变量。

依据：[D8]、[C4]。

## 35. Evaluation 失败后为什么不能自动结束？

**标准回答：** Answer 已保存不等于评价完成。失败保留审计与可恢复状态，不标记成功 EVALUATED，不自动下一题，也不补一个默认 3 分让流程继续。

**详细解释：** 完成统计只看有效持久化评价。若预算或时间确实不够，应明确反馈当前回答处理失败，而不是伪造 COMPLETE 和表现分析。

**可能继续追问：** 用户如何知道能重试？

**容易踩坑的错误回答：** “为了不卡住，失败先记中等分。”会污染统计与 Coach 的依据。

依据：[D8]、[C4]、[C6]。

## 36. 评价成功但下一题生成失败，要重新评分吗？

**标准回答：** 不应盲目重新调用评价。当前恢复路径会在一致的回答、模型和 Prompt 边界内复用已经验证的评价 artifact，再处理下一题；最终有效评价仍要按正常事务落库。

**详细解释：** Step 成功与最终业务提交分开。复用需要核验输入和当前状态，不是复用任意失败 Run，也不能拿另一回答的结果顶替。

**可能继续追问：** 新 Prompt 是否允许复用旧评价？

**容易踩坑的错误回答：** “只要日志有 JSON 就可以入库”“失败重跑全部最简单。”前者不安全，后者重复成本和评分。

依据：[D10]、[C4]。

## 37. 为什么保留 SSE，不改 WebSocket？

**标准回答：** 当前主要是回答请求后的单向阶段和结果通知，SSE 配合 HTTP 足够。可靠性重点是数据库提交后再发结果事件，以及跨网络 chunk 的正确解析，不是换协议。

**详细解释：** 前端 parser 状态跨 read 保留，不能每个 chunk 重置 event/buffer。阶段事件不等于成功事件；刷新以数据库状态恢复，不能靠最后一条 SSE 猜结果。

**可能继续追问：** 一条事件被拆成三个 chunk 怎么办？

**容易踩坑的错误回答：** “HTTP 200 就表示评价成功”“发完事件再 commit。”浏览器可能看见不存在的结果。

依据：[C8]、[D3]、[D8]。

## 38. 浏览器断连为什么会留下 RUNNING？

**标准回答：** 请求任务被取消，CancelledError 没进入普通 Exception 清理路径，Answer 和审计留在处理中。修复是显式取消处理、有界 shield 清理与过期认领恢复，而不是简单拉长 timeout。

**详细解释：** 客户端取消、Provider 超时和状态冲突要分开诊断。取消本地任务不证明 Provider 没消耗 token；没有返回 usage 时保持未知与保守预算。

**可能继续追问：** 清理本身也被取消怎么办？

**容易踩坑的错误回答：** “忽略取消继续后台跑就好了”“断连都等于模型慢。”不符合实际证据。

依据：[D8]、[C4]。

## 39. 怎么判断 timeout 来自输入太大还是 reasoning？

**标准回答：** 我按输入体积、结构化输出与 reasoning 分层诊断。压缩后完整请求仍超时，受控观察显示 120 秒内没有正文；关闭 thinking 后完整结构化输出约 12.178 秒完成，支持该次瓶颈主要在 reasoning/生成阶段。

**详细解释：** non-thinking 完整结构化输出约 12.18 秒，但随后仍 grounding 失败，证明性能和业务正确性是两件事。这个诊断不能推广为所有请求的唯一根因。

**可能继续追问：** TTFT 指正文还是 reasoning？为什么不直接加 timeout？

**容易踩坑的错误回答：** “输入压缩后就解决所有 timeout”“第一 chunk 就是用户正文。”都不成立。

依据：[D7]、[D8]。

## 40. 模型审计记录什么，不记录什么？

**标准回答：** 记录角色、实际 provider/model、Prompt 版本、状态、耗时、usage、repair、fallback 和错误类别。没返回的 tokens 不猜，隐藏 reasoning 不保存。

**详细解释：** HTTP、结构化、业务提交的成功层级需要分别看。历史日志还需逐路径脱敏审查，不能宣称整个旧系统所有日志都已经通过完整隐私治理。

**可能继续追问：** 成本如何计算？能否保存原始响应排错？

**容易踩坑的错误回答：** “timeout 没费用”“所有日志原样保存方便复盘。”可能泄漏材料和隐藏推理。

依据：[C2]、[D6]、[D8]。

## 41. Coach 如何避免重新编分数？

**标准回答：** statistics 独立从有效 Evaluation 计算，Coach 的输出 Schema 不提供整体分数或平均数写入口。报告明确分开确定性数据与模型分析。

**详细解释：** 均值不是最后一题的分数，没有有效评价时是 null，不让模型补表现。五维评分仍来自逐题评价模型，它们不是客观能力认证。

**可能继续追问：** 什么叫有效 Evaluation？整体平均如何算？

**容易踩坑的错误回答：** “Coach 根据感觉给最终总分”“平均分准确证明个人技术水平。”

依据：[C6]、[D4]。

## 42. Report regeneration 如何不影响旧报告和 Session？

**标准回答：** Session 完成与报告生成解耦。新 generation 完整通过后才在事务内切换 current；失败保留历史，旧成功报告继续有效，不回滚已完成面试。

**详细解释：** 自动 retry 复用 session、evaluation set 和 Prompt 对应的业务 key；主动 regenerate 可以产生新 generation。数据库限制同一场最多一个有效 current，不能先取消旧 current 再调用模型。

**可能继续追问：** 两个报告同时成功怎么办？

**容易踩坑的错误回答：** “生成新报告前先删旧报告”“Coach 失败把 Session 改回进行中。”

依据：[D4]、[C6]。

## 43. recurring issue 的次数怎么来？

**标准回答：** Coach 只给支持的 Evaluation ID，resolver 验证本场有效引用并按唯一 ID 计数。至少两条不同有效评价才能称为重复问题，单次只能是 weakness 或 observation。

**详细解释：** 模型不能生成 occurrence_count，也不能伪造 Question ID。resolver 回填真实问题和评价摘要；定性文本仍需要防止绝对能力结论，但不是完整语义事实判定器。

**可能继续追问：** 同一个 ID 重复五次是否算五次？

**容易踩坑的错误回答：** “模型说出现三次就保存三次。”必须去重并验证。

依据：[C6]、[D4]。

## 44. PerformanceObservation 为什么不写 UserSkill？

**标准回答：** 它描述某场面试中的表现，不是简历或用户确认的稳定事实。每条观察引用 Session、Report 和有效 Evaluation，和 CandidateProfile 分层保存。

**详细解释：** “本场 RAG coverage 较低”可以观察，“用户不会 RAG”不能自动落长期事实。旧 UserSkillProfile 保留动态表现语义，新的简历 UserSkill 不是同一真相源。

**可能继续追问：** 下一场能参考观察吗？

**容易踩坑的错误回答：** “一次回答差就降低长期技能等级。”越过用户事实边界。

依据：[D2]、[D4]、[C6]。

## 45. Memory approve 为什么必须单事务？

**标准回答：** 需要一起完成长期 Memory、用户确认事件和 Proposal 状态，任一步失败全部回滚。不能调用一个内部先 commit 的旧 service，再单独批准建议。

**详细解释：** 行锁序列化同一 Proposal 的审批；相同重复 approve 返回已有结果，冲突决策拒绝。correct 保存原文与修正文并在同一事务批准，reject 不写 Memory。

**可能继续追问：** Memory 写成功但状态更新失败怎么办？

**容易踩坑的错误回答：** “分两次提交，失败了下次补。”中间状态会影响后续面试。

依据：[C7]、[D4]。

## 46. 下一场如何使用记忆，又不发生漂移？

**标准回答：** 创建 Session 时分别读取有效的已批准 Memory 和近期表现观察，按各自数量与预算截取后固定。PENDING 不进入，已开始的 Session 不动态读最新记忆。

**详细解释：** Snapshot 保存真实内容、ID/版本或 fingerprint 及预算信息；观察另一个字段，不能当长期事实。实测批准后新场包含，旧场不变，但不能据此声称效果有因果提升。

**可能继续追问：** 面试中途批准一条，下轮能看到吗？

**容易踩坑的错误回答：** “每轮读取最新记忆更智能”“PENDING 也可先用。”破坏确认边界与可复现性。

依据：[D9]、[C4]、[C7]。

## 47. Celery 和 Redis 分别承担什么？

**标准回答：** Celery 执行已有简历等离线任务，Redis 用于 broker、结果后端和其他缓存/协调；长期业务事实与状态仍以 PostgreSQL 为准。在线面试模型调用不是自动转入 Celery。

**详细解释：** late ack 和 prefetch=1 改善任务处理方式，不提供 exactly-once。任务重投仍需领域幂等；源码也没有给每个失败自动无限重试的策略。

**可能继续追问：** Worker 重启如何防止重复 Profile？

**容易踩坑的错误回答：** “Redis 是整个业务状态机”“Celery 保证任务只执行一次。”

依据：[C9]、[D0]。

## 48. PostgreSQL、MinIO、Milvus 的边界是什么？

**标准回答：** PostgreSQL 保存业务关系、版本和约束；MinIO 保存上传等对象材料；Milvus 保存已发布可搜索知识向量。岗位硬资格和画像事实不以向量相似度为真相。

**详细解释：** 原始/待审面经不能直接进入正式向量索引。Source 的 URL 与 Snapshot 的内容有不同去重约束，跨 URL 的相同正文仍可保存，独立内容数量由 fingerprint 分组计算。

**可能继续追问：** DB 和对象存储能否一次事务？为什么 content hash 不全局唯一？

**容易踩坑的错误回答：** “所有数据都进向量库”“相同正文就删掉其他来源。”会破坏审核边界与 provenance。

依据：[D0]、[D1]、[D11]。

## 49. 怎么证明不是只有 Demo？性能怎么回答？

**标准回答：** 我区分 Replay 回归、真实模型和真实网页/浏览器。最新记录有 340 个后端、31 个前端测试，以及真实六轮完成；首次匹配和评价仍主要耗时在模型，并没有做生产压测。

**详细解释：** 最近六轮 Main 平均 8.25 秒、Evaluation 24.63 秒，是该场小样本。已有匹配详情不再调用模型；历史浏览器验证过审批和刷新，最新完整补验则被登录过期阻塞，不能说所有验收全部通过。

**可能继续追问：** 测试是否调用真实模型？一次结果可否代表 SLA？

**容易踩坑的错误回答：** “340 测试证明线上稳定”“全部 Live 搜索 E2E 已通过”。

依据：[D6]、[D8]、[D9]、[D10]。

## 50. 项目最大的不足是什么，下一步会怎么做？

**标准回答：** 当前是个人项目，模型延迟和质量不稳定，硬资格结构化覆盖有限，Live Search 冻结，面试也不等同真人。下一步先补数据质量、可重复验收和失败恢复，不急着再加 Agent。

**详细解释：** 对每个改进都设验证目标：资格字段覆盖率与人工确认、固定样本的提问重复率、报告引用正确率、真实请求延迟分布。现在没有这些生产指标，不能说已经达到。

**可能继续追问：** 哪个问题最优先？真实部署还缺什么？

**容易踩坑的错误回答：** “主要不足是模型还不够多”“换个更强模型就都解决。”忽略系统与数据边界。

依据：[D10]、[D11]、[D8]。

## 核验索引

- [D0] [架构与运行边界](../02_architecture.md)。
- [D1] [公共 Research M2B](../20_public_data_research_m2b.md)。
- [D2] [Candidate Profile M3](../21_candidate_profile_m3.md)。
- [D3] [Mock Interview M4](../22_mock_interview_core_m4.md)。
- [D4] [Report / Memory M5](../23_interview_coach_m5.md)。
- [D5] [Job Matching M6](../24_job_matching_m6.md)。
- [D6] [F2 真实模型：含失败及 Attempt 2](../25_real_model_acceptance.md)。
- [D7] [Context 分组修复](../30_profile_pipeline_context_fix.md)、[Fact contract 修复](../31_profile_fact_contract_fix.md)、[最终 Evidence Selection](../32_profile_evidence_selection_contract.md)。
- [D8] [预算误结束修复](../35_matching_presentation_interview_budget_fix.md)、[延迟、取消恢复与 MatchRun 复用](../37_interview_latency_recovery.md)。
- [D9] [Doubao 产品验收与记忆闭环](../38_product_polish_acceptance.md)。
- [D10] [最新主题策略、真实六轮与测试边界](../39_final_topic_ux_acceptance.md)。
- [D11] [真实人工资料与限定资格覆盖](../34_real_manual_e2e_readiness.md)。
- [C1] [ModelRouter / guard](../../apps/api/src/interview_api/infrastructure/models/router.py)。
- [C2] [Structured Output / repair](../../apps/api/src/interview_api/infrastructure/models/structured.py)、[Provider 错误与 thinking](../../apps/api/src/interview_api/infrastructure/models/provider.py)。
- [C3] [Profile agent](../../apps/api/src/interview_api/modules/candidate_profile/agent.py)、[Evidence resolver](../../apps/api/src/interview_api/modules/candidate_profile/evidence.py)、[context / alias](../../apps/api/src/interview_api/modules/candidate_profile/context.py)、[持久化和 correction](../../apps/api/src/interview_api/modules/candidate_profile/service.py)。
- [C4] [SessionController](../../apps/api/src/interview_api/modules/interview/controller.py)、[在线模型和恢复](../../apps/api/src/interview_api/modules/interview/core_service.py)、[Snapshot](../../apps/api/src/interview_api/modules/interview/context.py)、[topic policy](../../apps/api/src/interview_api/modules/interview/topics.py)。
- [C5] [MatchRun service](../../apps/api/src/interview_api/modules/job_matching/service.py)、[匹配模块](../../apps/api/src/interview_api/modules/job_matching/)。
- [C6] [报告 service](../../apps/api/src/interview_api/modules/interview_report/service.py)、[deterministic statistics](../../apps/api/src/interview_api/modules/interview_report/statistics.py)、[evaluation resolver](../../apps/api/src/interview_api/modules/interview_report/resolver.py)。
- [C7] [MemoryProposal 审批事务](../../apps/api/src/interview_api/modules/memory/proposal_service.py)。
- [C8] [API SSE](../../apps/api/src/interview_api/modules/interview/router.py)、[前端 SSE 读取](../../apps/web/src/api/interview.ts)。
- [C9] [Resume Parser](../../apps/api/src/interview_api/modules/resume/parser.py)、[Resume processor](../../apps/api/src/interview_api/modules/resume/processor.py)、[Celery Resume tasks](../../apps/worker/src/interview_worker/tasks/resume_tasks.py)、[Celery 配置](../../apps/worker/src/interview_worker/celery_app.py)。
- [C10] [Resume service](../../apps/api/src/interview_api/modules/resume/service.py)、[M3 migration 0021](../../apps/api/alembic/versions/0021_add_candidate_profile.py)。

目录链接用于定位模块，精确规则请进入相应文件。材料不承诺当前真实账号仍登录或服务正在运行；最近完整浏览器补验受登录过期阻塞，见 [D10]。
