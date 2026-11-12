# CODEBUDDY.md — WorkBuddy 独立项目记忆

> 归属：WorkBuddy 独立维护。Codex 的规范在 `AGENTS.md`，Claude Code 的在 `CLAUDE.md` / `.claude/`。
> 本文件**不复述**上述文件，只记录 WorkBuddy 需要带走的上下文与已核实结论。
>
> **硬规则**
> 1. 不写 API Key、Cookie、认证 Header、真实简历或任何个人隐私。
> 2. `AGENTS.md`、`CLAUDE.md`、`.claude/`、`docs/00–80` 对 WorkBuddy 只读。
> 3. 每条结论必须能标注为 `事实`（有代码/命令/测试证据）、`推断`（未验证）或 `待办`。禁止把推断写成事实。
> 4. 历史 Live 验收库与 `.runtime/acceptance/**` 是封存现场：只读，不改写、不迁移、不"修复性重跑"。

---

## 1. 已确认的基本架构（事实）

- 运行主体：`apps/api`（FastAPI，`src/interview_api` 布局）、`apps/web`（Vue3 + TS + Vite + Pinia）、`apps/worker`（Celery，仅 `kb_tasks` / `resume_tasks`）。
- 存储：PostgreSQL（业务 + 工作流状态）、Redis、MinIO、Milvus（仅已发布知识的向量）。
- 六个 Agent 均为「结构化产物 + 程序裁决」，不存在自由链式委托：
  - Research：`public_career/research/agent.py`（消费已取得快照，Search/Fetch 由 Service 调用）
  - Resume/Profile：`candidate_profile/agent.py`
  - Job Matching：`job_matching/agent.py`
  - Interview Main：`interview/agents.py::InterviewMainAgent`
  - Answer Evaluation：`interview/agents.py::AnswerEvaluationAgent`
  - Summary/Coach：`interview_report/agent.py`
- 能力记忆是**服务**（`memory/capability_service.py`，advisory lock + 版本化投影），不是第七个 Agent。
- 面试核心分层：
  - `interview/core_service.py`（`MockInterviewService`）：读状态 → 调模型 → 短事务提交 → 发事件；模型调用永不在 Controller 事务内。
  - `interview/controller.py`（`SessionController`）：唯一业务状态写入者；`owned()` 校验归属 + `state_version` + `workflow_mode=="m4"`。
  - `interview/adaptive.py`：合法目标/题源组合、知识观察与 gap 作用域（纯函数，无模型 I/O）。
  - `interview/main_harness.py`：Main-only 有界工具循环。
  - `interview/context_projection.py`：角色化 DTO 投影（`ROLE_CONTEXT_V1`），只用于模型线格式，从不用于提交状态。
  - `interview/topics.py`：确定性主题节奏（正则词典），不是资格判定。
- 模型路由：`infrastructure/models/router.py`，7 条 route；`ModelGovernance`（`infrastructure/models/governance.py`）在每个真实 HTTP 前做用户级准入与结算。
- 前端不暴露 `main_harness` 开关（仅 API 参数），也不展示 `completion_reason`。

## 2. 当前冻结契约与版本（事实）

| 名称 | 值 | 位置 |
|---|---|---|
| Main Harness | `MAIN_HARNESS_V1`（工具 2 / 逻辑调用 3 / HTTP 4，工具 2s、结果 ≤2KiB） | `interview/main_harness.py` |
| 题级 gap 作用域 | `QUESTION_GAP_SCOPE_V1` | `interview/adaptive.py` |
| 决策契约 | `TARGET_SOURCE_PAIR_V1` | `interview/adaptive.py` |
| 角色上下文投影 | `ROLE_CONTEXT_V1` | `interview/context_projection.py` |
| 评分语境 | `FOLLOWUP_SCORING_V1` | `interview/context_projection.py` |
| 自适应版本 | `2.1.0`（决策/知识状态） | `interview/adaptive.py` |
| Prompt 版本 | Main `2.4.0-harness.*`（Harness ON）/ `2.3.3`（OFF）、Evaluation `2.3.3` | `interview/adaptive_prompts.py` |
| 主题策略 | `technical-topics@1.0.0` | `interview/topics.py` |
| Alembic head | `0027_user_job_targets`（共 27 个版本） | `apps/api/alembic/versions/` |
| 单场预算默认 | `max_model_tokens=200000`、`max_rounds=40`、`max_duration=7200s`、默认 `question_count=20` | `core/config.py` / `interview/schemas.py` |

## 3. 开发阶段（事实）

- 阶段：Codex 侧完成 Phase 4–8 的自适应面试、能力记忆、训练中心、Main Harness V1 与长场可靠性修复；HEAD `62333df` 一次性提交 docs/71–80。
- 最近一轮真实 Live（docs/80，批次 `p1_thinking_long_20261110`）：45 HTTP / 456,618 Token；长场 18 题 / 18 答 / **17 条正式 Evaluation**；第 18 答在"下一题预留 81,129、已用 399,420、冻结上限 460,000"处被拒，Session 停在 `IN_PROGRESS`、`state_version=38`、Answer 18 `FAILED`。
- 该现场已封存于隔离库 `interview_agent_thinking_long_20261110`，只读。代码侧的合法终止修复见 `bd2e7b2` / `874903c`，**未回写该会话**。
- 2026-11-11 第三轮真实短程验收（`wb_budget_live_20261111`）：5 HTTP / 41,633 Token，2 答 2 条正式评价后以 `BUDGET_LIMIT / NEXT_QUESTION_RESERVATION` 正常结束，Coach live 完成，下一场记忆只引用这两条评价。**20 轮 Live 仍未跑**。

## 4. 当前问题（按优先级）

### P0

