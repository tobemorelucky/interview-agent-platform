# 自适应面试与长期能力记忆：源码讲解底稿

依据 2026-10-24 本地工作树，不以远程 README 代替实现。正文先讲机制；面试官要求时再打开下列函数。所有路径相对仓库根目录 `E:\work\code\python\interview-agent-platform`，链接可直接打开对应源码。

## 1. 先说明真实边界

系统仍是六个模型角色：Research、Profile、Matching、InterviewMain、AnswerEvaluation、SummaryCoach。**没有独立 MemoryAgent**；长期能力由现有记忆服务增量扩展。V2.2 不是图记忆、概率知识追踪、向量检索或自动模型训练。

面试前选 Skill、岗位、轮次、风格；Main 在合法空间做策略与问题生成，程序负责权限、版本、预算、引用和提交。评价与记忆不是“所有 Agent 自由聊天”，而是通过已验证产物和冻结上下文协作。

## 2. 核心源码地图

| 重点 | 文件 / 函数 | 实际职责与验收依据 |
| --- | --- | --- |
| 记忆 API | [memory/router.py](../../apps/api/src/interview_api/modules/memory/router.py)：list_capabilities、correct_capability | GET 只读呈现；PATCH 归属、revision、排除与事务提交；51 的并发更正/隔离测试 |
| 有效观察 | [capability_service.py](../../apps/api/src/interview_api/modules/memory/capability_service.py)：observations、observation_scope | 只读 COMPLETED Session/Answer/已提交 Evaluation，核对 ledger、引用原文、Blueprint 和用户；不复制整套聊天 |
| 聚合与版本 | 同文件 refresh、lock、present、list | 用户级事务 advisory lock、确定性重算、指纹不变不加版本；GET 时效呈现不写版本 |
| 遗忘冲突 | [capability_rules.py](../../apps/api/src/interview_api/modules/memory/capability_rules.py)：project、DEFAULT_POLICY | 固定时钟可注入，状态规则而非分数衰减；51/52 的规则回归 |
| 用户控制 | capability_service.correct | 排除集合只增、revoked 持久墓碑、expected_revision 冲突；不让用户直接改成 VERIFIED |
| 记忆规划 | capability_rules.memory_plan、capability_service.plan | 只映射当前 Blueprint，最多3项/6000字符，携带观察引用与版本；53 实际请求包含该计划 |
| 快照 | [interview/context.py](../../apps/api/src/interview_api/modules/interview/context.py)：build_context；[controller.py](../../apps/api/src/interview_api/modules/interview/controller.py)：create | 冻结 Profile/JD/Skill/Prompt/Blueprint/MemoryGuidedPlan，内容指纹保存，不每轮读取新记忆 |
| 合法空间 | [adaptive.py](../../apps/api/src/interview_api/modules/interview/adaptive.py)：build_blueprint、decision_space、decision_candidates、planning_payload | 依据知识状态、主题冷却、追问预算生成当前合法组合 |
| 模型边界 | adaptive.AdaptiveSelection、resolve_selection、validate_decision | 模型选 C_*，程序还原目标/来源，不准自由拼 ID；50 修复、53 Live 通过 |
| Main | [agents.py](../../apps/api/src/interview_api/modules/interview/agents.py)：InterviewMainAgent.adaptive、InterviewAgent.call | 同一请求选择策略与生成问题，无额外 Planner；版本化 Prompt 见 adaptive_prompts.py |
| Evaluation | agents.AnswerEvaluationAgent.adaptive / validate_artifact；adaptive.resolve_observations | 当前答案五维评分+知识诊断，引用必须是本答连续原文，缺口映射程序校验 |
| 评分隔离 | [skill_engine.py](../../apps/api/src/interview_api/modules/interview/skill_engine.py)：agent_payload、evaluation_contract | V2.2 Eval 移除记忆规划/历史表现/approved memory，去掉风格与提示，保留岗位评分依据；53 两个实际 Eval 请求核验 |
| 业务编排 | [core_service.py](../../apps/api/src/interview_api/modules/interview/core_service.py)：answer、adaptive_answer、call_agent、stream | 先落 Answer，事务外模型，最后 Controller 提交；stage/SSE/失败恢复/预算结算 |
| 最终裁决 | controller.prepare_answer、commit_answer、complete | 锁定会话，核对版本/活动题/动作/来源，原子提交 Evaluation、知识状态和下一题；完成时同事务聚合能力 |
| 审计复用 | agents.AnswerEvaluationAgent.reusable；Main.reusable | 成功审计+完全相同输入/用户场次/Answer/model/provider/prompt，重新校验后复用，不补造收费记录 |
| 模型额度 | [governance.py](../../apps/api/src/interview_api/infrastructure/models/governance.py)：reserve、settle、governed_call | 用户并发/日额度、短事务预约、实际 usage 结算，未知消耗保守保留，不是服务商账单 |
| 受控结构修复 | [structured.py](../../apps/api/src/interview_api/infrastructure/models/structured.py)：generate_structured | Schema/Pydantic 校验与有限结构修复，不替业务 validator 改事实或换题源 |
| 用户确认记忆 | [proposal_service.py](../../apps/api/src/interview_api/modules/memory/proposal_service.py)：MemoryProposalService.decide；[snapshot.py](../../apps/api/src/interview_api/modules/memory/snapshot.py)：build_memory_snapshot | approve/correct/reject，确认后才可进入上下文；能力推断不写成候选人事实 |
| 报告 | [interview_report/service.py](../../apps/api/src/interview_api/modules/interview_report/service.py)：generate；controller.complete | 报告分析独立，数字来自 Evaluation；能力源是完成场次观察，不是 regenerate 次数 |
| 前端 | [CapabilityMemoryPanel.vue](../../apps/web/src/components/CapabilityMemoryPanel.vue)、[InterviewChatPage.vue](../../apps/web/src/pages/InterviewChatPage.vue) | 当前状态、版本、观察、冲突、更正；本场记忆查看冻结值，不读取新结论覆盖旧面试 |

