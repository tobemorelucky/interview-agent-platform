# Main-only Agent Harness V1

## 启用与业务边界

向现有创建接口 `POST /api/v1/interview/sessions` 提交自适应会话参数时，加上 `"main_harness": true`。必须是带专业 Skill 的自适应面试。创建时冻结 `MAIN_HARNESS_V1` 和 Main Prompt `2.4.0-harness.1`。省略或传 `false` 使用原有 P0 路径；旧 Snapshot 无需升级。系统设计模式沿用原有验证工具。Evaluation 仍为 2.3.3，题级 gap、Memory 和 Controller 的最终提交规则保持兼容。

## 运行与安全

1. Main 收到仍包含全部合法目标题源组合的紧凑投影，返回 `{"kind":"final","selection":{...ScopedAdaptiveSelection}}` 或 `{"kind":"tool","name":"…","arguments":{…}}`。服务不代选工具。每个动作只有一个工具，禁止额外字段。工具名或参数结构非法直接拒绝。
2. 工具通过现有 Service 读本会话，复核归属、会话版本、冻结 Blueprint 和 JD 指纹。给模型的结果由 JSON 数据字段承载，包含 `status`、`code`、数据、来源和摘要指纹，标记 `UNTRUSTED_DATA`。结果最多 2 KiB，单次读取最多 2 秒。空、拒绝、超时、重复均计入最多两次反馈。重复调用返回 `DUPLICATE_TOOL`，无需重新读取。
3. 下一步可继续选工具或提交 FINAL。第三次逻辑调用或两条反馈后只接受 FINAL。每个决策最多 3 次逻辑调用、4 个 HTTP 名额，含至多 1 次仅修改 `selection.supporting_refs` 的既有子集 Repair。禁用 Provider 回退。超预算、非法 FINAL 或无法修复的结果停在当前业务状态。
4. AgentRun 绑定 Session、Answer 和完整输入 Hash。每个模型 StepRun 持久化已解析动作和 usage，每个工具 StepRun 持久化有界结果。重试复用已提交的模型/工具步骤和同一份冻结投影，失败的模型尝试仍占名额。正常异常与取消有失败记录；作答进程硬中断沿用 Controller 的答题 lease 围栏。开场或跳题若硬中断留下无法确认是否已发送的 RUNNING 记录，超时后明确要求新建面试，当前会话不会自动重发未知请求。
5. 单次逻辑调用使用既有 Session 预留，覆盖初始请求和允许的有界 Repair 输入。用户级 ModelGovernance 在每个实际 HTTP 前独立准入和结算。已知用量入 StepRun，未知用量保留保守占用。重放正式结果不再次结算。最终候选、题源、策略、引用、难度和 `focus_gap_ids` 仍由 P0 规则和 Controller 验证。

| 工具 | 参数 | 可读取范围 |
|---|---|---|
| `read_active_knowledge_gaps` | `{}` | 当前题所绑定目标的最多 8 个开放 gap ID 和短文本，包括本轮已通过校验、尚待提交的 Evaluation 投影；开场可为空 |
| `inspect_job_requirement` | `{"requirement_id": 正整数}` | 仅 Blueprint 允许且匹配 Snapshot 指纹的岗位要求，附冻结 JD 版本；不查询实时更新的 JD |
| `inspect_current_skill` | `{}` | 本场 Skill ID、版本、指纹、技术主题和有界专业规则；不读 Prompt 文件或任意路径 |

## 按需投影

OFF 保留原投影。ON 首次仅列 JD 要求 ID、Skill 身份、轮次与风格、目标状态和开放 gap 数。开放 gap 文本和 ID、详细 Skill、Requirement 正文按需读取。最新 Evaluation 仅留分数和覆盖摘要，近期题目保留题面，旧题保留身份和状态。MemoryGuidedPlan 保留相关状态、验证意图、优先级、版本与指纹。合法组合、目标、题源、允许策略和引用不受压缩。候选人原始回答、当前题及 rubric、仍需直接使用的候选人/公共题源证据保留。服务端 Snapshot、完整 P0 状态与原评价不改写。

## 离线对照与限制