- **P1.2a（已修复 + Live 短程已验证，`bd2e7b2` / `874903c`）**：合法 Evaluation 已通过 `resolve_observations`，但"下一题 Main 无法通过会话 Token 准入"时 `core_service.adaptive_answer` 原会直接抛 `INTERVIEW_BUDGET_EXHAUSTED`，Evaluation 只剩 AgentRun 审计、从未落 `InterviewEvaluation`；`controller.commit_answer` 的终止判定只认 `tokens >= max_model_tokens`。现状：该拒绝改为提交 Evaluation + `completion_reason=BUDGET_LIMIT` / `completion_detail=NEXT_QUESTION_RESERVATION`，时长拒绝另记 `DURATION_LIMIT`，Harness OFF/ON 同一路径。详见 `docs/81`、`docs/82`。
- **预算默认值低于实测需求**：默认 200,000 vs 实测 ≈22k/答（18 答 399,420）、docs/70 中性估算 20 答含 Coach 251,982。属部署决策，**本轮不改代码默认值**。

### P1

- Coach + 下一场 MemoryGuidedPlan 依赖 `session COMPLETED`（`interview_report/service.py:47`、`memory/capability_service.py:44`），因此被 P0 阻断。
- ~~ModelScope 路由下按角色选推理模式失效~~ **已修复（`67a39b1`）**：`_chat_provider` 现在透传 `thinking_type`，且 thinking=enabled 的角色输出上限自动抬到 `settings.thinking_output_tokens`（12000）；禁用角色保持不变。
- ~~生产入口缺跨进程发送围栏~~ **答题路径已围栏（`2273cb7`）**：`settle_model_call` 把「已离开进程但没有 usage 回执」的调用按 `answer_id` 记入 `interview_plan_json.unknown_model_calls`；租约过期后 `prepare_answer` 抛 `INTERVIEW_OUTCOME_UNKNOWN`（409）且不发 HTTP，只有 `confirm_unknown_retry=true` 才放行一次并入 `unknown_model_retries`。**仍未覆盖**：`ModelAdmission` 无 `business_key` / provider request id，Celery 重投递路径未验证（推断未围栏）。
- ~~`KeyError`→`model_request_failed`~~ **已修（`804d385`）**：provider 把「发送」与「解析」分段；连接未建立或 `client.post` 抛非 httpx 异常 → `model_request_not_sent`（`request_sent=False`，账本 SETTLED/0）；收到响应后的问题才记 `model_request_failed`。三类语义见 `docs/86` §2。
- 题面主题仍靠正则词典（长尾技术词可能保守误判）。
- 非自适应 `core_service.answer` 存在同一预算死路（本轮只记录，不扩大范围）。

### P2

- `docs/README.md` 索引停在 70。
- ~~5 个陈旧断言~~ **已修（`71c8405`，第八轮）**：改为断言「线上 thinking == 角色配置」与「显式选择非 ModelScope 分支」；第九轮全量回归 0 failed。
- `test_08/test_09` 迁移用例只能在空库跑；在有数据的库上返回非零（不损坏库）。
- 测试隔离只靠人工 `M1_TEST_DATABASE_URL`，`conftest.py` 无库名白名单，测试写入不清理。
- 前端不暴露 Harness 开关、不展示提前结束原因、未暴露 `confirm_unknown_retry`。
- docs/71 规划的 `apps/api/scripts/evals/` 尚未实现。

## 5. WorkBuddy 专属协作规则

- 与 Codex 串行、单工作树、`master` 单线；不并行编辑同一目录。
- 只新增 `docs/81+` 与 `.codebuddy/`；历史文档 append-only。
- 不复制、不改写 `AGENTS.md` / `CLAUDE.md` / `.claude/`；如确需改动，只提建议。
- 不调用真实收费模型、不改 `.env` / Provider 配置、不执行迁移、不重启或删除既有 Docker 容器与数据卷。
- 测试只用新建隔离库（名字带日期/任务后缀），并记录库名 + Alembic 版本。

## 6. Git 与交接规范

- 开工：记录 `git rev-parse HEAD` + `git status --short`；若有脏文件或他人提交，先报告再动手。
- 收工：只 `git add <明确路径>`，禁止 `git add -A`；不 amend、不 push、不 merge/rebase/reset/clean/stash。
- 提交前缀：`feat|fix|test|docs(workbuddy): ...`；业务与记忆分开提交。
- 汇报必含：commit hash、修改文件、测试真实计数（passed/failed/skipped）、未验证项。

## 7. 已验证事实 / 待验证

**已验证（可复现）**

- HEAD `62333df` 提交时工作区干净；无遗留未提交修改。
- `final_readonly_audit.json`：`questions=18`、`evaluations=17`、`status=IN_PROGRESS`、`version=38`、`used=399420`、`next_main_reserve=81129`、`frozen_budget=460000`。
- 第 18 答 Evaluation 的模型调用（AgentRun 38）成功；`validate_artifact` 在模型调用内即执行 `resolve_observations`，故该评价在当时已通过业务校验。
- `ReplayModelProvider` 的会话预留恒为 0（`core_service.call_agent`）；因此预算准入测试必须使用非 replay provider。

**待验证（不要当事实用）**

- 部署环境真实 `interview_max_model_tokens`（本轮未读 `.env`）。
- 与 GitHub `origin` 的同步状态（未 fetch）。
- Harness ON 在长场中的工具收益、Coach 在真实长场自然结束后的表现（均无 Live 证据）。
- Celery worker 重投递是否被 `ModelAdmission` 拦住（未测）。

---

### 交接区（每轮结束更新）

- 当前 HEAD：`49d5600`（第九轮文档）；业务见 `6fbbcdd`（启动脚本迁移头）、`aea1748`（简历抽取截断）
- 工作区：干净（仅 `.workbuddy/` 未跟踪）
- 下一轮入口：见「轮次记录」最后一轮（真实模型额度本轮已用满 12/12）

## 轮次记录

### 2026-11-11 · 第二轮（初始化 + P1.2a）

**提交**

