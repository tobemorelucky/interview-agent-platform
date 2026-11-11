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
| Alembic head | `0026_capability_memory`（共 26 个版本） | `apps/api/alembic/versions/` |
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
- ModelScope 路由下按角色选推理模式失效：`config.py` 有 4 个 `*_thinking`，但 `router.py::_chat_provider` 在 ModelScope 分支不传 `thinking_type`。
- 生产入口缺跨进程发送围栏：`ModelAdmission` 无 send-intent / provider request id，`governed_call` 每次新 `uuid4` call_key。
- 题面主题仍靠正则词典（长尾技术词可能保守误判）。
- 非自适应 `core_service.answer` 存在同一预算死路（本轮只记录，不扩大范围）。

### P2

- `docs/README.md` 索引停在 70。
- 测试隔离只靠人工 `M1_TEST_DATABASE_URL`，`conftest.py` 无库名白名单，测试写入不清理。
- 前端不暴露 Harness 开关、不展示提前结束原因。
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

---

### 交接区（每轮结束更新）

- 当前 HEAD：`f61aaf6`（第三轮文档）；业务修复见 `874903c`，第二轮 `bd2e7b2`/`e4df029`
- 工作区：干净（本轮收尾时核实）
- 下一轮入口：见「轮次记录」最后一轮

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

