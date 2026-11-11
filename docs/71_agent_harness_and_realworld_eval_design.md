# Main Agent Harness 与真实来源评测：实施前审查

2026-11-09。审查基线：本地 `411c345`；`research/` 为用户提供的未跟踪资料，未修改。本文是设计，不代表已经实现。真实模型 HTTP **0**；无正式库写入、迁移、配置更改、提交、推送或系统计划任务。

P0 实施见 [题级缺口契约与回归](72_question_gap_scope_p0.md)。P1 实际实现及离线验收见 [Main Harness V1](73_main_harness_v1.md)。本文保留实施前审查和 P2 设计。

## 1. 源码审查：六个角色分别有什么自主性

| 角色 | 模型已经能决定什么 | 工具、反馈及委托边界 | 确定性控制 |
|---|---|---|---|
| Research | 选择来源片段、抽取字段及质量建议 | Search/Fetch 有真实 Gateway，但由采集/刷新 Service 调用；Research 消费已取得的 Snapshot，不自主决定搜、抓、追加检索或委托 | 管理员入口、Refresh 状态、证据校验、发布门槛 |
| Resume/Profile | 选择简历 Evidence ID、分类与技能状态建议 | Evidence 目录由程序预构建；不自主查询、补问或委托 | Profile 所有权、事实解析、版本与幂等 |
| Job Matching | 已筛候选重排、解释 | SQL 召回、资格规则、证据矩阵与复用是程序操作；无模型工具循环，不会因资料不足自主委托 Research | 匹配 Service、版本围栏、最终排序及提交 |
| Interview Main | 自适应模式可选合法目标—题源组合及 NEXT / FOLLOW_UP / REQUEST_EXAMPLE / DEEP_DIVE；受真实回答和评价影响 | 普通自适应模式无主动查资料工具循环。系统设计模式确实由模型选择 verify_idempotency 或 verify_capacity 与参数；程序固定进入选工具阶段，最多执行该答的一次模拟，结果送入 Evaluation 及后续 Main，不是可选多步检索、也不是原生 Function Calling | SessionController 控制归属、状态版本、题量、跟进、题源、提交；系统设计场景还有全场工具上限 |
| Answer Evaluation | 当前答五维评分、原文引用、gap 诊断及策略建议 | 消费已准备好的题目、答案、父问答；系统设计消费真实工具结果但不自主选工具或调用 Coach；无状态提交权限 | Schema、回答字面引用、gap/工具引用、Controller 原子提交；后续规划不是它的状态决定 |
| Summary/Coach | 引用持久化评价生成定性发现、练习建议及记忆提案 | 报告 Service 读库，Coach 不自主查询、启动下一场或批准记忆 | 评价集合指纹、确定性统计、报告版本、提案审批和能力聚合 |

源码入口：`public_career/research/agent.py::run`、`public_career/refresh_service.py`、`public_career/tools/gateway.py::invoke`；`candidate_profile/agent.py::run`；`job_matching/service.py::generate` 与 `job_matching/agent.py::call`；`interview/adaptive.py::validate_decision`、`interview/core_service.py::adaptive_answer/design_tool_evidence`；`interview/agents.py::AnswerEvaluationAgent`；`interview_report/service.py::generate` 和 `interview_report/resolver.py`。

角色串联是 Service 固定工作流，不是模型自主委托。既有 `ResearchModelCalls`、AgentRun/StepRun 保存模型调用审计；`ModelGovernance` 按 user/system owner 预留和结算；`SessionController` 另外管单场时间/Token/版本/题目状态。这些都不等于供应商账号全局免费额度。

## 2. P0：先修 gap 的作用域，不绕过旧校验

离线把 docs/69 第四个 HTTP 的原始 Prompt 输入和原始模型 JSON 交给 `ReplayModelProvider → generate_structured → resolve_observations`。结构化成功；Question 5、父题 4、目标 `ai_agent_application:rag_retrieval`，五维 5/5/4/5/5。本题回答 RRF 公式和 k，模型只声明关闭 `gap_ebf04b4e255cc571e5412c3e`，却给整个目标 `DEMONSTRATED_IN_SESSION`。仍开放：

- `gap_a304838ff6b4c044b3f363f8`：融合如何降低单路偏差或漏召回。
- `gap_e966b66c767a0f23b3615679`：相近 chunk 重复占位对排序的影响。