| Hash | 内容 |
|---|---|
| `e4df029` | `docs(workbuddy): initialize independent project memory` — 本文件 |
| `bd2e7b2` | `fix(workbuddy): terminate adaptive interview safely on next-question budget limit` — 6 个文件，+489/−8 |

**已完成（事实）**

- 初始化本文件并通过独立提交入库。
- P1.2a：下一题 Main 因会话 Token 准入被拒（未发出任何 HTTP）时，已通过校验的 Evaluation 会正式提交，Session 以 `completion_reason=BUDGET_LIMIT`、`completion_detail=NEXT_QUESTION_RESERVATION` 结束。Harness OFF/ON 共用同一路径。
- 顺带修掉潜伏缺陷：`complete()` 把同一个 dict 对象存进 `completion_json`，就地改 `summary` 导致自适应会话的 `completion_reason` 从未落库。现改为构造新 dict。历史会话未回填。
- Coach 输入新增有界 `session_completion` 事实块（reason/detail/题量/已评价数/`early_terminated`），统计仍只来自持久化评价。
- 改动文件：`interview/core_service.py`、`interview/controller.py`、`interview/main_harness.py`、`interview_report/service.py`、新增 `tests/test_budget_termination.py`、新增 `docs/81_workbuddy_budget_termination.md`。无迁移、无 API 契约变更、无 Provider/.env 变更。

**测试（真实计数）**

- 隔离库：`interview_agent_wb_p12a_20261111`（本轮新建，Alembic `0026`，66 表）。历史验收库与 `.runtime/acceptance/**` 未写入。
- 命令前缀：`DATABASE_URL` 与 `M1_TEST_DATABASE_URL` 同时指向该库，`apps/api/.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`。
- 专项 `tests/test_budget_termination.py`：**8 passed、0 failed、0 skipped**。
- 十一文件完整回归（含专项）：**198 passed、0 failed、0 skipped**（169.85 s）。含 `test_main_harness`、`test_gap_scope_contract`（Harness OFF/ON 各 20 轮 Replay + Coach + 下一场记忆）、`test_m4_interview`（既有 pre-call 预算停止）、`test_m5_*`、`test_v23_training` 等。
- `git diff --check` 通过；真实模型 HTTP = 0。

**未验证 / 遗留（事实）**

- 未重跑 Live；第 18 答现场未回写、未补提交（属封存验收数据，禁止改写）。
- 非自适应 `core_service.answer` 仍有同根因死路，未改。
- `interview_max_model_tokens` 默认 200,000 仍低于实测需求（≈22k/答；docs/70 中性 20 答含 Coach 251,982）。
- `completion_detail` 前端未展示；`docs/README.md` 索引仍未收录 71–81。

**下一轮入口**

1. （需单独授权与额度）20 轮 Live 长面试：单场预算需覆盖 20 × 每答实测用量 + 末段峰值预留；本批实测每答 ≈11k、末次下一题预留 >35.9k，建议 ≥500k 单场、45–60 次 HTTP 上限。
2. 或：把 `choose_action` 的 COMPLETE 拆成 QUESTION_BUDGET / ROUND_BUDGET / DURATION_LIMIT / TOKEN_BUDGET，再更新 20 轮 Replay 断言。
3. 或：普通模式同根因修复（`core_service.answer`）。

### 2026-11-11 · 第三轮（语义检查 + 真实 Live 短程）

**提交**

| Hash | 内容 |
|---|---|
| `874903c` | `fix(workbuddy): distinguish duration from token admission refusals` — 3 文件；+4 专项测试 |
| `f61aaf6` | `docs(workbuddy): record budget-refusal semantics and the live short acceptance` — `docs/82` 新建、`docs/81` 追加 |

**两个语义检查（事实）**

- `reserve_model_call` 只在两类条件下返回 `None`：会话时长到期、Token 不足（`used >= max` 或 `used + reservation > max`）。用户日额度/并发/身份属于 `ModelGovernance` 的 `AppError`，不会变成 `None`。原实现两者共用 `budget_exhausted=True`，会被一律写成 `BUDGET_LIMIT`。
- 现记录 `budget_exhausted_reason`（`DURATION`/`TOKENS`），映射为 `(BUDGET_LIMIT, NEXT_QUESTION_RESERVATION)` 或 `(DURATION_LIMIT, NEXT_QUESTION_DURATION)`；读不到原因不终止，继续抛错。
- `MainHarness.call` 只有准入拒绝一条 `None` 路径；Schema 非法、模型错误、业务校验、Controller 拒绝都会记为 FAILED 并重新抛出。**该文件本轮未改**，只补 Harness ON 的拒绝测试。

**真实 Live 短程（批次 `wb_budget_live_20261111`，隔离库 `interview_agent_wb_budget_live_20261111`，Alembic 0026）**

- 5 HTTP / 41,633 Token / HTTP 时间窗 224 s：Main 6,021 → Eval 4,129 → Main 8,218 → Eval 4,548 → Coach 18,717；0 Repair、0 重试、0 回退。
- 会话 2：`COMPLETED`、`state_version=5`、2 题 2 答 2 条正式评价、`completion_reason=BUDGET_LIMIT`、`completion_detail=NEXT_QUESTION_RESERVATION`、`budget_exhausted_reason=TOKENS`、已用 22,916 / 冻结 58,860、预留表为空；**第 3 题没有任何 HTTP**。
- 报告 1 `COMPLETED:SUMMARY_COACH_AGENT`（live），Coach 输入含 `session_completion`（2/6、early_terminated=true）。
- 下一场 session 3 的 `MemoryGuidedPlan`：`ai_agent_application:agent` / PRACTICING / `observation_refs=["evaluation:1","evaluation:2"]`。
- 账本 5 条 SETTLED，charged=actual=41,633；5 条响应各精确回放 2 次，改输入被拒。
- 隔离实例开关：仅本批 Main/Evaluation `thinking_type="disabled"`，输出上限保持生产 3,000，Coach 保持默认。正式 Provider/`.env` 未改。

