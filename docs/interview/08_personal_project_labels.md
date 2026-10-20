# 我的项目个人标签

## 标签一：Agent 工程化，而不是模型调用堆叠

**一句话解释：** 我关注模型输出怎样安全进入业务，而不只是模型能不能回答。

**项目中的证据：** 六个角色使用逻辑 route、版本化 Prompt 和统一 Structured Output；提问与评价不直接改状态，Controller 才能推进；格式 repair 有界，业务校验失败不能换模型刷成功。

**面试如何表达：**

> 我会先定义模型可以决定什么、程序必须决定什么，再设计输入输出和失败路径。这个项目中，模型可以建议追问，但不能自己完成面试或修改硬资格。

## 标签二：Evidence 与可靠性意识

**一句话解释：** 我把可追溯性、版本与边界当成产品能力，而不是事后补日志。

**项目中的证据：** Profile 改为选择真实 Evidence；技能状态只允许向下收敛；MatchRun 和 Interview 固定输入；报告数字只来自有效评价；MemoryProposal 必须用户确认。

**面试如何表达：**

> 我不把模型写出的文本直接当事实。没有证据就保留未知，尤其不能用技术匹配去替代学历等硬资格判断。

## 标签三：真实验收驱动的故障定位

**一句话解释：** 我会用具体数据、时序和持久化状态验证假设，而不是先扩大 timeout。

**项目中的证据：** 真实 PDF 暴露 section 分组错乱；小输出与流式诊断定位 DeepSeek reasoning；预算误判通过 reservation 和实际 usage 对账定位；断连卡死追到 CancelledError 和清理路径。

**面试如何表达：**

> 排障时我先确认失败在哪一层：HTTP、Structured Output、证据 validator，还是业务提交。修复后检查同一回答没有重复、当前版本完整、失败历史仍保留。

## 三个标签串起来

> 我的特点是把 Agent 做成可验证的业务链路：先划清职责，再建立证据和状态边界，最后用真实资料、模型和浏览器验收检验这些边界。

这不是“已经具备大厂生产经验”的替代说法。可证明的是个人项目中的机制与排障过程，不是不存在的上线规模。

## 核验依据

[证据选择](../32_profile_evidence_selection_contract.md)、[首题预算修复](../35_matching_presentation_interview_budget_fix.md)、[取消恢复](../37_interview_latency_recovery.md)、[最新验收](../39_final_topic_ux_acceptance.md)。
