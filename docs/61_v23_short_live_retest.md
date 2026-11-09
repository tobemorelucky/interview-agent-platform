# V2.3 Structured Output 修复后短程 DeepSeek Live 复测

## 1. 范围与结论

2026-10-26，唯一一次短程批次通过：**3 个真实 Answer、3 个正式 Evaluation、Main 合法提交第四题**。没有完成第四答、Coach、Memory 或完整长面试，不宣称 V2.3 完整 Live 闭环通过。

- 模型：`deepseek-v4-pro-ga-260813`，当前 Ark OpenAI-compatible endpoint；Main/Evaluation 均 `thinking=disabled`，未改模型配置。
- 输入：明确 DEMO 的人工构造简历和 JD；Profile 准备使用明确 Replay，**本场全部 Main/Evaluation 为真实 Live**，不是实际求职者或真实公司岗位。
- 新库：`interview_agent_v21_v23_live_short_20261026_a`，Alembic `0026`；user/profile/job/session 均 ID 1。
- 授权硬上限：7 逻辑 / 9 HTTP / 120,000 Token / 600 秒。实际 **7 / 7 / 78,861 Token**。
- 最后模型响应时批次耗时 **207.563 秒**（含浏览器登录、页面操作及思考作答）；七个 HTTP 耗时合计 **50.796 秒**。没有并行批次、fallback、额外规划或重复筛选成功样本。
- HTTP 全部 200，`finish_reason=stop`，reasoning_tokens 全部 0；首两次缓存命中 0，后五次各 2,048（包含在 input tokens 中，不重复相加）。
- 验收后已停止本轮专用 API/Web；保留数据库和忽略的审计材料。未 commit/push。

## 2. 实际进程加载的契约

通过本批次新启动的 Python 进程、冻结 Snapshot、实际 HTTP system 内容及持久化 StepRun 交叉核对：Main Prompt **2.2.1**，Evaluation Prompt **2.1.0**，不是历史失败的 Main 2.2.0。

Main 冻结 Prompt fingerprint：`fa105bb9f8a79d413e1fb444f6490812a9c6e82eaab515dfc0b40a100a52a186`。

运行代码的 `AdaptiveSelection.supporting_refs` 上限仍为 12；Compatible Provider 使用 `json_object`，Schema 随 user 消息传入并由本地 Pydantic 与业务校验实际执行，不能将其描述为服务商强制 JSON Schema。四个实际 Main 输出都通过原校验及 Controller，不曾修改输出或裁剪数组。

本轮仅调整验收脚本 `accept_v23_live.py --short` 的硬预算档位，并新增离线预算检查和只读分析脚本；没有修改业务代码、上下文逻辑、`.env`、模型路由或正式库 migration。

## 3. 三答与实际策略

Codex 仅查看当时页面问题后作答，未提前打开当前题的参考答案、评分标准或 expected_points。

| 题 | Main 实际问题 | 模拟求职者的回答要点 | 后续真实决策 |
| --- | --- | --- | --- |
| Q1 | 同时使用 BM25 和向量检索时如何合并最终排序，相比只用向量解决什么问题？ | 按 chunk ID、RRF `1/(k+rank)` 求和，避免原始分数量纲混用；关键词与语义互补，承认未做完整标注/生产验证 | FOLLOW_UP：追问 RRF 常数 k 的选择及大小影响 |
| Q2 | 常数 k 如何选？很小或很大分别怎样影响排序？ | 以60为实验起点，区分平滑常数与召回条数；解释头部权重和平滑效应；计划固定查询集对照、MRR/nDCG及引用质量，不虚称已经完成实验 | FOLLOW_UP：追问两路 top-k、重复 chunk 的排名与分数处理 |
| Q3 | 两路各召回多少条？同一个 chunk 两路出现如何处理？ | 本地起点每路20条，单路内保留最佳排名，跨路保留各排名并累加，最终5条、稳定ID打破并列；计划对照10/20/50，不宣称最优 | NEXT：切到 Agent 工具 Schema、白名单和非法请求处理 |

第四题实际文本：

> 在你的训练助手项目里，Agent 调用工具时，你会如何设计工具参数的 Schema 校验和工具白名单？如果模型请求调用一个不在白名单里的工具，或者参数校验失败，系统应该怎么处理？

前两次观察为 `PARTIALLY_DEMONSTRATED`；第三次为 `DEMONSTRATED_IN_SESSION`，明确引用本答原文、解决先前 gap IDs，当前 RAG open_gaps 为空。它仅表示**本场题目表现**，不等于跨场次独立验证或用户确认事实。