**测试**：专项 12 passed；十一文件回归 **202 passed、0 failed、0 skipped**（隔离库 `interview_agent_wb_p12a_20261111`，Alembic 0026）。

**未验证 / 遗留（事实）**

- 未跑 20 轮 Live；长场自然结束、Coach 长场表现、跨场记忆质量仍未验证。
- `DURATION` 分支只有单测：真实会话中 `choose_action` 会先按同一阈值返回 COMPLETE，该分支几乎不可达。
- `choose_action` 的 COMPLETE 仍把"题数用满/轮次用满/时长/Token"统一记为 `BUDGET_LIMIT`。
- 生产默认 `interview_max_model_tokens=200,000` 低于实测需求（≈11k/答 + 每答预留）。
- 非自适应 `core_service.answer` 同根因死路未修。

**交接备注**：批次目录 `.runtime/acceptance/wb_budget_live_20261111/` 已封存（`batch_state.stop=COLLECTION_COMPLETE`）；首次达成终态时的 `TARGET_STATE_REACHED` 是本地书签、当时无在途请求，已按 `docs/82` §10 说明清除，HTTP 数未增加。


### 2026-11-11 · 第四轮（终止原因分类 + 18/20 真实长场）

**提交**

| Hash | 内容 |
|---|---|
| `307d273` | `fix(workbuddy): classify the interview stop cause instead of one BUDGET_LIMIT` — 4 文件 |
| `5a24f75` | `fix(workbuddy): authorise the one structural repair the model actually needs` — 4 文件（含真实响应夹具） |
| `e1dc2e1` | `docs(workbuddy): record the 18/20 live long interview and its blockers` |

**已完成（事实）**

- `SessionController.stop_reason` 成为唯一终止判定：`QUESTION_COUNT_REACHED / ROUND_LIMIT / TOKEN_LIMIT / DURATION_LIMIT`，题数优先，答满题数不再标成预算结束。`BUDGET_LIMIT` 仅作旧值兼容，不回写历史行。
- `FlattenedQuestionRepair` 授权"扁平草稿→嵌套结构"的唯一归一化，修复后的内容必须由原始字符串拼出；与 `SupportingRefsRepair` 组合。契约与事实校验未改。
- Live 批次 `wb_full_live_20261111`（库 `interview_agent_wb_full_live_20261111`，Alembic 0026）：47 HTTP / 588,586 Token / 约 45 分钟；18 题、18 条正式评价；`COMPLETED` + `TOKEN_LIMIT / NEXT_QUESTION_RESERVATION`；第 19 题零 HTTP；下一场 MemoryGuidedPlan 3 条、引用 `evaluation:10–18`。
- 参数落地已用请求体核对：Main 26 次全 `disabled`，Evaluation 20 次 + Coach 1 次全 `enabled`。
- 离线：专项 9 + 7 passed；11/13 文件回归 211 / 218 passed（隔离库 `interview_agent_wb_p12a_20261111`，Alembic 0026）。真实模型仅在本轮 Live 批次内调用。

**未完成 / 新增问题（事实）**

- 未跑满 20 题：650k 单场预算按实测每答 ≈31.6k 只够 18 题；第 19、20 题未提问。
- Coach 未生成报告：真实调用 68,248 Token 被 `COACH_COMPUTED_NUMBER` 误判（"至少一次"），重试分别被 `CAPTURE_GUARD` 与 `MODEL_USER_QUOTA` 拦住。详见 docs/83 §5.3。
- 三个新 P0：思考模式 Evaluation 与 3000 输出上限不匹配（3k/8k 均截断，12k 才通过）；Coach 200KB 输入上限使长场无法进入；数字围栏误杀技术惯用语。
- P1：`structured_output_failed` 无授权修复时不可恢复（需受控重采样）；采集围栏在准入后中断会留下 UNKNOWN 计费（本批 291,808 Token）；`router.py` 仍丢弃 `thinking_type`。
- 本轮禁止改口径：未放宽任何校验规则、未改生产配置、未在运行中改库、未重开会话刷成功率。

**下一轮入口**

1. 修 Coach 两项（输入上限策略 + 数字围栏），再补一次 Coach 真实重跑（需额度）。
2. 决定 Evaluation 思考模式与输出上限的生产策略（配置或路由层），一并处理 `router.py` 丢弃 `thinking_type`。
3. `structured_output_failed` 的受控一次重采样。
4. 预算/题量匹配：20 题需 ≥700k，或把 `question_count` 与预算绑定。

### 2026-11-11 · 第五轮（长场 Coach 与 Evaluation 稳定性，0 次真实 HTTP）

**提交**

| Hash | 内容 |
|---|---|
| `70da7e6` | `fix(workbuddy): let long interviews reach the Coach, and stop rejecting idioms` — 8 文件 |
| `67a39b1` | `fix(workbuddy): forward role-level thinking and give enabled roles output headroom` — 3 文件 |
| `b37103e` | `docs(workbuddy): record the coach and evaluation reliability fixes` |

**已完成（事实）**

- 数字校验改为"单位 + 服务端统计白名单"：`至少一次` / `一次调用` 等惯用语放行；`六轮回答`、`总分 5 分`、`85%`、`9 道题` 仍拒绝；分数只接受已发布的维度/总体均值。诊断含 `rule=FABRICATED_STATISTIC`、unit、value。
- 新增 `COACH_EVIDENCE_V1`（`interview_report/evidence.py`）：18 轮真实信封 227,061 → 90,615 字节（input 221,493 → 85,047），20 轮 99,749（+9,134）；保留评价 ID、题目/维度、五维分、作答、结论、覆盖/遗漏点、事实问题、追问方向、未解决 gap、有效题数与结束原因，移除项逐项登记在 `evidence_projection.omitted`；仍超限抛 `COACH_EVIDENCE_BUDGET`（含分节字节）。
- 角色推理：`_chat_provider` 不再丢弃 `thinking_type`；仅 thinking=enabled 的角色把输出上限抬到 `thinking_output_tokens`（12000），Main 仍 3000。用 MockTransport 抓请求体核对 `thinking={"type":"enabled"}` + `max_tokens=12000`，`None` 时请求体无该字段。
- 第四轮原始 Coach 响应（请求 47）离线重放可通过新校验；请求 2/9（截断）与 47 各精确回放 2 次。
- 20 条归档 Evaluation 一致性审计：18 条可解析、2 条截断（无假分数）、0 条不支持引用、0 条 gap 越界。