## 3. 创建、回答、完成的真实调用顺序

```text
MockInterviewService.create
  → SessionController.create → build_context
  → CapabilityMemoryService.refresh（用户锁内读有效历史）
  → build_blueprint（近期 VERIFIED 降优先级）
  → memory.plan → MemoryGuidedPlan
  → InterviewContextSnapshot + 指纹 → 提交

start / adaptive_answer
  → planning_payload → decision_candidates
  → Main AdaptiveSelection（candidate_id/strategy/支持引用/问题）
  → resolve_selection → validate_decision
  → Controller 重算合法空间、核对版本 → 提交

answer
  → prepare_answer（幂等、持久化 Answer、lease）→ 提交
  → Evaluation（事务外）→ resolve_observations
  → Main（非终局，事务外）→ 合法组合验证
  → commit_answer 短事务
     Evaluation + 知识观察 + 题目状态 + 下一题 / COMPLETE
     → 若 COMPLETE：CapabilityMemoryService.refresh 同事务
  → 提交后才发送结果 SSE
```

模型响应成功不等于业务提交成功。Main 失败时不能把半轮当作正式 Evaluation；保留 Answer 和已成功的审计，清理 processing，原键重试可复用有效评价。审计事务与业务事务不同，这是恢复设计，不是所有数据都包在一次长事务。

## 4. 能力规则，不要背成“分数超过4就掌握”

有效 good 需要 DEMONSTRATED_IN_SESSION、可定位答案引用、无开放缺口、无争议。FOLLOW_UP/DEEP_DIVE 或 UI guidance 保守视为 assisted。独立支持按不同 Session 计数，同场多次不叠加为多场。