## 4. supporting_refs、组合与来源

| Main | 策略 | knowledge_id 后缀 | candidate_id | supporting_refs |
| --- | --- | --- | --- | --- |
| 开场 | NEXT | rag_retrieval | C_98a08c112239e1a8ee5d8ea2 | `requirement:2`, `skill:ai_agent_application@1.0.0`（2条） |
| 第一答后 | FOLLOW_UP | rag_retrieval | C_6521e4d3e28ce5a79fa409e9 | `skill:ai_agent_application@1.0.0`, `answer:1`, `evaluation_run:3`（3条） |
| 第二答后 | FOLLOW_UP | rag_retrieval | C_6521e4d3e28ce5a79fa409e9 | `answer:2`, `evaluation_run:5`, `skill:ai_agent_application@1.0.0`（3条） |
| 第三答后 | NEXT | agent | C_331d91468123a99154379af5 | `requirement:3`, `fact:6`, `evaluation_run:7`（3条） |

knowledge_id 前缀均为 `ai_agent_application:`。每次实际选择均在当时合法 candidates 中，包含所选题源，后续决策包含本轮 Answer 或 Evaluation 引用。通用 Skill 来源明确是 `GENERIC_SKILL_NOT_COMPANY_EVIDENCE`，没有伪装成公开面经。第四题对应 JD 工具 Schema/权限/状态要求及项目事实，非随机换题。

Controller 全部接受并持久化对应问题；未出现 UNKNOWN_DIRECTOR_SOURCE、未知引用、组合错配或安全回退。**Repair 次数为0**：因此本轮验证了 Live 正常引用选择，但没有自然触发“17条→合法子集”的真实 Repair；该路径仍依据 docs/60 的固定失败 Replay 测试，不冒称 Live Repair 实测。

## 5. 逐请求 Token、Prompt 与耗时

输入长度是实际 system+user 消息字符数，不是 token；含传入 Schema。累计为此 Session 已发生的所有真实 HTTP usage。

| HTTP | Agent / Prompt | input chars | input Token | output Token | 秒 | 累计 Token |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | Main / 2.2.1 | 23,971 | 8,889 | 221 | 5.062 | 9,110 |
| 2 | Evaluation / 2.1.0 | 28,254 | 10,913 | 640 | 10.500 | 20,663 |
| 3 | Main / 2.2.1 | 28,345 | 10,961 | 256 | 4.749 | 31,880 |
| 4 | Evaluation / 2.1.0 | 30,512 | 12,051 | 636 | 9.435 | 44,567 |
| 5 | Main / 2.2.1 | 29,820 | 11,805 | 267 | 5.583 | 56,639 |
| 6 | Evaluation / 2.1.0 | 26,719 | 10,666 | 617 | 9.927 | 67,922 |
| 7 | Main / 2.2.1 | 26,921 | 10,657 | 282 | 5.540 | 78,861 |

Main 合计 input42,312 / output1,026 / total43,338，平均 **5.234秒**。Evaluation input33,630 / output1,893 / total35,523，平均 **9.954秒**。总 input75,942 / output2,919，占主要成本的是输入，不是 reasoning。没有人民币实账读取能力，不以赠送额度推断账单金额为零。

## 6. 上下文增长与200,000上限

只读核对以下真实构造路径：

- `context.model_context` 去掉 profile/normalized 重复表达和 CandidateFact.statement；保留 ID/真实证据，不重复传原始整份简历。
- `adaptive.planning_payload`：history 是已问题目的短投影（题目、状态、知识点），不是每轮全部回答和评价；当前 answer/evaluation 与累积知识观察另行传入。
- `InterviewAgent.call` 去掉完整 Blueprint 和重复 Prompt 正文，用 fingerprint 固定；Main 只保留合法 pairs，移除 choices。
- `skill_engine.agent_payload`：Main 有 memory plan 时移除 recent_performance；Evaluation 移除 memory plan、approved memory 和 recent performance，避免历史推断污染评分。

| Main 请求 | history题数 / 紧凑chars | Snapshot紧凑chars | adaptive紧凑chars | candidates |
| --- | ---: | ---: | ---: | ---: |
| 1 | 0 / 2 | 6,921 | 10,666 | 28 |
| 3 | 1 / 182 | 6,921 | 13,036 | 28 |
| 5 | 2 / 354 | 6,921 | 14,259 | 28 |
| 7 | 3 / 557 | 6,921 | 11,321 | 19 |