**未完成 / 待真实模型验证（事实）**

- 压缩后的 Coach 输入尚未经过真实模型，报告质量未验证。
- `thinking_output_tokens=12000` 只在参数层验证，未经真实 Evaluation 复跑。
- Evaluation 思考/非思考质量对比缺配对样本（20 条全为 enabled），不作降质/不降质结论。
- 本轮真实模型 HTTP = 0；未改生产配置、未改历史验收数据。

**`.workbuddy/`（事实）**

- 只有一个 4KB 的 `memory/2026-11-11.md`，是工具侧工作区记忆摘要，与本文重复、无敏感内容；未删除、未提交、未改 `.gitignore`。后续只写指针，不写规范；如需消除，可在设置里关闭工作区记忆或把它加入 `.gitignore`（需你/Codex 决定）。

### 2026-11-11 · 第六轮（18 条归档评价的真实长场 Coach + 定向 Evaluation）

**提交**

| Hash | 内容 |
|---|---|
| `e45386f` | `fix(workbuddy): check a one-time claim about this session against the count` — 2 文件 |
| `50198be` | `docs(workbuddy): record the long-session coach reached live, and the token accounting` — docs/85 新建、docs/84 追加更正 |

**已完成（事实）**

- 数字校验补漏：会话作用域内的 `一次`（本场/这场/全场/本次/一共/合计… 或 `只…了`）不再当惯用语，改走服务端计数白名单；`至少一次投递` 等仍放行。专项 23 passed。
- 归档 Evaluation Token 口径核实：`max_tokens` 是 completion 上限，推理与正文共用；`reasoning_tokens` 在 `completion_tokens_details.reasoning_tokens` 且计入 completion。docs/84 的错误数字已追加更正（10 号请求是 8,196/6,880，不是 11,879）。
- 18 轮数据复制：`pg_dump` 只读快照 → `pg_restore` 到 `interview_agent_wb_coach_live2_20261111`（Alembic 0026）；evaluations/questions/sessions 1–3 逐表 md5 与源库一致，源库未写入。
- 真实 Coach（1 HTTP / 30,661 Token / 46.8 s）：请求体 `thinking={"type":"enabled"}`、`max_tokens=12000`；输入 87,342 字节（旧 223,769）、输入 Token 22,303（旧 59,798）；`COACH_EVIDENCE_V1`，删减项在 `evidence_projection.omitted` 登记。
- Report 1：`COMPLETED`、`is_current=true`、`SUMMARY_COACH_AGENT`、modelscope，attempt 5（前 4 次失败记录保留）；5 优势 / 6 弱项 / 3 重复问题 / 5 对齐 / 5 建议 / 6 练习 / 3 记忆提案，全部引用 1–18 的正式评价；逐条与第 11/12、17/18、5/6、14/15 轮对照一致，无编造数字或候选人事实。
- 下一场 session 4 的 `MemoryGuidedPlan`：3 条，引用 `evaluation:10–18`，`refs_resolve=true`。
- Evaluation 探针（2 HTTP）：同一归档输入（第四轮请求 9） enabled+12000 → 合法评价，五维与归档 id=3 完全一致、同样的 2 个 gap；disabled+12000 → 4 条引用超上限 3，Schema 不合法（服务端会拒绝，不落分数）。**只有 1 对样本，不作质量结论。**
- 全批 **3 HTTP / 52,660 Token**（上限 8 / 220k），0 Repair、0 重试、0 非 200；3 条响应各精确回放 2 次，账本 known=charged。
- 离线回归 264 passed / 0 failed / 0 skipped（隔离库 `interview_agent_wb_r6_20261111`，Alembic 0026）。

**失败现场（保留，未当成功）**

- 第一次尝试（`.runtime/acceptance/wb_coach_attempt1_20261111/`，库 `interview_agent_wb_coach_live_20261111`）0 次 HTTP：采集作用域漏 `category` → `capture.request` 抛 `KeyError` → provider 兜底映射成 `model_request_failed`；准入留 `UNKNOWN/105,103`。夹具已修，产品错误映射**未改**（记录为待评估）。

**未验证 / 遗留（事实）**

- 生产默认 `interview_coach_thinking=disabled` + 6,000 输出：本轮用 enabled+12000，默认组合未验证。
- Evaluation 思考/非思考质量差异：1 对样本，不结论。
- 20 轮 Live 仍未跑（第 19、20 题未提问）。
- 发送围栏 P1 仍在；`KeyError`→`model_request_failed` 映射建议单独评估；`docs/README.md` 索引仍停在 70。

**下一轮入口**

1. 决定 Coach / Evaluation 的思考模式与输出上限生产默认值（配置决策）。
2. 若要做 20 轮 Live：单场预算 ≥700k，并先解决 Coach 输入在 30 轮规模的字节增长。
3. 发送围栏（send-intent + provider request id）与错误码分类。

### 2026-11-12 · 第七轮（请求安全收口 + 产品化路径审查，0 次真实 HTTP）

**提交**

