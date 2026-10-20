# 5 个真实 Bug / 故障故事

讲法：先用 60～90 秒讲 STAR 主线，再根据追问补指标和机制。下列结论来自本地真实资料与模型验收，不包装成生产事故；不展示真实简历正文、联系方式或密钥。

## 01. PDF section 顺序失真：40 条证据，项目却只有标题

### 现象与任务

真实 Resume 38 提取原文 1490 字符，产生 40 条原子 Evidence，总 excerpt 1450 字符。原 section 分布 general 34、education 1、projects 1、skills 4；项目类只有标题，项目正文散在 general。这会影响 Profile 组织和技能使用证据上限。

### 定位

从 raw_text、逐条 Evidence 的 offset 与 section 追到切分逻辑。证据逐行保存，section 依赖标题按提取顺序继承。PDF 的文本顺序不一定等于视觉版面的阅读顺序，不能把下一个标题之前的所有行都可靠归到同一段。

### 根因

原文事实没有消失，失真发生在“线性文本 → section”的解释层。长 key 和完整 Report 又加大了模型请求，但它们不是 section 错误的根因。

### 修复与结果

保留原子 excerpt、offset、key/hash，新增确定性 grouped context，用原文和 Report 中可精确匹配的项目/教育/技能提示辅助分组，不可靠仍 general。实际 context 变为 general 8、education 3、projects 23、skills 6；没有把数据库旧 Evidence 改写成新事实。

### 为什么没选其他方案

- 没让模型自由重分并重写证据，否则不能保留原始 provenance。
- 没声称实现版面重建或扫描 PDF OCR，当前 Parser 只是文本提取。
- 没仅加 timeout，分类失真与等待时间是不同问题。

### 学到什么 / 面试一句话

> 半结构化材料的错误不一定在模型，也可能发生在确定性预处理；先验证数据如何进入模型，再讨论 Prompt。

依据：[context 修复及分布](../30_profile_pipeline_context_fix.md)、[Parser](../../apps/api/src/interview_api/modules/resume/parser.py)、[分组实现](../../apps/api/src/interview_api/modules/candidate_profile/context.py)。

## 02. Profile 120 秒 reasoning 无正文：先定位，不无限等

### 现象与任务

DeepSeek V4 Pro 完整 Profile 请求长期没有结果。最初怀疑 14620 字符输入过重；context 修复到约 9707 字符后，单次 120 秒诊断仍超时。

### 定位

按输入处理、输出 Schema、reasoning 与非流式等待分层诊断。受控流式观察发现 reasoning 开始，但 120 秒内没有正文；non-thinking 完整结构化请求约 12.178 秒完成。性能恢复后却仍有事实校验失败，因此不能把 HTTP 成功当业务成功。

### 根因

在该次请求中，默认深度 thinking 和抽取任务不匹配，主要时间耗在 reasoning/生成阶段。不能据此断言所有历史 timeout 都是同一个原因，也不能从第一 chunk 推断已产生用户正文。

### 修复与结果

把 resume_profile 的 thinking 作为独立 route policy 显式 disabled，保留 60 秒正式预算、同一 validator 和真实模型审计。随后事实 contract 完成修复，正式 Profile generation 模型 step 12568 ms 成功，reasoning 0，ProfileVersion 1 与 current 指针正常落库。

### 为什么没选其他方案

- 没把正式预算改成 180/300 秒：延长等待不解决持续 reasoning。
- 没降低证据标准或用备用模型刷通过：性能和 grounding 分开处理。
- 当时没有全局关闭其他 Agent reasoning；后来模型策略变化是另一次用户授权，不能倒推当时所有 route 都这样配置。

### 学到什么 / 面试一句话

> 对结构化抽取，更多 reasoning 未必更可靠；我用时序和 usage 验证假设，再按任务配置策略。

依据：[context 仍超时](../30_profile_pipeline_context_fix.md)、[non-thinking 诊断](../31_profile_fact_contract_fix.md)、[正式成功指标](../32_profile_evidence_selection_contract.md)。

## 03. statement 与 literal Evidence 冲突：不是继续磨 Prompt

### 现象与任务

性能解决后，Prompt 1.0.3 的正式调用 HTTP/Schema/alias/技能状态收敛全部通过，最终却失败于 skills.12.statement，引用 E29/E30。失败 generation 保留，没有创建半成品 ProfileVersion。

### 定位

增加字段级诊断，区分引用不存在、引用中无字面支持、其他未引用 Evidence 可能支持。该次失败 statement 在引用和其他原文都没有连续字面支持；仅凭这一结果不能判断是合理改写还是事实杜撰，也不能断言用户没有该能力。

### 根因

让模型自由写 statement，却要求它满足严格 extractive literal contract，两种职责存在张力。即使 Prompt 明确要求摘录，也仍把不必要的文本生成风险留给模型。

### 修复与结果