准确抛错点 `interview/adaptive.py::resolve_observations`：旧 gap 减去显式关闭 ID，再并入本轮新增 gap；只要剩余非空，拒绝目标级 demonstrated。失败发生于 Evaluation 业务校验，尚未发第三次 Main。保留五维及其他输出、仅把目标状态改成 partial 的离线反事实可通过，并保留上述两个 gap；这不是补丁或一次新的评分。不能直接删除拒绝规则，因为 `decision_space` 会略过已 demonstrated 的目标。

建议最小版本化修复：

1. 新 Session 冻结题级 gap 契约；Main 在合法组合之外显式声明本题聚焦的旧 gap ID。Controller/验证器只允许当前合法目标的开放 ID，按既有 Question JSON 元数据冻结，不依据题面关键词猜作用域。存在旧 gap 且以“补机制”为目的的追问不得把聚焦列表留空规避校验。
2. 评分与本题观察保持原值。关闭旧 gap 需显式 ID 和对应本答连续原文引用，禁止高分自动关闭、未知 ID 或借父答支持当前关闭。全量历史 gap 继续存在。程序只证明 ID/原文/作用域合法，技术语义仍需模型及人工复核。
3. 本题充分且只剩未考察的历史 gap：允许题级充分展示，但目标累计状态不升为 demonstrated，仍可复测。若本题聚焦 gap 尚存或仍列本题关键机制缺失却宣称充分展示：继续硬拒绝。Memory 继续消费 Controller 提交的累计状态与开放 gap；跟进/提示的独立验证条件不变。
4. 老 Session 继续旧 Schema、Prompt、Hash、重试与历史结果，不自动升级。不改既有五维阈值、Controller 状态机、Memory 聚合或 DB Schema。

P0 拟改（均在 `apps/api/src/interview_api/modules/interview/`）：`adaptive.py`（选择/评价版本、聚焦与状态推导）、`adaptive_prompts.py`（新冻结 Prompt）、`context.py`（仅新 Session 固定契约）、`context_projection.py`（题级范围和全量 gap 的不同语义）、`agents.py`（新旧模型 Schema 分派）。复用已持久化的 adaptive decision JSON；仅若新字段不能通过现有提交链保真，才最小接入 `controller.py` 的题目元数据保存，不改裁决。对应新增固定 Replay fixture 与 PostgreSQL 测试。风险是模型新字段遗漏、恢复时旧/新 Hash 混用、Repair 误删 gap 证据、过早结束或错误 VERIFIED。用原样旧失败 + 同题同答新版本范围 + 错误聚焦/虚假关闭/合法全部关闭 + 失败重试/幂等/usage/Coach/Memory 验证。固定 Replay 能证明契约，不证明 LLM 会正确判断。

**本轮未修改上述生产文件。** P0 不是单点放宽校验可以正确完成，涉及版本化产物及跨文件衔接，按 AGENTS.md 等待确认。离线定向回归 **63 passed / 34 skipped**；未配置隔离 PostgreSQL，未把集成项记为通过；docs/70 的 20 轮合法 Replay 是历史证据，不是这次修复。

## 3. P1：Main-only 有界读取工具循环

仅在新建、明确 opt-in 的自适应会话冻结 `main_harness_version`。现有系统设计工具流程、其他 Agent、旧会话不变。初版复用现有结构化输出走 **JSON Action Envelope**，不宣称 native tool calls。仅用二选一的严格 discriminated union：

```text
{"kind":"tool","name":<whitelist>,"arguments":<strict per-tool schema>}
{"kind":"final","selection":<AdaptiveSelection>}
```

每个动作最多选一个工具；不可并行，禁止未知字段、模型自带 uid/sid、路径、URL、SQL 等。默认 **2 次工具反馈 / 3 次 Main 逻辑调用**，含最多一次额外结构修复的单决策 **4 HTTP 硬上限**；日 Repair 另设最多 5 次。工具参数错误、重复、空、超时同样消耗反馈名额。到边界仅允许终局 Schema，预算或时限不足则按现有安全失败保留原答，不伪造选择、不偷偷 fallback。

| 工具 | 严格输入 | 返回边界 / 复用 |
|---|---|---|
| `read_active_knowledge_gaps` | `{}` | 本决策绑定的当前目标 ID 及其最多 8 个 gap ID/短文本，含截断提示；开场可空。读取 `planning_payload` / `knowledge_state` 的受信服务端投影，区分当前有效评价产生的待提交观察与已提交状态 |
| `inspect_job_requirement` | `{"requirement_id":正整数}` | 仅返回本场冻结 Blueprint 可引用、Snapshot 持有的岗位要求及版本/指纹，不查任意 JD |
| `inspect_current_skill` | `{}` | 当前冻结 Skill 的 ID、版本、允许的技术领域和相关面试约束；不返回系统 Prompt、历史能力明细或其他用户材料 |