| Hash | 内容 |
|---|---|
| `804d385` | `fix(workbuddy): stop reporting a local pre-send failure as a provider fault` — 3 文件 |
| `2273cb7` | `fix(workbuddy): require an explicit accept before replaying an unknown answer` — 6 文件 |
| `25f18fd` | `docs(workbuddy): audit the request-safety chain and the self-service path` — `docs/86` 新建 |

**已完成（事实）**

- 根因链复现：httpx request hook 抛 `KeyError` → provider 兜底 `except (…, KeyError, …)` 记 `model_request_failed` → `governed_call` 的 `sent` 已为 True、`usage={}` → `settle` 判 `UNKNOWN` 并按预留全额计费。
- `_generate` 拆「发送 / 解析」两段：`ConnectError|ConnectTimeout|PoolTimeout` 与 `client.post` 抛出的非 httpx 异常 → `model_request_not_sent` + `request_sent=False` + `usage.total_tokens=0`；读/写超时与传输错误保持 UNKNOWN；收到响应后的信封问题记 `model_request_failed` + `request_sent=True`。
- `governed_call` 以异常自报的 `request_sent` 优先；已确证未发送走 `settle(…, {"total_tokens": 0})` → `SETTLED / actual=0 / charged=0`（沿用 `test_v13_shared_governance` 的 `before_send` 既有口径），未新增账本状态、未直接改审计字段。
- 答题围栏：`settle_model_call(request_sent=…, answer_id=…)` 记录 `unknown_model_calls`；`prepare_answer` 在租约过期且存在滞留预留/未知调用时抛 `INTERVIEW_OUTCOME_UNKNOWN`（409，零 HTTP）；`SendMessageRequest.confirm_unknown_retry` 可显式放行一次，写 `unknown_model_retries`；SSE 错误事件带 `requires_confirmation`。
- 用户路径审查（`docs/86` §5）：注册/登录、简历上传、Job Matching、Evaluation+Coach、Memory 训练为完整实现；**粘贴 JD 与私有岗位完全缺失**；创建 Session 硬依赖 `ACTIVE` 公司/岗位 + `PUBLISHED` JD + requirement + 画像。
- 私有 JD 方案对比后推荐「新增 1 张 `user_job_targets`」而不是改 `companies/jobs/job_descriptions`（后者要改 4 张公共表与 3 个全局唯一约束，泄露风险靠每处查询自觉）。迁移**未实施**，等批准。

**测试（真实计数）**

- 隔离库 `interview_agent_wb_r7_20261112`（本轮新建，Alembic `0026`，66 表）；Redis 容器本轮启动，收工未停。
- 专项：`test_request_safety.py` 5 passed；`test_answer_retry_fence.py` 3 passed；与 `test_interview_recovery_performance.py` 合并 16 passed。
- 全量（排除 `test_0[0-9]_*` 迁移用例）：**721 passed / 5 failed / 1 skipped**（165 s）。
- 5 failed 全为既有且与本轮无关：4 例 `test_modelscope_provider` 断言 `"thinking" not in body`（`disabled` 现在也会显式发送）、1 例 `test_product_polish` 断言统一模型名（本机 `USE_MODELSCOPE=true`）。
- `git diff --check` 通过；真实模型 HTTP = 0。

**改变了既有行为（需知悉）**

- 旧行为：租约过期后同一幂等键直接重发一次真实 HTTP。新行为：默认拒绝，需 `confirm_unknown_retry=true`。`test_interview_recovery_performance::test_expired_processing_lease_fences_old_answer_without_duplication` 已按新语义更新，仍保留原意（旧结果不能覆盖已提交答案、只产生 1 条 Answer）。

**未验证 / 遗留（事实）**

- 用户级账本仍无 `business_key` / provider request id，无法与供应商账单对账；worker 重投递路径未测。
- UNKNOWN 会话只能显式确认或新建，没有管理员对账入口。
- 私有 JD 的隐私/留存策略、解析失败时的降级体验未设计。
- 前端未接入 `confirm_unknown_retry`，用户目前只会看到 409 文案。

**下一轮入口**

1. 批准并实施私有 JD Workbench V1：迁移 `0027_user_job_targets`（1 表 + 1 唯一索引），后端 8 个文件、前端 8 个文件，见 `docs/86` §7。
2. 或先做「用私有目标创建面试」的最小纵切（`job_target` 只读 + `context.py` 分支），把 JD 解析留到下一步。
3. 顺手可做：修正 5 个陈旧断言（单独 `test(workbuddy)` 提交），避免 CI 绿灯失真。

### 2026-11-12 · 第八轮（CI 收口 + 私有 JD Workbench V1，0 次真实 HTTP）

**提交**

| Hash | 内容 |
|---|---|
| `71c8405` | `test(workbuddy): assert the real thinking policy and the non-hosted model branch` — 2 文件 |
| `9ec6416` | `feat(workbuddy): interview against a JD the user pasted themselves` — 迁移 0027 + `modules/job_target` 6 文件 + Interview 5 文件 + 测试 |
| `98cfbbc` | `feat(workbuddy): paste, review and confirm a JD from the web app` — 前端 7 文件 |
| `5082756` | `docs(workbuddy): record the private JD workbench and the snapshot constraint` — `docs/87` |

**CI 五项（事实）**

- 4 例 `test_modelscope_provider`：断言写死「无 thinking 键」，而 `67a39b1` 之后角色策略原样上线（`disabled` 也显式发送）。改为断言「线上 thinking == 角色配置；None 时无键」。
- 1 例 `test_product_polish`：只在非 ModelScope 部署成立；`use_modelscope` 是只读属性，改为 patch `llm_provider`。
- 两文件由 9 passed / 5 failed → **14 passed / 0 failed**。

**私有 JD（事实）**

