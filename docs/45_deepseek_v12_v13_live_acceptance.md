# V1.2 / V1.3 DeepSeek 有界真实模型验收

日期：2026-10-19。本次基于用户更新后的明确调用授权，不再要求独立读取赠送余额。未购买额度、修改计费保护、Key、endpoint、模型配置、业务 Prompt、Schema 或 validator。没有 commit/push。

## 1. 范围与预算

- 模型：现有 `openai_compatible / deepseek-v4-pro-ga-260813`，禁止 fallback。七个逻辑 route 和 Legacy 默认模型均只读确认指向 DeepSeek。
- Main / Evaluation / Coach 实际 `thinking.type=disabled`，原 route timeout=180 秒保持不变；最小探测独立设置 45 秒 / max_tokens=128。
- 限制：最多 14 逻辑请求、28 实际 HTTP、300,000 Token、900 秒；一把本地文件锁限制一个验收批次，固定 attempt 文件拒绝覆盖重跑。
- 执行结果：**14 逻辑 / 14 HTTP / 76,322 Token / 115.803 秒**。14 次 HTTP 全部 200、finish_reason=stop、reasoning_tokens=0；repair=0、fallback=0、unknown usage=0、计费/权限异常=0。
- 输入 70,246 Token，输出 6,076 Token；provider 返回缓存命中合计 16,896 Token，属于输入的一部分，**不能再从总 Token 中扣掉**。没有价格配置或账单数据，不计算/猜测费用，也不声称账单必然为零。

**边界：LOCAL_FIXTURE 数据 + LIVE 模型 + 实际程序工具执行。** 不是 Live Search，不是 Replay Model，不宣称使用真实个人简历或真实公司岗位。本次没有重新运行 Research/Profile/Matching 的真实生成，它们只做路由检查。

隔离数据库：`interview_agent_v12_acceptance_live_20261019_attempt1`，head=0025。准备阶段沿用已有离线 fixture Profile/JD；正式验收 Main/Evaluation/Coach 全部使用真实 Provider，没有替换决策、固定工具顺序或筛选成功样例。正式 `interview_agent` 库未写入本次验收数据。

## 2. 最小真实请求

请求只要求 `status=ok / ready=true`，使用真实 Provider、JSON response format、现有 structured-output 校验和 V1.3 admission。2.120 秒返回，输入 211、输出 9、total 220，未修复，校验通过。

它计入本批预算。两场各两轮完整模型链通常各需 7 次，加探测共 15 次，因此执行前明确优先保证两场工具/修订/评价和一份完整报告，不突破 14 次限制。

## 3. 逐请求记录

所有行：模型均为 DeepSeek 目标 ID，HTTP=200，finish=stop，reasoning=0，repair=0。业务校验通过由后续 AgentRun/StepRun SUCCEEDED 和数据库结果交叉确认；探测使用 Probe Schema。

| # | route / 阶段 | HTTP 秒 | 输入 | 输出 | total | cache hit |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | acceptance_probe | 2.120 | 211 | 9 | 220 | 0 |
| 2 | interview_main / A 开场 | 5.358 | 5214 | 238 | 5452 | 0 |
| 3 | interview_main / A 首轮选工具 | 2.976 | 3612 | 96 | 3708 | 0 |
| 4 | answer_evaluation / A 首轮 | 18.349 | 4411 | 1063 | 5474 | 0 |
| 5 | interview_main / A 追问 | 5.121 | 7344 | 268 | 7612 | 2048 |
| 6 | interview_main / A 修订后选工具 | 2.680 | 4224 | 108 | 4332 | 2048 |
| 7 | answer_evaluation / A 修订后 | 15.191 | 5027 | 883 | 5910 | 2048 |
| 8 | interview_coach / A 报告 | 12.919 | 9629 | 832 | 10461 | 0 |
| 9 | interview_main / B 开场 | 4.334 | 5359 | 216 | 5575 | 2048 |
| 10 | interview_main / B 首轮选工具 | 3.174 | 3711 | 101 | 3812 | 0 |
| 11 | answer_evaluation / B 首轮 | 17.162 | 4510 | 979 | 5489 | 0 |
| 12 | interview_main / B 追问 | 5.289 | 7345 | 286 | 7631 | 4096 |
| 13 | interview_main / B 修订后选工具 | 3.109 | 4424 | 106 | 4530 | 2560 |
| 14 | answer_evaluation / B 修订后 | 13.723 | 5225 | 891 | 6116 | 2048 |