统一结果：`{status: OK|EMPTY|ERROR, code, bounded_data, source_refs, content_hash}`；内容最多 2 KiB，单工具最多 2 秒，整体受 Session 剩余时长与调用预算约束。真实 Handler 通过 `MockInterviewService.read_state → SessionController.owned/state` 核对登录归属、Snapshot 与 state_version；步骤执行不持有写锁，提交前重验。对短时读取可使用请求内缓存，但同一工具与规范化参数第二次发出仅返回 DUPLICATE，不产生新的读取或延长预算。结果明确标记不可信数据，不允许作为系统指令。工具结果本身不新增合法组合或来源权限。

复用既有 Main 投影、Audit、Controller 校验和提交。基线保留完整当前投影；Harness 可仅在模型投影提供工具资源目录与必要摘要，完整服务端权威状态仍用于校验。不要把原本已经发送的全文重复取回，或以拿掉必要证据强迫调用。复用现有 payload 拼装、身份校验和 provider admission；**不直接挂接公共采集 ToolGateway**，后者的 `ToolContext/ToolInvocation` 围绕 RefreshTask，不应拿私人 Session 伪造采集归属。工具事件先以现有 AgentRun/StepRun 有界 JSON 表示（工具名、参数脱敏、来源、digest、耗时、状态），不增表、不记隐式推理链。面试动作和报告只接受经现有校验的最终产物。

P1 验收补充：如果初始 Prompt 已包含同一 Skill、JD 要求或 gap 全文，再读取不算自主性收益。每个有价值的工具动作应能指出它补足了哪项此前缺失的信息，以及真实结果如何改变下游选择。与 Harness OFF 的完整投影比较，同时检查不必要调用；必要时用紧凑但无工具的消融对照分离投影变化。

P1 拟新增 `interview/main_harness.py`（白名单、参数 Schema、读权限、循环）及对应测试；拟接入 `interview/agents.py`、`core_service.py`（仅自适应 Main 分派、按步骤预算）、`context.py` / `adaptive_prompts.py` / `context_projection.py`（冻结版本及专用投影）。复用 `adaptive.py::resolve_selection/validate_decision`；若现有 per-step 预留接口需要聚合，一并评审 `infrastructure/models/structured.py` 的限定边界，禁止新 runner 绕过准入或绕开 Main 已有 Repair。关键风险：同键重试重复花额度、过期结果写入、报价/私密上下文进入工具轨迹、工具结果诱导越权。离线回归至少包含零工具直接 final、按实际非空/空/超时反馈选择另一行动、非法名/参数/ID、跨用户、重复、注入、断线、版本冲突、配额用完。只在模型边界 Replay；工具 Handler、Controller 和隔离 DB 都真实执行。

## 4. 数据包与评测

对 6 个 JSONL 逐行 UTF-8 解析，检查重复 JSON key/ID、manifest 数量、URL 形态、层级标记、逐条引用、主题归属/次序、轮次与日计划。**225 条，结构/引用 0 错误**；数据文件聚合 SHA256 `57c6759ee6b19d5b62d8dd4c0a988105d954c9765458996bc7100d67d524e084`（按清单顺序，文件名 + NUL + 原始文件 SHA256 字节）。

| 数据 | 数量 | 结论 |
|---|---:|---|
| JD 摘要 | 12 | 11 雇主官网、1 高校转载；统一 record_type 不能覆盖 source_authority 的差异 |
| 社区记录 | 14 | 3 第一人称自述、11 汇编案例；实际 10 个来源 URL 组 |
| 报道主题 | 135 | 非逐字、无真人答复与金标；与所属报道来源/阶段一致 |
| 衍生场景 | 40 | 都有 JD/主题引用，全部 NO_HUMAN_GOLD |
| 长程计划 | 10 | 合计 200 行为提示；全部合成，允许合法提前结束 |
| 日轮换 | 14 | 日次连续、引用均可解析、仅计划未调度 |

同源组：I02/I03/I04、I09/I10、I11/I12。以上未逐条联网复证源站当前可用性、岗位开放状态或真人经历；无版权/入生产库授权推论。对所有来源文本按不可信数据处理，不能把 `testing_instruction`、题面或社区叙述拼成系统指令。开发/留出优先按 canonical URL 分组；同时保护 JD 来源分组；跨分组衍生样本隔离，不硬塞入两边。冻结拆分 manifest/hash，禁止留出反馈用于调 Prompt。