- 新表 `user_job_targets`：`user_id` 归属、`raw_text`、`source_type=USER_SUPPLIED`（CHECK）、`content_fingerprint`（同用户唯一）、`requirements_json`、`parse_status`（CHECK）、`parse_mode`（LLM/RULE）、`version`、`confirmed_at`。
- **一张表不够**：`interview_context_snapshots` 原有 3 个 NOT NULL 公共外键，唯一替代方案是伪造公共 Company/Job/JD（本轮禁止）。0027 改为可空 + `job_target_id` + CHECK「恰好一个来源」；`downgrade` 在有私有快照时**主动拒绝**（实测报 `39 interview snapshots…`）。
- 解析复用 `public_research_extract` route 与 `generate_structured`；每条要求必须带**原文逐字** `evidence`，否则丢弃；公司/岗位原文没写就留 `null`；模型不可用降级 `RULE` 并如实标注。
- 私有要求索引（1..n）**绝不写入** `interview_session_questions.job_requirement_id`（会指向无关公共要求），改存 `candidate_refs_json.job_target_requirement`；`question_policy` 同时读两处去重。
- `skill_engine.freeze` 新增 `USER_PROVIDED_JD_CONTEXT` 来源模式，不冒充 `SOURCED_JOB_CONTEXT`；私有岗位不做自动 Skill 推荐，前端强制手选。
- 快照冻结：改要求 → `PARSED`、`version` 不变；再确认 → `version+1`；已有 Session 的 `context_snapshot` 逐字节不变（测试断言 `reread == frozen`）。

**测试（真实计数）**

- 隔离库 `interview_agent_wb_r8_20261112`（`0027`，67 表）。
- 专项 `test_job_target.py`：**8 passed**；全量（排除迁移用例）：**734 passed / 0 failed / 1 skipped**（204 s）。
- 前端：`vue-tsc --noEmit` 与 `npm run build` 均通过。
- 接口级联调（本地 uvicorn + 离线替身 `127.0.0.1:8899`，**非真实模型**）：注册 → 建 target → 解析（`LLM`，3 条中 2 条留存，编造的那条被逐字校验丢弃）→ 确认 `version=1`。

**未验证（事实）**

- 真实模型对真实 JD 的抽取质量未验证。
- **未做浏览器操作**：无可用浏览器自动化通道，`npm run dev` 未在 5173 监听；只做了类型检查、生产构建与真实 HTTP 接口联调。
- 未跑「上传简历 → 画像 → 私有岗位面试」的完整 UI 流程（画像需 Worker + 模型）。

**新增风险**

- Job Matching 仅复用确定性 `evidence_matrix` 做单目标证据视图，**未接入**批量推荐，也不写 `job_match_*`。
- 训练中心仍只支持公共岗位。
- 私有 JD 留存期/脱敏策略未定义。

**下一轮入口**

1. 用真实模型验收私有 JD 解析质量（需额度），并对比 LLM 与 RULE 的差异。
2. 训练中心接入私有目标（`training.py` 返回 `company_id/job_id` 为 None，需要按 `job_target` 分支）。
3. 单目标匹配报告（是否落表、如何避免与公共 Job Matching 混淆）需要设计。
4. 私有 JD 的留存/删除/导出策略与隐私说明。

### 2026-11-12 · 第九轮（真实浏览器 E2E + 私有 JD 安全核验 + 有界 Live）

开工 `8eb65c0`。本轮按用户显式授权：新建隔离库并执行 0027 迁移、启动本地前后端与 Worker、
Chrome 真实 UI 操作、12 次真实模型 HTTP。正式库与历史 Live 库未触碰。

**提交**

| Hash | 内容 |
|---|---|
| `6fbbcdd` | `fix(workbuddy): read the migration head from the scripts, not a constant` — `scripts/delivery.py` + 2 个测试文件 |
| `aea1748` | `fix(workbuddy): stop reasoning from truncating the resume extraction JSON` — `llm/provider.py`、`resume/processor.py`、`resume_tasks.py`、新增 `test_resume_extraction_budget.py` |
| `49d5600` | `docs(workbuddy): record the real-browser private-JD E2E and its limits` — `docs/88` |

**发现的两个真实产品链路缺陷（事实）**

1. `scripts/delivery.py::migrate` 把迁移头与数据库版本都写死 `0024`。在正确迁移到 `0027` 的库上实测
   `MIGRATION_FAILED (RuntimeError)`，即 `start-dev.bat` 在 0025+ 环境必然启动失败。改为从
   `ScriptDirectory` 读 head；修复后 `{revision: "0027"}`。
2. 真实 UI 上传 604 字合成简历失败：`Unterminated string starting at ... char 2011`，且原始异常文本
   被写进用户可见字段。账本显示 6,852 token 只换来 2,043 字符 —— 简历结构化调用没带 thinking 策略，
   DeepSeek-V4.1-Flash 把 6,000 输出预算耗在推理上，JSON 被截断。修复：抽取调用固定
   `thinking_type=disabled`、`finish_reason=length` → `MODEL_OUTPUT_TRUNCATED`（`request_sent=True`）、
   用户可见错误文案脱敏、Worker 不覆盖已记录原因。修复后 resume 238 `COMPLETED`，画像版本 1 真实生成。

**真实浏览器 UI（事实）**

- 无 Playwright 包；用 Node 22 内置 WebSocket 直连 Chrome 157 CDP（`.runtime/ui/cdp.mjs`），
  驱动真实 Vue 页面完成点击/输入/下拉/文件上传/截图。截图 16 张在 `.runtime/ui/shots/`，
  脚本在 `.runtime/ui/`（`.gitignore:31` 忽略，不进 Git）。
- 11 步全部走通：注册登录 → 合成简历上传+真实画像 → 粘贴公开 JD → LLM 解析 4 条逐字证据要求 →
  前端改/删/补 → 证据面板 → 确认 v1 → 选 Skill 建 session 321 → 3 答 → 正式评价 3.6/1.2/1.2
  → Coach 报告 44 `COMPLETED` → 批准记忆提案 → 第二场 322 / 第三场 323 读取跨场 Memory。
