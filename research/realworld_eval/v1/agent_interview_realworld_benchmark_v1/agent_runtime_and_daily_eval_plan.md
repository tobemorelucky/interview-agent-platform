# V3 规划建议：从六 Agent 的“顺序调用”到受控自主执行，以及基于真实岗位的评测

## 已知代码现状（按 GitHub 当前可见版本和历史回归记录核对；本地未提交文件可能不同）

- 六角色已存在，不能简单宣称“没有自主性”：Main 已经自主选择合法 `candidate_id`、`FOLLOW_UP/DEEP_DIVE/NEXT`，Controller 掌管状态。
- `interview/adaptive.py` 已有 `AdaptiveSelection`、合法目标与来源组合校验，`context_projection.py` 已有 Main/Evaluation 分角色输入；`controller.py` 是单一业务写入者。
- Evaluation 仍有 `UNRESOLVED_KNOWLEDGE_GAP`（回答部分解决，但目标整体状态过宽）确定性真实失败；这项必须优先修。
- 20 轮离线 Replay 完成 Coach/Memory，真实 20 轮未通过。正式库 schema 0025，较新能力在隔离 0026；不能自动迁移。
- 用户已配置每天最多 125 次的免费 API 方案：**这里不能推定 125 次就是 125 个模型逻辑步骤、HTTP 或不计 Token 的无限额度**。先实测服务商实际限制、统一计数和重置时区。

## 架构决策

**采用约束式 Agent Harness（自主执行环境），而不是将业务 Controller 改为大模型决定一切。**

固定的只有业务层必然的事实依赖：读取资料/用户授权 → 问题已提交 → 用户答案 → 正式评价 → 完成记录 → Coach → Memory。真正交给模型自主的是：选择下一步需要什么资料、是否用工具、多次查询还是直接作答、如何选择目标、什么时候停止检索、要追问什么。最终持久化仍由 Controller 校验。

```text
User task/goal
   ↓
TaskRuntime (非第七个Agent；权限/预算/运行状态/事件轨迹/停止条件)
   ├── existing ResearchAgent [search, fetch, inspect_source, request_review]
   ├── existing ProfileAgent  [read_resume_evidence, ask_user_clarification]
   ├── existing MatchingAgent [query_published_jobs, compare_requirements, inspect_profile_evidence]
   ├── existing MainAgent    [read_job_scope, read_skill, read_knowledge_gaps, inspect_prior_turn]
   ├── existing Evaluation   [read_current_rubric, read_answer_span, inspect_gap_evidence]
   └── existing Coach        [read_valid_evaluations, read_learning_progress, draft_next_plan]
   ↓ typed final artifact
Existing schema/Controller/permissions/frozen Snapshot/idem/usage/Memory aggregator
```

TaskRuntime 不自行调用模型规划跨域的大动作。每个既有 Agent 在自己领域内最多进行 2~3 次工具反馈循环：

`LLM chooses tool or final → tool gateway validates → tool returns bounded evidence → LLM chooses next → final typed artifact → domain validator → Controller commit`。

工具执行权限是**授予域内只读能力**而不是开放 shell、SQL、文件系统或通用网页请求。外部搜索结果、JD 和简历始终是数据，不是指令。内建工具参数 schema、关联用户与 Session、只读事务、超时、结果长度上限、日志脱敏、调用次数预算、同键幂等与重试分类。

### 五项 Agent 自主性细节

1. **动态工具选择**：Main 可选择是否读取当前目标的 gap、只读取指定 JD 要求、查当前 Skill 评分目标，或直接生成问题；绝非每轮无条件访问全部工具。
2. **小步规划 + 观测后重规划**：在一次决策内先确定当前信息缺口，按工具结果更新计划；不需要要求模型输出隐藏推理链。保留可审计的显式行动决策及状态。
3. **跨 Agent Handoff 是明确委托，不是自发写库**：Matching 发现 JD 缺失可提出 Research 调研请求，需审核/用户确认后调度；Coach 可给出下场专项建议而非自行替用户创建并修改状态。
4. **按任务失败降级**：工具超时可以选择别的已批准证据或放弃该行动，禁止捏造工具结果；有副作用工具必须显式用户批准，当前第一版不开放。
5. **自治停止**：足够证据、连续两次工具无增益、达到轮数/时限/预算、无法合法授权时结束或请求用户澄清，不做无界反思循环。