HTTP 时长由 httpx 请求开始到响应完整读取得到；Agent 审计还包括治理/解析开销，不能混为同一个测量：

| Agent | 真实调用 | StepRun 平均秒 | StepRun 范围秒 | HTTP 总秒 |
| --- | ---: | ---: | --- | ---: |
| InterviewMainAgent（含选工具） | 8 | 4.025 | 2.698–5.378 | 32.041 |
| AnswerEvaluationAgent | 4 | 16.126 | 13.740–18.373 | 64.425 |
| SummaryCoachAgent | 1 | 12.937 | 12.937 | 12.919 |

Main 的 8 次中，4 次为问题生成，4 次为严格 ToolDecision JSON；不把它说成原生 Chat API Function Calling。Main/Evaluation prompt=1.2.0，Coach prompt=1.0.3。

完整不含 Key、隐藏推理或原始模型响应的逐 HTTP 数值日志保留在 ignored `.runtime/acceptance/deepseek_v12_v13_live_20261019_attempt1.json`。不会删除或覆盖这个 attempt。

## 4. 场景 A：缺少业务幂等

隔离 Session **2**，User 1，Java Skill / 项目深挖面 / 标准 / 两轮。

1. 用户明确声明：没有业务唯一键，客户端超时直接重试；名额使用原子扣减。
2. **模型自主选择 verify_idempotency**，理由是未去重重试是当前最直接风险。参数合法，程序实跑后相同请求提交 2 次、接受 2 次，FAIL / DUPLICATE_RESERVATION。
3. Evaluation 精确引用 `dt_4cc80171b55752263c4ed609`、FAIL。Controller 执行 FOLLOW_UP，真实追问：

   > 你刚才提到目前没有业务请求唯一键，超时后客户端会直接重发相同请求。那如果现在要补上重试去重，你会怎么设计幂等键和唯一约束，让重复请求返回同一个预约结果？

4. 用户修订为 ATOMIC_KEY + ATOMIC_RESERVATION，同事务失败回滚、重复请求返回原结果。
5. 模型选择 **verify_capacity**，说明幂等风险已由声明修订，接着验证不同请求竞争名额；程序接受 1 次，PASS。Evaluation 引用 `dt_09a67ae49ee0d87763662f03`、PASS。
6. Controller 在有效评价持久化后完成：2 Answer、2 Evaluation、无 PENDING/ASKED 泄漏。同 key 重新调用服务只返回持久化事件，没有新增模型调用。

注意：A 修订后验证的是**容量**，没有在本批再单独重跑幂等工具；不能把这次 PASS 说成所有性质都被重新验证。

## 5. 场景 B：幂等已有、名额有竞争风险

隔离 Session **3**，与 A 引用相同共享 JD 1 / ProfileVersion 1，固定配置相同。

1. 用户声明 ATOMIC_KEY，但名额先查后写，容量 1。
2. **模型自主选择 verify_capacity**，以 2 个不同请求竞争最后名额。程序接受 2 次，FAIL / OVERSELL；不是由验收程序替模型选择工具。
3. Evaluation 引用 `dt_f157895993e4b7074965b54b`、FAIL。追问聚焦条件更新成功判断和预约插入的事务边界：

   > 你刚才提到检查和更新不是原子操作，工具模拟也确实出现了超售。那如果改成条件更新，比如 UPDATE 名额表 SET 剩余名额 = 剩余名额 - 1 WHERE 活动ID = ? AND 剩余名额 > 0，你会怎么判断这次扣减是否成功，以及它和预约记录插入要放在怎样的事务边界里？