拟建独立入口 `apps/api/scripts/evals/realworld_dataset.py`（六类严格 Schema、跨文件验证、来源分级、分组切分）、`run_daily_realworld.py`（Windows 可调 CLI）、`budget.py`（全局账本），测试放 `apps/api/tests/test_realworld_eval_contract.py`。先选 5–10 条合法/非法及来源类型覆盖的案例跑 dry-run 与 Replay；不导入正式库。候选回答用预先编写的匿名合成行为或无模型的脚本策略，不能当真人或金标准。Live 若未来用模型扮候选人，也必须计入同一全局预算，默认不启用。

## 5. 日额度与可证伪的 A/B

本地实际 Main 仍指向魔搭 1017，未换模型。魔搭公告的基准为日发 200 魔粒，满足绑定条件再加 50；按 1017 页面预计 2 粒/次，125 是 **250÷2 的条件性推算**，不是账号余额、QPS、上下文或 Token 上限。未从公开文档确认该账号余额、确切重置时区/时刻或并行共享消费；不推断北京时间午夜重置，不拿本地 ModelGovernance 的 per-owner 100 次当账号上限。开始首次 Live 前人工核对控制台：剩余可用免费魔粒、单次扣减、重置窗口、失败扣减、速率/Token/上下文限制。没有可信余额/时窗或无法隔离共享消费时只 dry-run。可用保守 rolling-24h 窗口兜底，但不能用它替代魔搭余额核查。来源：[魔搭官方魔粒公告](https://www.modelscope.cn/posts/435591)、[1017 模型页](https://modelscope.cn/models/deepseek-ai/DeepSeek-V4.1-Flash-1017)。

- 同一 provider 账号作用域使用**持久共享**的评测侧预算（例如隔离本地 SQLite + 原子事务/进程互斥，不新增正式 DB 表），在所有模型 HTTP 实际发送前统一准入。只有发送前被拒可不计 HTTP；发送后超时/未知、429、Repair、重试、Coach 全计数。验证能力探针也计，禁用 fallback；不是按逻辑轮次计。
- 日 cap 取 `min(70, 已确认账号剩余安全请求数, 本地滚动窗口剩余)`；同时检查账户魔粒、总 Token、QPS 和时限。70 次按 2 粒约 140 粒，只有真的有 125 次额度才留 55 次余量；应用外调用须人工从余量扣掉。共享密钥跨其他程序时不能保证供应商侧不超，缺证据默认不发。
- 一个 20 答含 Coach 的基线约 **41 HTTP**（20 Main + 20 Eval + 1 Coach）。允许每题两次额外 Main 时上界 **81 HTTP**，还不含 Repair，**不能保证塞进 70**。首轮建议两场配对短场各 4 答（基线 9 + Harness 最多 17 正常 HTTP），全批最多 5 Repair；余量用于单独批准的能力探针和失败样本，日 cap 不等于用满。长场另审批，不自动排班。
- CLI 拟提供 `--dry-run`（默认无网络）、`--live --max-http 70 --max-repairs 5`（显式授权、锁和隔离库），输出 `summary.json`、`traces.jsonl`、`badcases.jsonl`、`daily_report.md`。记 commit/数据 hash/场景/模型/Prompt/模式、实际 HTTP/usage/延迟、工具审计、结果与失败分型；不存密钥、私人回答或隐藏推理。Windows Task Scheduler 首次手动通过后再单独启用；失败只归档，禁止自动改代码和刷成功。

对照三组口径：存档 V2.3.2 原样作为历史基线；P0 修完的版本在 **Harness OFF/ON** 同输入、同模型、同合成回答策略与请求预算配对，避免把 P0 修复收益算成工具收益。必要时小规模增加“紧凑投影但无工具”消融。预先标注哪些任务信息已足够、哪些有真实信息缺口；留出评审人员盲审追问聚焦/技术正确性/下游工具证据价值。固定 Replay 只能证明执行路径和不变量；自主性证据必须观察 Live 的**无工具时不调用、需要时选择正确工具、结果改变后策略相应变化**，而非工具数量。

报告原始分母及按来源组的配对差异：业务完成率（合法提前完成计成功；额度/失败分类单列）、工具动作合法率与必要时有效使用率、无必要调用率、盲审追问相关性、gap 矛盾率、Coach 引用有效覆盖率、每成功场 HTTP/Token 与 P50/P95 延迟。越权泄露、非法写入、未结算、错误 VERIFIED 为零容忍门槛；短样本仅报告方向与置信不足，不声称统计显著。第一批离线验收需同时证明权限/状态无退化、不同反馈确实可改变合法下一动作，以及无必要工具时原路工作；之后才申请有界 Live。