- 没观察 UNKNOWN；近期机制不足 GAP_SUSPECTED；部分展示、辅助后正确或仅一次独立支持 PRACTICING。
- 至少两场独立有效支持、最新表现不依赖辅助、无开放冲突才可能 VERIFIED。
- 同 scope 先正确后独立相反观察 → CONFLICTED；先错后对可进步，不能一概当矛盾。
- scope 由岗位、轮次、难度、题源、题干规范空白、rubric 指纹构成；不同 scope 记为差异，保守不误报相同条件冲突。
- 同 scope 两场后续独立 good 可解决冲突，保留 RESOLVED_BY_INDEPENDENT_RECHECK；争议本身也保留来源。
- 默认同语境30天、跨语境60天、至少3场且跨语境90天后 RECHECK_DUE。**不是两场就自动60天**，还要有多个 scope。
- 大于180天且目标优先级低于4的观察不进入规划，但不删除历史。岗位重点可保留。
- clock/policy 可注入；规则是工程启发式，不是心理测量，也没有校准后的掌握概率。

## 5. 为什么撤销不会“聚合复活”

correct 先锁 user_id，再检查资源归属/revision/待排除 ID 是否为本人有效观察。排除列表与旧集合取并集，撤销采用 `root.revoked or revoke`，之后 refresh 一直带 exclusions/revoked。输入指纹还包括规则与用户更正说明；写投影和追加版本在调用者事务内，失败整体回滚。

V2.2 Main 删除旧 recent_performance_observations，Blueprint 不用旧表现层绕回弱点；否则虽然删掉新计划，旧层仍可能把撤销内容带回。用户批准的目标/偏好另属 MemoryProposal/UserMemoryItem，撤销系统推断不等于删除用户确认偏好。

## 6. 一致性与代价

会话 `state_version + active_question_id` 和行锁防止并发提交；Answer 的 session/idempotency key 唯一约束和 question 唯一关联防重复；能力用户级事务锁防两个完成同时漏观察。模型调用不占业务行锁，最终再次验证。不能把这些说成“网络请求恰好一次”，外部结果未知仍需保守结算。

能力重新从有效 Evaluation 计算，指纹相同不追加版本；规则时效跨阈值才产生新状态。`present` GET 只按时钟重投影，不写历史。当前实现全量重算、冲突比较含二次扫描，数据规模大时有代价；尚无生产规模性能证明。

两个预算层不要混：Session 控本场题目/时间/Token；ModelAdmission 控用户并发/日额度。实际 Provider usage 决定已知结算，超时缺 usage 留保守估值；授权批次还加验收脚本总预算，不是新业务账本。

## 7. 能支撑的验收与不能支撑的说法

- 50：V2.1.0 真实 UNKNOWN_DIRECTOR_SOURCE；pair contract 修复；494项历史集成和真实浏览器 Replay；后续6请求Live。原失败输出三元组未完整保存，不编造它。
- 51：V2.2 完整 Replay、510后端/43前端历史回归；提示依赖、时间、冲突、撤销、幂等与隔离。
- 52：备份实际恢复到隔离库、0026锁演练、63项关键回归；正式库仍0025。
- 53：A/B真实请求中 Main 有记忆计划、Eval 没有；A选Redis，B选Kafka诊断，6HTTP/35,585Token/37.197s，账本一致。

不建议写进简历：完整多场 Live 训练已验收、复杂记忆算法、知识图谱、原生无限 Function Calling、准确率提升百分比、生产QPS/用户规模、完整自动爬虫均可靠。现有证据不能支持这些结论。

## 8. 本轮核验记录

工程收尾只新增文档，没有重写上述业务。当前定向测试 **28 passed / 0 failed，2 deselected**；两项主动未选为数据库测试，本轮用现有演示库只读核对替代新造数据，不能当作它们重新通过。完整510项/43项见51，是此前结果。

本轮前端43/43通过；真实浏览器读取既有场次/报告/记忆、打开更正表单后取消并刷新恢复，无console error。没有重建完整面试，不把它说成新跑完一次多场E2E。浏览器与实际数据核验详情见54；本轮零收费请求。正式0026执行步骤与授权边界见57文档。