### 分阶段实现（不一次性重构所有 Agent）

**P0 先修业务冲突**：解决 `UNRESOLVED_KNOWLEDGE_GAP`。将本题评分、具体 resolved_gap_ids、目标级状态三者分离；历史 gap 不可凭单答全部清除，旧冻结版本不受影响。原始失败样本固定 Replay + 隔离 PostgreSQL 完成回归。

**P1 Tool Harness 最小切片**：先在现有 Main 实现一个有界工具循环，三类只读工具（read_active_goal_gaps, inspect_job_requirement, inspect_current_skill），并保留原终局 `AdaptiveSelection` 与合法组合验证。支持模型原生 tool calling；若当前免费 API 不支持该模式，可使用严格 JSON Action Envelope（两者进同一 dispatcher），不得伪装实际 native tool call。工具请求与最终选择都需可追溯。先做 A/B 对照，不全量替换。

**P2 面向业务增量**：Research、Matching、Profile、Coach 再分别加入小范围工具选择；Evaluation 优先保持独立单次评分与证据校验，必要时允许只读查依据，不给历史能力标签，避免循环自我确认。

**P3 用户产品能力**：用户直接粘贴 JD 选目标，复用现有 Skill 与 Profile；发布面经与来源许可单独审批；结果展示真实工具轨迹（对用户只展示行动概述，不暴露系统 Prompt/私人证据）和失败恢复。

## 逐层评测：结果 > 轨迹 > 文本观感

| 层 | 输入 | 自动检查 | 不可替代的人工检查 |
|---|---|---|---|
| 业务不变量 | 已知原始失败、合法/非法引用 | Controller、数据库最终状态、幂等、权限与使用量 | 无法仅靠Replay宣称评分合理 |
| 工具选择 | JD目标+当前资料状态+工具集 | tool_name合法、参数schema、权限、无效/重复调用、链式依赖 | 有多个合理路径时不要求轨迹逐步完全一致 |
| 面试策略 | 真实报道主题+特定答题表现 | 随回答变化、追问聚焦、合法目标题源、知识 gap 保留 | 问题自然度、价值、技术事实准确性 |
| 长程任务 | 每5/10/20轮 checkpoint | Controller合法 COMPLETE、Coach来源、下一场冻结Memory | 报告建议是否对用户有用 |
| 稳定性和成本 | 多次固定情景真实模型运行 | 请求成功率、Token、延迟、repair率、quota超额率 | 模型切换是否显著影响质量 |

建议核心指标：`task_success_rate`(终局满足业务目标的任务/总任务)，`hard_violation_rate`(泄露/非法写入/混租)，`tool_decision_valid_rate`，`tool_usefulness_rate`(是否帮助下游决策，有人工分歧记录)，`gap_contradiction_rate`，`interview_followup_relevance`（人工复核），`coach_evidence_coverage`（服务端引用有效数/需要引用数），`tokens_per_completed_interview`，`p50/p95_latency`，`retry/repair_fraction`。不使用单一自评LLM总分代替业务成功率。

**原始资料并不提供人类候选人的原始回答或真实五维得分**：以上 `sourced_interview_topic_units` 只能用于主题和追问相关性评测；任何答案正确性参考都必须由独立专家/资料审查、具有明确知识依据的 rubric 或确定性可测条件补上。无金标时报告 `UNREVIEWED`，不能声称精确评分准确率。

## 每日真实验收（日免费 125 次模型 API 的前提下）