- **偏差**（不得抹掉）：① E2E 库被全量回归夹具写入 219 ACTIVE 公司 / 347 PUBLISHED JD，前端下拉出现
  `DEMO ...`；用户 A 全链路未引用它们（快照公共三外键 NULL）。另建 `..._r9_clean`（`companies=0,users=0`）
  跑 105 项离线专项证明空库可建私有 JD 面试。② 第三场用正式 API 创建且未 start（UI 会自动 start，
  会超 12 次额度）。③ Capture/Replay 本轮未启用。

**真实模型（事实）**

- **12 / 12 HTTP，75,706 token，全部 `SETTLED`，0 UNKNOWN**（`model_admissions` 可查）：
  resume_report 6,852（失败）+ 1,822（修复后）、resume_profile 2,332、legacy_llm/JD 解析 1,367、
  interview_main 7,817 + 9,691 + 9,794 + 7,933、answer_evaluation 7,380/7,353/7,532、interview_coach 5,833。
- 解析 route 实际是 `legacy_llm`（不是 `public_research_extract`）；模型 `deepseek-ai/DeepSeek-V4.1-Flash-1017`。
- LLM 解析 560 字公开 JD → 4 条要求，全部带原文逐字 evidence；本轮模型**未输出编造项**，
  所以「拒绝编造」只有离线 fixture 证据，不能说真实模型已证明。
- 私有 JD 快照生效（`USER_PROVIDED_JD_CONTEXT`），但本场 3 题均来自候选人画像/追问，
  **没有一题显式引用 JD requirement** —— 短场未触发，不能宣称已验证「JD 驱动出题」。
- RULE 降级只在离线测试覆盖，未与 LLM 做同 JD 并列对比。
- 连续调用窗口 03:33–03:37 + 06:42–06:55 UTC（会话中断分成两段），均在 20 分钟内。

**私有数据安全（0 模型，HTTP 探针 12 项断言全通过）**

- 用户 B 列表为空；详情/匹配/PATCH/DELETE/读 A 会话/读 A 报告全 `404`；
  用 A 的 target 建会话得 `409 PROFILE_REQUIRED`（**归属检查排在画像检查之后，错误码顺序可探测**）。
- 未确认版本 `JOB_TARGET_NOT_CONFIRMED`、混合来源 `JOB_SOURCE_CONFLICT`、不存在 ID `404`。
- 编辑 A 的 JD 后快照 320/321/322 的 `md5(context_json::text)` 前后完全一致；
  删除返回 `JOB_TARGET_IN_USE`，历史会话不失效。
- 三场题目 `job_requirement_id` 计数 0；公共研究/题源 7 张表对私有文本命中全 0。
- XSS：B 的 `<img onerror>` / `<script>` 以纯文本渲染，注入 DOM 元素 0、`alert` 0。
- Prompt injection：注入指令未进入 Skill 配置、未添加工具策略，`company_background` 仍为
  `USER_PROVIDED_JD_CONTEXT` + `USER_SUPPLIED` + 固定「未经企业核实」文案。

**测试计数（分列，不混合）**

- 全量回归（排除迁移用例）：**739 passed / 0 failed / 1 skipped**（217 s）。
  跳过仍是 `test_v142_source_fragments.py:96` 缺 V1.4.1 快照库。
- 迁移专项：空库 `..._r9_migbase` 跑 00–08 → **9 passed**；`interview_agent_v21_wb_r9_20261112`
  跑 09 → **1 passed**（该用例强制库名前缀 `interview_agent_v21_`）。
- `0026→0027→0026→0027` 往返四步全成功；`ck_interview_snapshot_source` 在库中确认存在。
- 干净库离线专项 6 文件：**105 passed / 0 failed / 0 skipped**，0 真实 HTTP。
- 前端 `vue-tsc --noEmit` 0 错误、`npm run build` 成功。
- 运行期 HTTP 安全探针不计入 pytest 数字。

**环境（事实）**

- 启动脚本 `start-dev.bat` / `start-interview-platform.ps1` 会**自动执行迁移**；环境变量
  `DATABASE_URL` 优先于 `.env`，已用 `delivery.py config` 确认指向隔离库。
- 该脚本能完整拉起 API/Worker/Vue，但 `Start-Process` 的子进程随外层会话被回收；
  改用同一套 `delivery.py api|worker` + `scripts/serve-web.mjs` 常驻后台任务。
- Windows 同时存在 `HTTPS_PROXY` 与 `https_proxy` 时 Windows PS 5.1 的 `Start-Process` 抛
  「已添加项」；本轮清除小写项绕过，**未改代码**，建议后续在 `platform-common.ps1` 去重硬化。

**仍需修复（事实/待办）**

1. `build_context` 归属检查前置（统一 404，避免错误码探测）。
2. Capture/Replay 缺口：12 次真实调用无归档，无法精确重放；**本轮未满足用户这一项要求**，
   需下一轮新预算单独批准带 capture 的有界批次。
3. 前端 `handleCreateSession` 总会自动 `handleStart()`，无法只创建会话。
4. 证据面板顶部计数按行 status，行内却展示 PARTIAL 支撑事实；报告生成中提示与
   「暂无成功的 Coach 分析」并存，易误读；相同 JD 重复创建会 version+1 且无提示。
5. 训练中心仍不支持私有目标；私有 JD 留存/删除/导出策略与隐私说明未定义。
6. 仍待真实模型：LLM vs RULE 同 JD 对比、编造项丢弃率、JD requirement 显式驱动出题、
   Harness ON 在私有目标下、Coach 长场稳定性。

**下一轮入口**

1. 先修 `build_context` 归属检查顺序（小改动 + 专项测试，0 模型）。
2. 若用户再批额度：做一次**带 Capture/Replay 归档**的有界 Live，重点验证 JD 要求驱动出题与编造项拒绝。
3. 训练中心接入私有目标（需按 `job_target` 分支，属新功能，须单独批准）。