4. 用户改为 ATOMIC_RESERVATION，模型再次选择容量工具；程序接受 1 次，PASS，Evaluation 精确引用新结果 `dt_cfe73b8565a265b03bd19cf5`。
5. Session COMPLETED，2 Answer / 2 Evaluation，无开放题；同键重试不增加 Answer/Evaluation 或模型调用。

两个场景首轮选了不同验证目标，追问分别针对重试幂等和并发名额；不是固定工具顺序，也不是仅把题库文本重写。

**B 的独立 Coach 未调用。** 两轮完成时已达到第 14 次逻辑请求；该库没有伪造/复用 A 的报告充当 B 报告。B 仍可读取数据库导出的 completion summary/评分，但不宣称已有 B 的 Coach 分析。

## 6. 报告、Evidence 与 V1.3 核对

- A 的 Report **1** = COMPLETED / current。2 个有效 Evaluation 导出的 statistics 与 Report.statistics_json 完全一致，overall_average=3.4；不读取 Coach 自编分数。
- Coach 输出 2 条 strengths、3 条 weaknesses、1 条 recurring issue、1 条 job_alignment、3 条行动建议、3 条练习计划；resolver 已验证真实 Evaluation ID。
- 6 条 PerformanceObservation 均关联该 Report/Session 且 supporting IDs 是本场评价子集。1 条 MemoryProposal 为 **PENDING**，UserMemoryItem=0，没有自动写长期记忆。
- 13 个正式 AgentRun / StepRun 全部 SUCCEEDED、目标模型正确、structured_retry=0；探测不伪装成正式 AgentRun。
- 用户模型账本 **14 行全部 SETTLED**，actual_tokens 和 charged_tokens 均合计 **76,322**，与真实 HTTP usage 一致。不是用 Session 内预算代替用户额度。
- 两场 Snapshot fingerprint 重算一致、Skill 版本固定、共享 JD ID 相同；没有重载最新 Profile/JD 来替换本场上下文。
- 在该隔离库通过原 AuthService 建立 DEMO B（User 2）；可查询同一公共 JD，但读取 A 的 Session 2/3 均为 404；这项核对没有模型调用。API/JWT/Memory/工具隔离的完整负例仍见 V1.3 既有浏览器与 PostgreSQL 测试，不为重复确定性验收再消费 Token。

## 7. 修复和测试

**没有业务故障，没有修改业务代码、Prompt、validator、Schema、timeout 或模型配置。** 只新增有界验收脚本 `apps/api/scripts/accept_deepseek_v12_v13.py` 和离线预算测试 `apps/api/tests/test_live_acceptance_budget.py`。

脚本约束覆盖：逻辑/HTTP/Token/全批次时间限制、单批锁、拒绝覆盖 attempt、费用/权限 HTTP 异常停止、usage 缺失保守计入、每次 repair 独立计数。不会通过续开新批次补齐 B 的第 15 次请求。

预算专项 5 passed（0.56 秒），无 DB/网络；随后 V1.2/V1.3/预算定向回归 **31 passed / 17.84 秒**，使用独立旧验收库和离线 Provider，无额外真实调用。新增脚本编译通过，未修改历史 migration。此前完整后端 399、前端 40/40、build 和浏览器记录仍是上一轮结果，不冒充本轮重新全量执行。

## 8. 最终判定和未验证项

**核心 V1.2/V1.3 已具备进入最终人工体验的基础**：真实 DeepSeek 能选择合法工具、使用实际结果进行差异化追问和五维评价，Controller 正确推进，至少一场完整到 Coach/Report，用户额度实际结算与共享/私有边界可核对。

但不标记为所有矩阵全部覆盖：B 独立 Coach 受逻辑上限未执行；A 修订后幂等未单独复测；只实测 Java / 标准 / 项目深挖配置；AI/银行 Skill、温和/严格的真实模型效果、真实模型的断连/timeout/repair 失败分支未在本批诱发。它们已有离线回归不等价于新的 LIVE 验收。

工具仍为声明策略下的有界单项模拟，不执行用户真实代码、不证明生产事务或所有并发交错正确。数据仍是 fixture，不能包装成真实公司/用户案例或 Live Search。

本批已经结束，不启动更多真实请求，不恢复豆包，不进入新功能开发。