改为 Evidence Selection：模型给 category、label、alias 和技能 proposed_state，服务端精确恢复引用、验证标签、选真实代表 excerpt。摘要/优势从稳定事实层剥离，全部证据边仍保存。

正式 Resume 38 生成成功：32 条稳定事实，包括 24 技能、2 项目事实、6 教育事实，71 条事实—证据关系；6 条教育事实不是 6 段学历。所有兼容 statement 都是已有真实 excerpt，没有拼接成新断言。

### 为什么没选其他方案

- 没用 embedding 或第二个 LLM 判“意思差不多”，否则严格边界变成另一次主观判断。
- 没自动替模型找别的 Evidence，更没有制造出处。
- 没删除下游 statement 字段：Matching、面试快照、UI/correction 仍需兼容，所以改生成责任，不改数据库 Schema。

### 学到什么 / 面试一句话

> 合同冲突要收敛职责，不是无限提示模型更听话；稳定事实可以来自模型选择，但文本应来自证据。

依据：[真实失败字段](../31_profile_fact_contract_fix.md)、[兼容审计与成功](../32_profile_evidence_selection_contract.md)。

## 04. 第一题就预算耗尽：估算不应成为永久消耗

### 现象与任务

Session 52 显示 Q1/10，用户回答后约 38 秒被告知预算耗尽。数据库只有一题、一回答，零有效 Evaluation。要求恢复“回答必须先有效评价，才能推进”的不变量。

### 定位

沿 Answer 保存、模型审计、预算与 Controller 分支追踪。question_budget=10、时间预算 7200 秒都不是当时真正耗尽原因；首调用 actual total 20155，保留的预估 reservation 却为 106474，下一调用的估算导致预算拒绝。

### 根因

调用前保守预留被当成永久累计消耗，没有正确结算；旧 budget_complete 还允许跳过未评价题并结束，预算误判因此升级为错误业务结果。

### 修复与结果

短事务认领唯一调用 reservation，Provider 返回 usage 后按实际结算、释放预留；失败 unknown usage 保守计入。评价失败的 Answer/Question 不再被标成功或 COMPLETE，同一回答可安全重试。增加至少三个有效回答/评价不会首题结束的状态机回归，后续真实六轮验收也完成了六个唯一 Answer 和六个 Evaluation。

### 为什么没选其他方案

- 没硬编码 Session 52 或扩大 question budget。
- 没记默认分数，避免掩盖没有评价的事实。
- 没把未知费用全部退回：本地取消不证明 Provider 没执行。

### 学到什么 / 面试一句话

> 资源账本和业务状态要分别正确；预算保护不能破坏“无有效评价不算完成”的不变量。

依据：[完整预算对账](../35_matching_presentation_interview_budget_fix.md)、[真实六轮](../39_final_topic_ux_acceptance.md)。

## 05. 浏览器断连后 Evaluation 卡死：取消不是普通异常

### 现象与任务

Session 57 的用户回答已经保存，Answer 9、AgentRun 82、StepRun 207 长期 RUNNING，没有 Evaluation，页面没有明确可重试反馈。

### 定位

请求有 SSE HTTP 200/start，但约 12 秒出现 request cancellation 和数据库 cleanup 取消。不是已确认的 Provider timeout，没有证据支持“token 太长”或 state_version 冲突；latency/usage 未返回就保持未知。

### 根因

asyncio.CancelledError 没被普通 except Exception 清理路径覆盖，取消中的 DB 操作也可能再次被取消，回答认领和审计没有收敛到失败/可恢复状态。

### 修复与结果

显式处理取消，必要清理使用有界 shield；阶段 lease、版本校验和持久化状态支持刷新恢复；同 Answer/幂等键安全重试，不重复保存回答或评分。新 Session 58 实际刷新触发取消，重试后仍是一 Answer、一 Evaluation；Session 59 后续三轮真实模型评价后正常进入下一题。

旧 Session 57 恢复时已超过其时间预算，只记录明确失败，不伪装成功完成。最新 Session 66 是另一个独立的六轮成功样本。

### 为什么没选其他方案

- 没禁用浏览器取消或依赖“用户不要刷新”。
- 没换 WebSocket：协议改变不替代服务端取消清理。
- 没为每次重试新建回答，也没删除失败历史刷成全绿。

### 学到什么 / 面试一句话

> 端到端可靠性必须包含断连时序；用户看不到结果时，先确认数据保存在哪一步，再定义能安全重做什么。

依据：[取消故障、恢复与三轮验收](../37_interview_latency_recovery.md)、[恢复服务](../../apps/api/src/interview_api/modules/interview/core_service.py)、[SSE 前端](../../apps/web/src/api/interview.ts)。

## 哪三个最有区分度？

优先讲 **statement contract 冲突、首题预算误结束、浏览器取消卡死**：分别体现 AI 事实建模、状态/账本一致性、异步失败恢复。reasoning 故事适合 AI 岗性能追问；PDF 故事适合材料质量与预处理追问。