**预检查**：provider capability probe 仅做有限调用，先核实 JSON、native tools/JSON-envelope、Usage、限额重置、超时；全部计入请求配额。读取 provider 显示的剩余额度（若提供）；若 API 总额度被其他程序共享，无法强保证不会超标，因此使用更保守的本地 cap。

**推荐上限**：一次 daily run 默认最多 70 HTTP 模型请求、5 次 Repair 上限、最多 2 场短面试、最多 1 次 Coach，留出至少 55 次自由额度给用户其他调用。即使 70 尚未用完，若供应商余量更低也立即停；模型调用失败也要计数，不能靠重试刷满。

| 频率 | 运行 | 约用 HTTP（规划，不代表真实） | 判定重点 |
|---|---|---:|---|
| 每次提交 | 现有离线 20 轮 Replay + contract | 0 | 业务契约不能退化 |
| 每日 | 2~3个真实 JD / 社区来源主题的短场 Eval、工具压力案例轮换 | 20~50 | 动态选题、合法工具、评分与gap、失败保护 |
| 每日 | 最少1条带工具失败/提示/不完整回答的定向case | 3~12 | 真正适应非理想输入 |
| 每周可选 | 单场较长或20轮 Live（需当日剩余预算足够） | 约41+额外工具步骤 | Coach、Memory跨场闭环 |

一场含20问答在旧设计基础下约20次Main + 20次Eval + 1次Coach=41次模型HTTP；加入多步模型工具循环后可能大于60，必须以实际 HTTP 账本而非逻辑轮次预留额度。不能承诺125次一定够；Provider可能还有限制QPS/上下文/日Token。

Daily runner CLI 建议：

`python scripts/evals/run_daily_realworld.py --dry-run --dataset research/realworld_eval/v1`

`python scripts/evals/run_daily_realworld.py --live --max-http 70 --max-repairs 5 --suite daily_rotating`

上面是**设计中的接口**，当前仓库中不保证已经存在，必须由 Codex 实现后才能使用。首次人工执行通过后，再让用户明确启用 Windows Task Scheduler 等定时任务；自动运行不意味着自动改代码、自动提交或自动迁移。

### 每日输出目录（建议）

`research/eval_runs/YYYY-MM-DD/summary.json`（版本、模型、场景ID、总HTTP、真实usage、status、失败码）

`traces.jsonl`（model action、工具ID/参数脱敏、tool_result digest、业务状态变迁、拒绝原因）

`badcases.jsonl`（可重放输入指纹+来源链接+错误分型：model/tool/context/contract/provider/infra）

`daily_report.md`（通过/失败、对比昨天/最近7天，模型与Prompt指纹冻结；计量口径明确）

敏感数据路径和真实用户简历不得写到 git 仓库；测试应在隔离数据库或最小匿名模拟用户上进行。任何每日失败都只报告，不自动修改业务代码并继续测试以掩盖问题。

### 数据集拆分和避免评测污染

- `A`: 官方JD标签与考察点，不是面试原始题。
- `B/C`: 社区报道与编排题，来源逐条标注。不把公司名称当权威证明。
- `D`: 原创行为模拟/压力场景，须标注DERIVED。
- 以 `interview_case_id` 或源 URL 为单位做开发/留出切分；主题多样、岗位多样、同源记录不能跨集合。
- 开发集用于Prompt优化；留出集每日轮换检查，但不把它的全部问题内容再次放到开发Prompt里。
- 评价最终结果的业务断言冻结，不能直接把 Agent 的主观结果当自己的金标。

## 进入下一阶段的客观条件

- 原始 `UNRESOLVED_KNOWLEDGE_GAP` 失败已修且复现后通过，非法状态仍硬拒绝；
- 新Provider结构化/工具调用路径及会话Usage/请求配额经过真实冒烟；
- 首个Main工具循环通过至少5个工具故障/权限/无必要工具边界用例；
- 至少一场真实短面试完成Coach+Memory，并保留真实HTTP轨迹；
- daily runner连续多日不超额、不污染正式库，且可人工审查Badcases；
- 不夸大：不能把这些条件当作已完成。
