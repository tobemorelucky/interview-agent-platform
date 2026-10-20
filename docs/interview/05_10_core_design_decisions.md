# 10 个核心设计决策

这里的“最初方案”区分实际历史实现和设计阶段备选，不把所有取舍包装成已经发生的数据事故。先讲问题与取舍，源码留到被追问时再展开。

## 01. Profile：从事实生成转为 Evidence Selection

**问题 →** 需要长期可追溯的候选事实，而不是一段听起来合理的简历总结。

**最初方案 →** 模型输出 label、自由 statement 和 Evidence 引用，程序验证字面支持。

**暴露的问题 →** non-thinking 解决速度后，Structured Output、alias 和状态收敛通过，statement 仍触发 UNSUPPORTED_CANDIDATE_FACT。自由改写与严格抽取 contract 不匹配，Prompt 强调摘录也不能保证。

**最终方案 →** 模型只选择/分类证据，提标签和技能建议状态。程序验证标签，保存全部引用，用真实代表 excerpt 生成兼容 statement；summary/strengths 不进入稳定事实。

**Trade-off →** 牺牲自由表达与摘要完整性，换取稳定事实边界。单一 representative excerpt 不等于全部证据汇总，既有下游文本规则可能漏掉次要细节，不能说语义覆盖完全。

**面试一句话 →** “我不再让模型写事实证明自己，而是让它选择已有证据，由程序建立事实。”

依据：[最终 contract 与下游审计](../32_profile_evidence_selection_contract.md)、[resolver](../../apps/api/src/interview_api/modules/candidate_profile/context.py)。

## 02. Evidence：持久化长 key，调用边界短 alias

**问题 →** 原文 1490 字符，模型请求 14620 字符，真实证据之外存在明显标识与包装开销。

**最初方案 →** 把持久化 re_hash 等真实标识带入每条模型 Evidence。

**暴露的问题 →** 40 个长 key 的值约 2680 字符，模型并不需要 hash 才能选择证据；它们增加输入和输出负担。

**最终方案 →** 按稳定顺序映射 E01～E40，先精确还原再校验。真实 key、excerpt、offset 和 hash 保留在服务器；完整 Report 不再无条件进入 context。

**Trade-off →** 增加调用级映射与 unknown alias 拒绝逻辑；alias 不能跨调用引用。首次修复请求变为 9707 字符，但仍曾超时，不能把瘦身说成性能问题全部解决。

**面试一句话 →** “模型只需要可引用的短身份，服务器才需要完整 provenance。”

依据：[context 体积对账](../30_profile_pipeline_context_fix.md)、[alias 实现](../../apps/api/src/interview_api/modules/candidate_profile/context.py)。

## 03. Reasoning：按 route 配置，而不是统一高思考

**问题 →** 严格抽取任务长时间没有用户可用正文。

**最初方案 →** DeepSeek V4 Pro 沿用 Provider 默认 thinking 行为，尝试诊断输入体积与输出负担。

**暴露的问题 →** 完整请求在 120 秒诊断中有 reasoning 但无正文；non-thinking 完整结构化输出约 12.178 秒完成，业务校验仍另有失败。

**最终方案 →** 对 resume_profile 明确 thinking disabled，不扩大到 180/300 秒来掩盖问题。后续按授权切换当前统一 Doubao 2.1 的低延迟配置，保留历史模型与策略审计。

**Trade-off →** 减少开放推理并不保证输出正确，必须保留 Schema、alias 和 Evidence validator；任务策略可配置，不在领域代码硬编码。

**面试一句话 →** “我先用时序诊断区分模型慢和业务错，再按任务选择 reasoning policy。”

依据：[route-level 诊断与验收](../31_profile_fact_contract_fix.md)、[当前模型记录](../38_product_polish_acceptance.md)、[Router](../../apps/api/src/interview_api/infrastructure/models/router.py)。

## 04. Token 预算：预留与真实消耗分开

**问题 →** Session 52 第一题回答后结束，但有效评价为零。

**最初方案 →** 调用前按上下文和输出上限保守预留，旧路径没有及时按实际 usage 释放预留，还存在未评价预算结束分支。

**暴露的问题 →** 首次模型实际 total 为 20155 token，却留下 106474 的预留；下一次调用被预算保护拒绝。题目数不是根因。

**最终方案 →** 每次调用有唯一 reservation，成功有 usage 就结算并释放预留，未知失败保守计入。未评价回答不再走成功 COMPLETE，失败可安全重试。

**Trade-off →** 需要维护 reservation、审计和有效评价三个边界。Provider 没 usage 时仍可能偏保守，不能把取消请求当零费用。

**面试一句话 →** “预算是暂占额度加实际账本，不是把每次估算永久累加。”

依据：[预算事故](../35_matching_presentation_interview_budget_fix.md)、[Controller](../../apps/api/src/interview_api/modules/interview/controller.py)。

## 05. Cancellation：显式恢复，而不是无限 pending

**问题 →** 浏览器断连后回答已保存，页面和审计停在 RUNNING，没有评价。

**最初方案 →** 普通异常路径能处理模型失败，但请求取消没有进入该清理逻辑。

**暴露的问题 →** CancelledError 绕过 except Exception，后续 cleanup 也可能在取消状态下失败。不能把这种情况直接归因于模型 timeout。

**最终方案 →** 显式处理取消，使用有界 shield 完成必要清理；lease/阶段过期与版本 fencing 支持恢复，页面刷新读数据库，同一回答键安全重试。

**Trade-off →** 取消清理不能无限等待，也不能证明 Provider 停止计费。已超出面试真实时间预算的旧场不会凭空恢复成完成状态。

**面试一句话 →** “网络断开是独立的业务失败路径，要清理认领和恢复状态，而不只是捕获模型异常。”