Main memory plan 仅201紧凑chars，当前为空计划/规则元数据，并没有多场历史；不能由此评价丰富记忆下的输入成本。Schema紧凑chars：Main1,517、Evaluation3,216；system分别3,428/1,959。数字按JSON紧凑序列化计算，不能直接与实际带空格消息长度相加。

**仍有冗余候选**：Evaluation 收到 choices 与 candidates 两种同源投影（前两次4/28，第三次3/19），以及超出当前问题的候选空间。尚未证明所有字段都需要；本轮仅记录，不修改压缩逻辑。固定Snapshot每轮仍会重传，知识观察也随回答增长，当前不是最小上下文。

本自适应路径没有每若干轮执行摘要模型或截断历史的压缩过程；实际启用的是确定性 DTO 投影/过滤。`adaptive.bounded` 是96,000字节硬拒绝，不是有损摘要。三轮不单调增长，不能由此保证20轮安全。

`SessionController.state/reserve_model_call/choose_action`：`max_model_tokens=200000` 是**整场 Main/Evaluation 累计 token 用量**（含修复及失败已上报usage），另加未知消耗保守记账与在途预留；不是每轮额度，也不是模型上下文窗口。预留检查可能在实际用量到200,000之前就拒绝新调用。时间仍7200秒、每主问题最多2次追问；均未提高。

当前已用78,861（39.43%），剩余名义121,139，实际仍受下一请求保守预留约束。按本次样本简单外推，15/20个有效回答的 Main+Evaluation 可能约35万/46万，**未含Coach、额外失败与长程增长，不能当作准确预算预测**。因此短程稳定性通过，但**现有200,000会话上限下不具备直接开始15–20轮的预算条件**。须另行审查成本/会话约束并单独授权，不得用批次预算绕过会话预算。

## 7. PostgreSQL、幂等及数据保护

- Session1 `IN_PROGRESS`，state_version=7，turn_count=3，active_question_id=4。
- Question4条：前三COMPLETED，第四ASKED；Answer3条均COMPLETED；正式Evaluation3条，一答一评；没有重复有效记录。
- InterviewAgentCall7条，对应7个真实AgentRun/StepRun均SUCCEEDED；另有准备阶段Profile Replay记录，不能算真实调用。
- 7条Live配额记录全部SETTLED，actual_tokens合计78,861，与HTTPusage及StepRun一致；无残留模型预留、未知usage或answer_processing lease。
- 浏览器第二答完成后刷新成功恢复第三题。三答后用第一答原question/content/idempotency_key请求，HTTP200返回原SSE结果；模型仍7/7，Session/Answer原样，未重复评价或创建题目。
- 正式库 before/after只读指纹文件完全一致，仍0025/64表。历史失败库 Session/Answer前后完全一致，仍3题、2正式评价、第三答FAILED；原80321Token记录保留。
- 没有触发本场失败，故失败后Evaluation复用的新增Live故障实验**未执行**；沿用已完成的PostgreSQL/Replay证据，不能说本轮重新实测该失败路径。

## 8. 材料、检查和停止点

忽略的本地证据（不含API Key，不保存hidden reasoning）：

- `.runtime/acceptance/v23_short_live_20261026_a.json`：每次实际消息、业务JSON、usage、HTTP计数。
- `.runtime/acceptance/v23_short_final.json` 与 `v23_short_after_retry.json`：数据库只读检查点、冻结Snapshot、配额。
- `.runtime/acceptance/v23_short_20261026_before.json` / `after.json`：正式库指纹。
- `.runtime/acceptance/v23_short_old_before.json` / `after.json`：历史失败状态保护。
- `output/playwright/v23-short-live-q4.png`：真实页面第四题，已目视核对。

本轮实际新增检查：离线预算四边界自检通过（零HTTP）、脚本compile、真实浏览器/后端/隔离PostgreSQL三答流程、刷新、同键幂等、七次真实模型/配额核对、`git diff --check`。之前524 passed不是本轮重跑结果；本轮没有再跑全量业务测试，也没有修改业务代码。

本轮脚本变更：`accept_v23_live.py` 增加 `--short`；新增 `check_v23_short_budget.py`、`analyze_v23_short_live.py`。正式库无新结构/API变更。下一步等待用户决定长程成本与授权；本轮停止，不执行更多收费调用。