使用 `research/realworld_eval/v1` 的 S02-complete（J03、I03-T06）和 S04-partial（J04、I02-T04）做三组配对。JD 是官方来源摘要，社区汇编是报道主题，试验用要求变体和回答为衍生数据。OFF/ON 每对使用相同 Blueprint、合法候选、问题和回答。两组 S04 使用不同的衍生 Requirement 重点，ON 第一次没有要求正文，读回后才有足够信息据此选择。

- S02 两边均直接选择 RAG 深挖，ON 无工具调用。
- S04 的 Tool Calling 变体，ON 读取一次冻结 JD 要求后切到 Agent 参数校验，OFF 从已提供正文得到同样选择。
- S04 的 RAG 变体，ON 读取一次后转为 RAG 结果去重的追问，OFF 同样选择 RAG。三个配对的合法候选目录相同。

| 配对场景 | 提交 / 两臂 | 模型调用 OFF→ON | 首个 Main 消息字节 OFF→ON | 本答全部消息字节 OFF→ON | Replay 用量 OFF→ON | 本地耗时 OFF→ON |
|---|---:|---:|---:|---:|---:|---:|
| S02 信息充分 | 2/2 | 2→2 | 18,730→17,735 | 29,029→28,034 | 200→200 | 182→169 ms |
| S04 Tool Calling 侧重 | 2/2 | 2→3 | 18,697→17,700 | 28,963→46,261 | 200→300 | 197→216 ms |
| S04 RAG 侧重 | 2/2 | 2→3 | 18,695→17,700 | 28,959→46,257 | 200→300 | 176→205 ms |

统计包含本答 Evaluation 和后续 Main，排除开场。两次应读取的工具均合法且获得了首次输入没有的正文。消息字节包括 System 和 Schema，Replay 用量为每次固定 100，耗时含本地服务与 PostgreSQL。首个 Main 小约 5%，调用工具的样本该答总消息增约 60%。这些数字不能换算为供应商 Token 或 HTTP 延迟。

Replay 验证决策可依赖真实工具反馈及被服务端接受的条件。追问相关性只对固定测试题作示例核对；模型是否会自主选对工具、是否避开不必要调用、实际 Token 和延迟均待 Live 验证。

## 测试记录

- 指定隔离数据库 `interview_agent_v232_reliability_20261029_a`，预检真实库名和 Alembic `0026`。进程级审计钩子阻断非本机 DNS/连接；pytest 的模型边界使用 Replay 或 httpx MockTransport。未改正式库或运行迁移。
- 15 个相关测试文件 **242 passed、0 failed、0 skipped**，覆盖 P0、旧自适应版本、V1.2 系统设计、治理、普通面试、训练和能力记忆。
- 最终聚焦重跑 Harness 和上下文投影 **38 passed、0 failed、0 skipped**，含并发开场的单 Run 验证。两组结果有重叠，不能相加。
- OFF Session **818** → Coach Report **62** → 下一场 **819**。ON Session **821** → Coach Report **63** → 下一场 **822**。两场各提交 20 个评价、8 个 FOLLOW_UP，自然结束，报告引用正式评价。ON 八次追问读取真实服务端 gap 再冻结焦点；下一场均有 MemoryGuidedPlan。
- 四次 Mock HTTP 路径按调用分别完成 ModelGovernance 预留和结算，其中含一次原引用子集 Repair。尝试改写题面时被拒，已知用量仍入账。503 没有回退，未知用量保守结算。模型步失败后同键恢复复用 Evaluation 和已持久化工具结果，无重复有效提交。

## 文件

- `interview/main_harness.py`：Action Schema、冻结读取、循环、持久化与恢复。
- `interview/schemas.py`、`context.py`、`adaptive_prompts.py`、`context_projection.py`、`core_service.py`：创建开关、冻结契约、Main 投影与接入。
- `infrastructure/models/structured.py`、`public_career/research/audit.py`：仅 Harness 请求启用的结构修复及 StepRun 扩展；其他调用保留默认行为。
- `interview/training.py`：审计摘要按 Main Run 展示，避免工具和模型多步造成重复条目。
- `tests/test_main_harness.py`、`tests/test_gap_scope_contract.py`：隔离 PostgreSQL 契约、来源对照、治理及两套 20 轮 Replay。

没有数据库迁移或模型配置变更。真实模型 HTTP 为 0。P2 账号级免费额度、每日计划任务与评测自动执行不在本次范围。