依据：[断连时序与恢复](../37_interview_latency_recovery.md)、[core service](../../apps/api/src/interview_api/modules/interview/core_service.py)。

## 06. MatchRun：复用成功结果，但冻结全部解释输入

**问题 →** 新匹配主要耗时在模型，用户重复操作不应重复承担相同成本。

**最初方案 →** 新 generation 默认重新调用模型；已有详情本身只是数据库读取。

**暴露的问题 →** 一次拆分测量中，DB 候选查询约 19.82 ms，模型约 59.612 s，说明优化普通查询不是主要收益点。

**最终方案 →** 对同用户完整冻结输入以及 model/Prompt/policy 一致的成功 Run 做有界历史复用，保存复用来源，不新增模型调用；详情加载只读并行请求。

**Trade-off →** 不能只按 Profile/Job ID 缓存，JD、Requirement、岗位状态和已批准偏好都影响有效性。首次匹配仍慢，不能承诺缓存命中率或全站低延迟。

**面试一句话 →** “不是缓存一段回答，而是复用同一业务输入边界内已验证的结果。”

依据：[阶段测量与复用](../37_interview_latency_recovery.md)、[MatchRun service](../../apps/api/src/interview_api/modules/job_matching/service.py)。

## 07. Memory：分析提出建议，用户才确认长期内容

**问题 →** 面试分析如果直接写长期记忆，会把一次表现变成永久标签。

**最初方案 →** 旧系统存在显式 consolidate 等兼容路径；M5 设计时需要避免它绕过新式 Session 的审批。这里是边界治理，不声称已经发生用户记忆污染事故。

**暴露的问题 →** Observation、Proposal 与长期事实语义不同；旧 service 内部提交会使“Memory 已写、Proposal 未批准”成为事务风险。

**最终方案 →** Proposal 初始 PENDING，approve/correct/reject 使用统一事务；批准才写 Memory 和用户事件，拒绝不写，修正保留原文。新式 Session 不能用 legacy consolidate 绕过。

**Trade-off →** 增加用户操作，但保留控制权。审批是用户认可，不是自动证明内容客观真实。

**面试一句话 →** “模型负责建议记什么，用户决定哪些内容可以影响下一场。”

依据：[M5 边界](../23_interview_coach_m5.md)、[审批事务](../../apps/api/src/interview_api/modules/memory/proposal_service.py)。

## 08. ContextSnapshot：历史引用与实际内容一起固定

**问题 →** 当公共题库和 Memory 后续被修改，历史面试仍需解释当时依据。

**最初方案 →** 这是 M4 从设计阶段就引入的边界，不是先发生历史漂移再补救；后续扩展批准 Memory 与表现观察的分层固定。

**暴露的问题 →** 只有 ID 不足以冻结可编辑的 InterviewQuestion/Experience；每轮读取最新 Memory 也会改变本场条件。

**最终方案 →** 不可变 Profile/JD 引用版本；可编辑公开资料保存实际内容与 fingerprint；已批准 Memory 和近期 Observation 分字段、独立预算，一次性固定。

**Trade-off →** 快照占存储，模型 context 还要做保留语义的投影。旧 legacy 场次没有可靠信息就明确隔离，不猜造快照。

**面试一句话 →** “版本解决单个对象的历史，快照解决这场面试使用了哪一组对象。”

依据：[M4 Snapshot](../22_mock_interview_core_m4.md)、[第二场记忆验收](../38_product_polish_acceptance.md)、[context builder](../../apps/api/src/interview_api/modules/interview/context.py)。

## 09. Agent / deterministic boundary：建议不等于写权限

**问题 →** 让模型既评价又推进，容易混淆“模型认为完成”和“数据库允许完成”。

**最初方案 →** 既有面试逻辑中有按 index 推进的路径；M4 增量接入时明确要收敛写入口，而不是保留多个隐式推进点。

**暴露的问题 →** 动态插题、模型失败、双请求和预算保护都会使下标式控制不足，真实首题事故进一步验证了状态不变量的重要性。

**最终方案 →** Main/Evaluation 返回 artifact 和建议，Controller 统一校验；active question、顺序和状态定位当前题，版本冲突不覆盖，提交成功后才发结果事件。

**Trade-off →** 比直接消费模型 JSON 多一层领域控制，但更能解释错误和恢复。它并不把模型的技术评价变成确定性真理。

**面试一句话 →** “让模型决定内容，让程序决定这个内容能否改变业务状态。”

依据：[M4](../22_mock_interview_core_m4.md)、[预算修复](../35_matching_presentation_interview_budget_fix.md)、[Controller](../../apps/api/src/interview_api/modules/interview/controller.py)。

## 10. 推荐展示：审计完整，默认界面简洁

**问题 →** 同一要求展示大量重复 explanation、Evidence、offset 与内部版本，求职者难以判断怎么准备。

**最初方案 →** 将底层 Evidence Matrix 逐条铺在推荐页面。

**暴露的问题 →** 相同事实重复命中多个要求，内部筛选 N/A、pre_rank 等挤占主要操作；真实用户反馈“像工程日志”。

**最终方案 →** 以 Requirement 聚合，去重核心理由、缺口和证据，默认只呈现匹配等级、亮点、风险和关键资格；详细原文与版本折叠，不重跑匹配。

**Trade-off →** 默认展示不可能容纳所有审计信息，必须提供可展开依据；简洁总结只能来自已保存匹配结果，不能再调用 LLM 编理由。

**面试一句话 →** “可解释不是把数据库字段全部展示，而是默认给决策信息，追问时提供证据。”

依据：[展示修复](../35_matching_presentation_interview_budget_fix.md)、[推荐页](../../apps/web/src/pages/JobMatchingPage.vue)。
