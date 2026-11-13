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
- `test_08/test_09` 迁移用例只能在空库跑；在有数据的库上返回非零（不损坏库）。本轮确认：10 个迁移用例在专用空库 `interview_agent_v21_wb_r10_20261112` **10/10 通过**；在承载数据的回归库里必然失败（第十轮）。
- ~~测试隔离只靠人工 `M1_TEST_DATABASE_URL`，`conftest.py` 无库名白名单~~ **已修（`9eb66a8` / `3b99f6c`，第十轮）**：`conftest.py` 加 session 级 autouse 闸（前缀 + 测试后缀 + 黑名单 + 两个环境变量必须一致，否则 `pytest.exit(2)`），`scripts/run-workbuddy-tests.py` 在运行前拒绝生产/E2E/历史 Live 库。
- ~~库名闸只覆盖 pytest~~ **已修（第十一轮，`be1407e` / `275343e`）**：判定收敛到 `interview_api.core.db_targets`（纯字符串，九类）；`delivery.py migrate` 需 `--authorize-database <名字>` 才能动生产/E2E/未知库，系统库与历史 Live 库**无覆盖**；启动脚本只在操作员传 `-AuthorizeDatabase` 时转发；`prepare_v23_demo.py` 只接受 `*_browser_*` 夹具库。
- ~~私人 JD 训练的 RECHECK 未验证~~ **已验证（第十一轮，`1e24814`）**：`CONFLICTED`（同 scope 先好后坏）与 `RECHECK_DUE`（VERIFIED + 30/60/90 天，靠服务 `clock` 前移读取）都能合法进入 RECHECK；能力记忆仍按 `(user, topic)` 聚合，没有岗位维度。
- ~~`training_mode` 单独出现（无 `training_request_key`）绕过幂等~~ **已修（第十二轮，`4a25891`）**：路由条件只看 `training_mode`，缺键在开库前 `422 TRAINING_REQUEST_KEY_REQUIRED`；并发/重启/换内容三类语义有 10 条专项测试。**保证来自 `pg_advisory_xact_lock`，不是数据库约束**——新增创建路径绕过 `TrainingService.create` 会复发。
- ~~私人岗位入口只能先进详情才能找到~~ **已修（第十二轮，`80149e9`）**：已确认目标的行内直接给「开始训练」，未确认行给原因而不是按钮。
- ~~Coach 的记忆输入是 `str(dict)` 截断到 600 字符~~ **已修（第十三轮，`790913e`）**：`COACH_EVIDENCE_V2` 把两个记忆层改为结构化投影（semantics / epistemic_status / items / shown / total / clipped），并把 `memory_guided_plan` 投影成 `capability_context`、`training` 投影成 `training_context`，每条评价补 `fingerprint`。跨岗位语义由 `scope_note` 明写。真实 18 轮夹具体积 85,047 → 86,830 字节（+2.1%）。
- **Coach Harness 已实施、默认 OFF**（第十三轮 `4e726f9`）：`interview_coach_harness` 打开后模型可用一次只读工具（`read_evaluation_detail` 带指纹校验 / `read_capability_history` 读尝试开始时的一次性快照），最多 2 次模型调用；`suggested_training` 由 `resolve_coach_artifact` 用 `training.modes()` 重新校验，非法即降级并记录。OFF 路径的提示词、schema、生成键与上一轮逐字节一致（有用例断言）。
- **Harness Live 短程结果（第十四轮，`docs/93`）**：同一真实会话（18 评价）跑 OFF ×1 + ON ×2，共 **3 次 HTTP / 77,110 token**。ON 两次都 `SINGLE_CALL_MODEL_CHOSE_FINAL`——**两个工具可用但 0 次调用**，正文也从未引用跨场次状态。ON 唯一结构性增益是 `suggested_training`（两次都合法：PRACTICING→WEAKNESS，引用真实评价），成本 +3.5% token。
- **工具价值审查（第十五轮，`docs/94`，结论：暂时搁置 Harness）**：零成本审计发现 `read_evaluation_detail` 修复前在真实数据上返回的答案比 V2 更短（401 < 601 字）且重复 V2 已有字段；第 18 条评价整个结果 4,199 字节、净增量几乎为零。修复后两个工具只返回增量（真实第 18 条 1,411 字节），历史工具的 1,214 > 1,200 越界也修掉。合成场景 B（正式 Service 造出裁剪评价）证明工具**能**返回隐藏事实，但真实 Live 中模型**仍 0 次调用**（累计 0/4），且 ON 的 WEAKNESS 建议因 CONFLICTED 被 resolver 拒绝，确定性规则基线却给出合法 RECHECK。**结构化建议有价值，但这价值不来自工具调用；零模型调用的规则基线已能满足主要需求。** 保持 `INTERVIEW_COACH_HARNESS=false`，不再为诱导调用改 Prompt / 加工具 / 加轮次。
- **生成键缺口已修（第十四轮，`b7a9403`）**：ON 的键现在含 `tools.policy_signature()` 与报告期能力捕获指纹；OFF 公式逐字节不变（证据：本轮 OFF 命中 11-11 那条 FAILED 行的旧键并在该行完成重试）。
- ~~报告页建议链接丢失岗位~~ **已修（第十四轮，`98c9e35`）**：报告 session 载荷补 `job_id`/`company_id`，链接现在能预选岗位+模式+知识点；跳转不创建训练（会话数 3→3）。
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

- 当前 HEAD：`git log -1`（第十五轮文档 `7631d38` 之后只有本文件的记录更新）
- 工作区：干净（仅 `.workbuddy/` 未跟踪，按第五轮约定不提交）
- 下一轮入口：见「轮次记录」最后一轮（第十五轮真实模型 HTTP = 2 / 10,505 token；**Coach Harness 建议暂时搁置、保持 OFF**，下一阶段转向普通用户闭环，不是 Research/Matching）
- 环境提示：本轮开始时 postgres/redis/minio 容器处于 `Exited (255)`，用 `docker start` 拉起后数据完好（151 个库）。**不要**用 `docker compose down -v` 之类的操作。
- 副本库 `interview_agent_wb_r14_real_20261113` 有 3 份真实模型报告（gen 1/2/3），**保留**；原件 `interview_agent_wb_full_live_20261111` 未变。`interview_agent_wb_r15_real_20261113` 是本轮合成场景 B 的 OFF/ON 两份报告，保留作证据。

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

### 2026-11-12 · 第十轮（私人 JD 训练闭环 + 测试库隔离 + 归属顺序，0 次真实 HTTP）

开工 `8354b2a` 之上，工作区有 8 个未提交的改动文件（第十轮中途中断处）。本轮续做并收口。
真实模型 HTTP = **0**；未使用历史 Live Capture 回放；未迁移；未操作浏览器。

**提交（6 + 1）**

| Hash | 内容 |
|---|---|
| `9eb66a8` | `test(workbuddy)`: pytest 拒绝写入 E2E / 生产 / 历史 Live 库 |
| `8354b2a` | `fix(workbuddy)`: 私人 JD 归属先于其他校验 |
| `1f2071f` | `fix(workbuddy)`: JD 复用与逐事实证据计数如实汇报 |
| `7715ccc` | `feat(workbuddy)`: 用已确认的私人 JD 进入训练（+ 4 条新测试） |
| `b7d4cb9` | `fix(workbuddy)`: 岗位页与报告页不再误报自身状态 |
| `3b99f6c` | `test(workbuddy)`: 带闸门的隔离测试运行器 |
| `1789cca` | `docs(workbuddy)`: `docs/89` 十节记录 |

**已完成（事实）**

- **测试库隔离两道闸**：`conftest.py` session 级 autouse 判定（`interview_agent_` 前缀 + `_test/_regression/_mig` 后缀或 `interview_agent_v21_` 豁免 + 黑名单 + `DATABASE_URL == M1_TEST_DATABASE_URL`，否则 `pytest.exit(2)`；9 条离线判定表）；`scripts/run-workbuddy-tests.py` 在运行前拒绝对生产库/E2E 库/历史 Live 库**指向**，建库但从不 drop，只迁移目标库，并把同一 URL 同时给两个环境变量。
- **归属顺序**：`build_context` 先解析来源与归属（`JOB_SOURCE_CONFLICT` / `JOB_SOURCE_REQUIRED` / 统一 `JOB_TARGET_NOT_FOUND` 404），再查画像；存在性预言机消除，正常用户仍 `PROFILE_REQUIRED`；会话创建与训练入口共用。
- **私人 JD 训练闭环（无迁移）**：`build_context` 早已支持 `job_target_id` 并写入冻结快照，缺的只是投影与入口。workbench/feedback 增加 `job_target_id` 与 `source_label`，`company_id/job_id` 改为可空；训练页支持私人岗位下拉 + `?job_target_id=` / `?session_id=`（从该场快照解出目标与 Skill，不读可变的 JD），无画像时给上传简历入口。
- **FULL 已证**：私人 FULL 会话快照冻结 `job_target.version=1`；用户随后改要求并重新确认到 v2，该会话 `context_snapshot` 逐字节不变；`companies/jobs/job_description_versions/job_requirements` 四表计数不变（不产生公共数据）。
- **WEAKNESS 已证**：全新用户在私人目标下 `available_modes` 全为 `FULL`，直接建 WEAKNESS 被 `TRAINING_TARGET_INVALID`(422) 拒绝；跑完一场私人 FULL 产生 1 条正式评价后，面板出现可用弱项目标，创建成功且 blueprint 收敛到该单一目标。
- **公开岗位训练无回归**：同一用户公开入口仍 `job_target_id=None`、`source_label=None`、原 `company_id/job_id`。

**测试（真实计数，分列不混合）**

- 全量后端 @ `interview_agent_wb_r10_test_20261112`（0027，67 表）：**757 passed / 10 failed / 1 skipped**（246.95 s）。
- 10 个 failed 全是 `test_00`–`test_09` 迁移用例：承载数据的库不是空库，且 `test_09` 强制库名前缀 `interview_agent_v21_`。在专用空库 `interview_agent_v21_wb_r10_20261112`：**10 passed / 0 failed**（64.52 s）。**0 个失败落在业务代码上。**
- 受影响子集（私人训练 + 岗位 + 训练 + 隔离闸）：**31 passed**（27.85 s）；运行器冒烟 13 passed。
- 前端 `npm run build`（`vue-tsc && vite build`）通过，0 类型错误。
- `scripts/run-workbuddy-tests.py` 拒绝路径实测：`interview_agent`、`..._r10_e2e_...`、`interview_agent_thinking_long_20261110` 三种名字均被拒。

**数据库（全部名字与用途）**

| 库 | 用途 | 状态 |
|---|---|---|
| `interview_agent` | 生产 | 未被任何测试/迁移连接 |
| `interview_agent_wb_r10_test_20261112` | 后端回归 | 0027 / 67 表 |
| `interview_agent_v21_wb_r10_20261112` | 迁移用例（空库） | 跑完停在 0027 / 67 表，不能二次跑迁移用例 |
| `interview_agent_wb_r10_e2e_20261112` | 浏览器验收（预留） | 0027 / 67 表 / `companies=0`，本轮未写入 |
| 历史 Live 库 | 封存 | 只读 |

**未验证 / 遗留（事实）**

- **未操作浏览器**：私人 JD → 训练 → 报告链路只有构建与读码验证；界面类修改同理。
- 私人 `RECHECK` 未验证（造不出 `CONFLICTED`）。
- 闸门未覆盖 `delivery.py migrate`、启动脚本、`prepare_v23_demo.py`。
- 能力记忆按 `(user, topic)` 聚合，私人与公开训练共用一个根，无法按岗位区分。
- 私人目标下没有 Skill 推荐接口（后端有 `recommend_for_target`，前端只能手选；页面措辞已按"请你选择"写）。
- `training_mode` 单独出现（无 `training_request_key`）会绕过 `TrainingService.create` 的幂等。

**下一轮入口**

1. `/training` 的私人链路做一次**真实浏览器**验证（需要前端 + API 起在隔离库；训练首题会触发真实 HTTP，须先批额度或用离线 provider）。
2. 把库名闸前移到 `delivery.py migrate`。
3. 请求记录 ↔ 业务审计三向 join 的最小方案（`docs/89` §10.1）：`agent_runs.call_key` + `provider_request_id`、`ModelResponse.request_id`、capture 行写 `call_key`。历史批次不得标成"可精确回放"。
4. 私人 `RECHECK`：用离线 provider 造两条相反评价，把它从"未验证"变成"有证据"。

### 2026-11-12/13 · 第十一轮（测试分库 + 私人 JD 真实 UI 验收 + RECHECK）

开工 `1ba948b`，工作区干净。真实模型 HTTP = **0**；未迁移生产库；未新增 Agent、未重构 Controller / Memory。

**提交**

| Hash | 内容 |
|---|---|
| `be1407e` | `test(workbuddy)`: 数据库目标分类收敛到一处 + 两段式运行器 |
| `275343e` | `fix(workbuddy)`: 运维迁移必须显式写出库名 |
| `1e24814` | `test(workbuddy)`: 私人 RECHECK 的真实准入证据（5 条） |
| `ccd7ff8` | `fix(workbuddy)`: 报告页不再把用户自填公司名当雇主展示 |
| `b0620de` | `test(workbuddy)`: 离线验收启动器 |
| `6d2e155` | `docs(workbuddy)`: `docs/90` 十节记录 |

**已完成（事实）**

- **两类测试分库**：`db_targets` 九类判定（PRODUCTION / ARCHIVED_LIVE / E2E / UI / DEMO / MIGRATION / TEST / SYSTEM / OTHER），pytest、运行器、delivery、夹具脚本共用。运行器两段：业务段 `interview_agent_wb_r11_test_20261112`（自动迁移），迁移段独立空库；空库校验查 `information_schema.tables`，非空即拒，**从不 drop/清空**。
- **环境性失败清零**：`782 passed / 0 failed / 1 skipped`（业务 772+1skip，迁移 10），用例总数 768 → 783，没有删除或跳过任何既有用例。
- **运维保护**：`delivery.py migrate` 对生产/E2E/未知库要求 `--authorize-database <同名>`（exit 3），系统库与历史 Live 库无覆盖；启动脚本传 `-AuthorizeDatabase` 才转发；`prepare_v23_demo.py` 只接受 `*_browser_*`。`interview_agent_thinking_long_20261110` 因名字无 live/accept 曾被旧规则漏判，现归入 `ARCHIVED_LIVE`。
- **RECHECK 准入（事实）**：`CONFLICTED`（同一 scope 先独立好、后不达标且未被 ≥2 次独立复测解决）或 `RECHECK_DUE`（VERIFIED 且距上次独立验证超过 30/60/90 天）；两者都只给 FULL+RECHECK，`gentle` 风格 422。测试用 `GOOD,GOOD,WEAK,GOOD,WEAK` 造出冲突；过期用服务 `clock` 前移 120 天验证（观察仍是真实评价）。
- **真实浏览器（Chrome 157 / CDP / headless）**：10 步全走通，14 张截图在 `.runtime/ui/shots/`；API 用 `serve_offline_acceptance.py`（同一应用 + replay provider + 阻断外呼）。RECHECK 页显示「缓存工程 · 表现存在分歧」与"第一题先独立作答"；改 JD 到 v2 后会话快照仍是 v1、`company_id/job_id` 仍 null；第二用户 404/0 条。
- **UI 缺陷（仅靠操作页面才发现）**：报告页标题把用户自填的"示例科技"当雇主展示，无来源说明。修法：报告详情补 `job_target_id`/`job_target_version`，页头写明版本与"未经企业核实"。

**未验证 / 遗留（事实）**

- 简历上传 → 解析 → 画像未走真实链路（需 Worker + 模型）；画像是验收启动器用 replay 预置的真实版本。
- 私人入口只在"打开目标"后出现，列表行没有直达入口（未改，属可发现性而非误导）。
- 记忆仍无岗位维度；`training_mode` 缺 `training_request_key` 时绕过幂等（契约风险未改）。
- CDP 脚本与截图在 `.runtime/`（gitignored），仓库内只有可复用的 `serve_offline_acceptance.py`。

**数据库（本轮）**

| 库 | 用途 | 状态 |
|---|---|---|
| `interview_agent_wb_r11_test_20261112` | 业务回归 | 0027 / 67 表 |
| `interview_agent_v21_wb_r11_20261112` | 迁移第一遍 | 跑完 0027，已非空不可复用 |
| `interview_agent_v21_wb_r11b_20261112` | 迁移第二遍 | 同上 |
| `interview_agent_wb_r11_e2e_20261112` | 浏览器验收 | 0027，2 用户 / 1 私人目标 / 14 会话 |
| `interview_agent` | 生产 | 未连接、未迁移 |

**下一轮入口**

1. `delivery.py migrate` 的授权用法要写进 README/团队说明——现在不传参数会拒绝启动生产库。
2. 私人岗位的 Skill 推荐接口（后端已有 `recommend_for_target`，未暴露）。
3. 简历上传链路（需 Worker + 额度）与私人入口可发现性。
4. 请求记录 ↔ 业务审计三向 join（`docs/89` §10.1）等批准。

### 2026-11-13 · 第十二轮（训练创建幂等 + 列表入口 + Coach 设计审查，0 次真实 HTTP）

开工 `68d21f0`，工作区干净。真实模型 HTTP = **0**；未迁移生产库；未连接历史 Live 库；
Coach 方案**只审查不实施**。

**提交**

| Hash | 内容 |
|---|---|
| `221fb28` | `test(workbuddy)`: 子集运行不再因惰性 mapper 解析失败 |
| `4a25891` | `fix(workbuddy)`: 一次训练点击只创建一个会话（有键无键都成立） |
| `80149e9` | `feat(workbuddy)`: 已确认 JD 的列表行直接进入训练 |
| `8f1c744` | `docs(workbuddy)`: 启动迁移授权写进 README |
| `f1aa960` | `docs(workbuddy)`: `docs/91` 十节记录 |

**已完成（事实）**

- **幂等根因是路由条件**：`MockInterviewService.create` 原来只在 `training_mode and training_request_key` 时进 `TrainingService`；缺键时走普通路径，但 `build_context` 仍冻结 `training` 块，于是创建的会话"看起来是训练、实际无锁无去重"。改为只看 `training_mode`，缺键在开库前 `422 TRAINING_REQUEST_KEY_REQUIRED`（不生成服务端键，避免"看起来幂等"）。
- **语义**：同键同内容 → 同 id；同键异内容 → 409；新键 → 新会话；不同用户同键 → 互不影响；并发同键 → 一条记录（`pg_advisory_xact_lock(2300, uid)`，事务级、跨进程）；进程重启 → 同 id（状态只在库里）。训练创建本身 0 次模型调用。
- **已证伪**：把路由条件改回旧写法，只有"缺键"那条失败，其余 9 条通过——正是缺陷形状。
- **无 Schema 迁移**：去重靠既有 JSON 列 + advisory lock；补唯一索引的条件已写进 `docs/91` §3。
- **列表入口**：已确认行给「开始训练」，未确认行给原因（「确认 JD 后可开始训练（当前为待解析）」）而不是按钮；真实 Chrome 验证点一次即到 `/training?job_target_id=1`，未确认目标服务端返回 409 `JOB_TARGET_NOT_CONFIRMED`。
- **README**：按库类型说明授权、给隔离开发库完整示例、明确 `.env` 不是授权、封存库无覆盖。实测 `start-dev.bat` 对 `*_e2e_*` 库输出 `MIGRATION_REFUSED` + `Delivery check failed: migrate (exit 3)`，未启动任何服务。
- **Coach 审查（只读）**：它是单次结构化调用，无工具通道；评价 catalog 固定且有损（答案 600 字、评价 800 字、缺失点 4 条）；**记忆输入是 `str(dict)` 截断到 600 字符**（用 `project_snapshot` 直接验证），且 `memory_guided_plan` 不在投影白名单里（被记进 `omitted`）。`MemoryGuidedPlan` 是 `capability_rules.memory_plan()` 纯规则，模型零参与。
- **方案（未实施）**：Phase 0 先修输入形状（结构化记忆 + `capability_snapshot` + `training_context`，零模型成本）；Phase 1 两个只读工具 `read_evaluation_detail`（本场、解裁剪）与 `read_capability_history`（同用户、只返回状态与引用、不返回原文）；工具 1 次、模型 2 次封顶；`suggested_training` 由 resolver 按 `project(states)` 再校验，存进 `coach_analysis_json`（无需迁移）。

**测试（真实计数，分列）**

- 后端全量业务段 @ `interview_agent_wb_r12_test_20261113`（0027/67 表）：**782 passed / 0 failed / 1 skipped**（245.75 s）。
- 迁移段 @ 空库 `interview_agent_v21_wb_r12_20261113`：**10 passed / 0 failed**（54.83 s）。
- 训练/岗位/记忆/隔离子集：**87 passed**；幂等专项 10 passed（旧条件下 1 failed）。
- 前端 `npm run build`（`vue-tsc && vite build`）通过，0 类型错误。
- 浏览器：真实 Chrome headless + CDP，10 步（`docs/91` §5），4 张截图。

**顺带修掉的测试基础设施缺陷（事实）**

`test_workbench_first_visit_...` 在整包运行通过、单独跑文件失败（`NoReferencedTableError: user_job_targets`）——SQLAlchemy 首次配置 mapper 时按表名解析外键，取决于哪个测试模块先被导入。conftest 里显式导入该模型模块后消失。生产不受影响（`main.py` 导入全部 router）。

**未验证 / 遗留（事实）**

- 并发 `start` 最坏浪费一次模型调用（业务状态只写一次，普通面试同样如此，本轮未改）。
- 幂等保证来自锁而非数据库约束：新增加绕过 `TrainingService.create` 的创建路径会复发。
- 从列表进入训练后仍需手动选一次专业方向（私人 JD 无自动推荐，第十轮决定）。
- 记忆无岗位维度；Coach 记忆输入形状缺陷（Phase 0 待批准）。
- 简历上传 → 画像链路本轮未走（replay 预置）。
- 并发测试是单进程 3 协程，多进程争用只有锁语义保证，无实测。

**数据库（本轮）**

| 库 | 用途 | 状态 |
|---|---|---|
| `interview_agent` | 生产 | 未连接、未迁移（拒绝路径已实测） |
| `interview_agent_wb_r12_test_20261113` | 业务回归 | 0027 / 67 表 |
| `interview_agent_v21_wb_r12_20261113` | 迁移空库 | 跑完 0027，不可二次跑迁移用例 |
| `interview_agent_wb_r12_e2e_20261113` | 浏览器验收 | 0027 / 2 用户 / 2 私人目标 |
| 历史 Live 库 | 封存 | 只读，未连接 |

**下一轮入口**

1. **Coach Harness 方案等审批**（`docs/91` §8）：Phase 0 输入投影 → Phase 1 两个只读工具 → `suggested_training` 受控校验。不要直接开工。
2. 私人岗位 Skill 推荐接口（后端 `recommend_for_target` 已存在，未暴露）。
3. 简历上传链路（Worker + 额度）。
4. 请求记录 ↔ 业务审计三向 join（`docs/89` §10.1）。

### 2026-11-13 · 第十三轮（Coach Harness V1：Phase 0 结构化输入 + Phase 1 受限工具，0 次真实 HTTP）

开工 `f14f755`，工作区干净。真实模型 HTTP = **0**；未迁移生产库；未连接历史 Live 库；**未新增 Agent、未改 Controller、未改 CapabilityMemory**。

**提交**

| Hash | 内容 |
|---|---|
| `790913e` | Phase 0：`COACH_EVIDENCE_V2` 结构化记忆投影 + 9 条测试 |
| `4e726f9` | Phase 1：两个只读工具、`suggested_training` 校验、审计、前端兼容 + 17 条测试 |
| `022dcdd` | `docs/92` 十节记录 |

**已完成（事实）**

- **Phase 0 根因**：`project_snapshot` 对 `approved_memory_context`/`recent_performance_observations` 用 `_clip()`（dict 先 `str()` 再截 600 字符），`memory_guided_plan` 直接不在白名单被丢弃。V2 改为结构化：两个记忆层带 `semantics`/`epistemic_status`/`is_stable_fact`/`items[≤8]`/计数；计划变 `capability_context`（state/priority/needs_recheck/memory_revision/refs≤3/history_summary，含 `scope_note` 明写"跨岗位聚合，不是本岗位掌握度"）；训练范围变 `training_context`；每条评价加裁剪前算的 `fingerprint`。
- **体积代价（实测）**：真实 18 轮夹具 85,047 → 86,830 字节（+1,783，+2.1%）；40 条记忆只多 ~800 字节（8 条上限）。
- **Phase 1 门控**：`interview_coach_harness` 默认 **False**。OFF 的提示词 1.0.3、`CoachArtifact` schema、生成键逐字节不变（有用例断言 `prompt_version == "1.0.3"` 且无 `audit` 键）。ON 用两条 1.1.0 提示词（plan/final），最多 1 次工具 + 2 次模型调用，第二次 schema 强制终稿。
- **工具只有两个、都只读**：`read_evaluation_detail`（仅本场 catalog；重算 `fingerprint` 与冻结值比对，不符返回 `EVALUATION_CHANGED`；≤4KB；不返回 `reference_answer`）；`read_capability_history`（**不查库**，读尝试开始时的一次性快照；仅 blueprint 内的 knowledge_id；只返回状态/修订/冲突/引用，≤5 条观察、≤1.2KB、无答案原文；空历史返回 `EMPTY`）。
- **只在确有缺失时提供工具**：`clipped_text_fields > 0` 才给评价工具，有记忆行才给历史工具；两者皆无 → 单次调用、schema 直接终稿。
- **建议校验复用训练规则**：`resolve_coach_artifact` 用 `interview.training.modes()`（与 `TrainingService` 同一函数）判定模式合法性，非法建议**降级**为 `suggested_training_rejected`，不拖垮 findings/statistics。
- **冻结两层且标注**：快照里的 `memory_guided_plan` = 会话起点冻结（`..._FROZEN_AT_SESSION_START`）；报告期快照 = 尝试开始时读一次（`CAPTURED_AT_REPORT_GENERATION_NOT_THE_SESSION_SNAPSHOT`，带内容 `fingerprint` 与逐条 `revision`）。工具步骤 `coach_tool` 记录参数/结果/耗时/能力指纹 → 可查"模型当时看到了什么"。
- **测试发现的两个真实缺陷**：历史工具写成同步函数却 `await`（每次工具调用 TypeError）；工具白名单取自计划里"被优先"的点而非冻结 blueprint（合法知识点被误判越权）。都是先测出来再修。

**测试（真实计数）**

- 全量业务段 @ `interview_agent_wb_r13_test_20261113`：**808 passed / 0 failed / 1 skipped**（227.61 s；上一轮 782，+26 = Phase 0 九条 + Phase 1 十七条）。
- 迁移段 @ 空库 `interview_agent_v21_wb_r13_20261113`：**10 passed / 0 failed**（53.75 s）。
- Coach 相关子集：**92 passed**（51.47 s）。
- 前端 `npm run build`（`vue-tsc && vite build`）：通过，0 类型错误。

**未验证 / 遗留（事实）**

- **没有成对真实模型输出**，所以不能声称 Phase 0 或 Harness 提升报告质量；只证明"模型第一次拿到了此前拿不到的数据"且无回归。
- 真实 provider 下 `CoachTurn`（判别式联合）首轮 JSON 合法率未知，偏低会吃掉第二次调用额度。
- 用工具的报告约为 `2 × payload` 输入成本（18 轮夹具 ≈ +87KB），真实 token 未计量。
- `generation_key` 未包含能力快照指纹：记忆变化不会让已完成报告失效，只在审计里可见（是否要改是一个产品决策，已列为 `docs/92` §10.6）。
- 建议合法性只做了 `modes()` 的单元覆盖，未在真实 `CONFLICTED`/`RECHECK_DUE` 用户上端到端跑。
- 前端只加了报告页一句建议 + 预选链接，没有工具轨迹页。

**数据库（本轮）**

| 库 | 用途 | 状态 |
|---|---|---|
| `interview_agent` | 生产 | 未连接、未迁移 |
| `interview_agent_wb_r13_test_20261113` | 业务回归 | 0027 / 67 表 |
| `interview_agent_v21_wb_r13_20261113` | 迁移空库 | 跑完 0027，不可二次跑迁移用例 |
| 历史 Live 库 | 封存 | 只读，未连接 |

**下一轮入口**

1. **真实 Live 短程验收（等授权）**：同一场会话跑 OFF / Phase 0 / Harness ON 三组，量工具使用率、首轮 JSON 合法率、token、延迟，并人工看建议是否指向真正的机制。
2. 私人岗位 Skill 推荐接口（后端 `recommend_for_target` 已存在，未暴露）。
3. 简历上传链路（Worker + 额度）。
4. `generation_key` 是否纳入能力指纹：需要一次显式设计（会改变"已生成报告被复用/顶替"的语义）。

### 2026-11-13 · 第十四轮（Coach Harness V1 真实 Live 短程对照，3 次 HTTP / 77,110 token）

开工 `ee8d3b3`，工作区干净。未新增 Agent、未改 Controller / CapabilityMemory / 训练状态机；生产库与历史 Live 库均未写入。

**提交**

| Hash | 内容 |
|---|---|
| `b7a9403` | `fix(workbuddy)`: 生成键覆盖 Harness 自身的输入（工具策略 + 能力捕获） |
| `98c9e35` | `fix(workbuddy)`: 报告 → 训练链接不再丢失岗位 |
| `40ada98` | `docs(workbuddy)`: `docs/93` 十节记录 |

**场景（事实）**

- 来源 `interview_agent_wb_full_live_20261111` 会话 2：18 条真实评价、`completion_reason=TOKEN_LIMIT`（预算 20 / 已答 18）、综合 4.0667/5；3 条能力记忆全部 PRACTICING、0 冲突；唯一历史报告是 11-11 的 FAILED（`MODEL_USER_QUOTA`）。
- 副本 `interview_agent_wb_r14_real_20261113`（TEMPLATE 复制 + 0026→0027 显式授权迁移）；原件复核未变。
- 场景 A（证据充分 → 直接 Final）**真实覆盖 2 次**；场景 B（裁剪→查评价）与 C（冲突→查历史）**未由真实模型触发**（只裁剪 1 个字段；归档无 CONFLICTED）。不伪造状态补齐。

**真实调用（全部 SETTLED，无 UNKNOWN）**

| 运行 | HTTP | in/out | 合计 | 延迟 | 结果 |
|---|---|---|---|---|---|
| OFF（gen 1, 1.0.3） | 1 | 23,115 / 1,987 | 25,102 | 12,822 ms | COMPLETED，无建议 |
| ON #1（gen 2, 1.1.0） | 1 | 24,272 / 1,756 | 26,028 | 11,227 ms | `SINGLE_CALL_MODEL_CHOSE_FINAL`，建议 WEAKNESS/agent |
| ON #2（gen 3，新请求键） | 1 | 24,272 / 1,708 | 25,980 | 11,120 ms | 同上路径，建议 WEAKNESS/rag_retrieval |
| OFF 复跑 | **0** | — | 0 | — | 命中旧键，`reused=true` |

- 预算：3/6 次 HTTP，77,110/180,000 token，`structured_retry=0`。预热阶段两次脚本级失败**未发出请求**（当日准入计数为 0，无新报告/无新 run）。
- 质量：引用全部真实存在（14/17/15 个去重评价），无跨岗位掌握度断言，无无依据数字；建议两条都经 `training.modes()` 复核并带 `state`/`available_modes`。
- **未证明**：工具在真实模型下的价值（0 次调用）；Phase 0 结构化记忆被用上（三份正文都没引用跨场次状态，"历史"仅指"历史分位数"）；建议稳定性（两次指向不同知识点，都合法）。

**浏览器（真实 Chrome headless + CDP）**

报告页出现"下一场可以练什么"与"不是已排好的训练"说明；链接为 `/training?mode=WEAKNESS&knowledge_id=…&job_id=1`；训练页岗位/方向/模式/知识点全部预选，跳转前后会话数 3→3（未自动创建）；点历史"第 1 版"（OFF 报告）无建议块。截图 `r14_01`–`r14_04`。

**测试（真实计数）**

- 业务段 @ `interview_agent_wb_r14_test_20261113`：**818 passed / 0 failed / 1 skipped**（246.80 s；上轮 808，+10 = 生成键 8 + 报告身份 2）。
- 迁移段 @ 空库 `interview_agent_v21_wb_r14_20261113`：**10 passed / 0 failed**（56.29 s）。
- 前端 `vue-tsc && vite build` 通过，0 类型错误。

**未解决风险**

1. 工具路径在真实模型下零覆盖；要取证需"证据确实不足"的会话或真实 CONFLICTED 记忆。
2. 能力记忆无岗位维度，`capability_context` 是跨岗位按 topic 聚合。
3. 建议在多个同等弱项间不稳定（两次运行给不同目标）。
4. ON 的键现在含能力指纹 → 记忆一变就新代次（GET 不触发生成，故不会误生）。
5. 单会话单归档、样本量 1，不能推广为"ON 更好"。
6. 私有 JD 会话本轮未做 Live 对照。

**下一轮入口**

1. 找一个投影裁剪严重的真实会话（或合法构造高裁剪输入的离线夹具），验证"模型会不会查、查了是否改变结论"。
2. 或先补一条真实 `CONFLICTED` 历史（需真实重测），再验证"查历史是否改变建议"。
3. 在上述任一证据出现前，不要把 Harness 设为默认开启。

### 2026-11-13 · 第十五轮（Coach 工具查询必要性审查，2 次 HTTP / 10,505 token）

开工 `7e5e2d7`，工作区干净。未改 OFF 的 Prompt/Schema/生成键，未新增工具或轮次，未改 Controller / CapabilityMemory，正式库与归档 Live 库未写入。

**提交**

| Hash | 内容 |
|---|---|
| `6d65a33` | `fix(workbuddy)`: 两个工具改为只返回增量 + 修字节上限 + 4 条专项测试 |
| `7631d38` | `docs(workbuddy)`: `docs/94` 十节记录 |

**零成本审查（真实 18 条评价归档）**

- V2 已给出题目全文、五维分数、答案前 600 字、评价前 800 字、四类要点前 4/4/3/3 条。真实会话里**只隐藏 34 字符自由文本 + 32 条列表项**。
- 修复前 `read_evaluation_detail`：第 18 条答案 401 字 < V2 的 601 字、focus 1 条 < 3 条、整个结果 4,199 字节——4KB 大半重复 `question/scores/question_kind/session_question_id`。**"详情"工具比投影给得还少。**
- 修复：只返回 `answer_hidden`/`evaluation_hidden` 尾部、`extra_*` 列表项，带 `hidden_counts`/`shown_by_projection`/`no_additional_evidence`；整个封装 ≤4KB，裁剪不低于 V2。同一条评价结果 4,199 → **1,411 字节**。历史工具的 1,214 > 1,200 越界（只量 data、循环后追加 `truncated`）也修掉。
- 第十四轮真实会话的 `capability_context` **0 条 items**（冻结计划为空），但 `training_scope` 已有状态和合法模式 → 历史工具新增的是引用明细，不是新决策依据。

**离线场景（0 次真实调用）**

- **B 合成**：`LongEvaluationProvider` 经正式 Service 造出 940 字评价，决定性事实放在 800 字裁剪点后与第 6 条 missing_points。V2 确实遗漏；工具返回该事实；指纹一致；改指纹 → `EVALUATION_CHANGED`；越界 → `EVALUATION_OUT_OF_SCOPE`；无 `reference_answer`/其他用户数据。
- **C 合成压力测试**：`GOOD,GOOD,WEAK,GOOD,WEAK` 五场正式 replay 会话 → 合法 `CONFLICTED`。V2 计划已给 state/needs_recheck，`modes()` 已决定 FULL+RECHECK；历史工具只加观察引用。resolver 接受 RECHECK、拒绝 WEAKNESS。**不称为真实用户历史，且未做 Live。**
- **确定性基线**：`deterministic_suggestions(training_scope, evaluations)` 零模型调用，产出合法候选项；第十四轮真实会话给 3 个 WEAKNESS（含真实 ON 两次选中的 agent / rag_retrieval），CONFLICTED 场景给 RECHECK。

**小规模真实 Live（仅 B）**

| 运行 | HTTP | Token | 延迟 | 首轮 | 工具 |
|---|---|---|---|---|---|
| OFF 1.0.3 | 1 | 4,600 | 5,173 ms | — | — |
| ON 1.1.0 | 1 | 5,905 | 6,700 ms | `SINGLE_CALL_MODEL_CHOSE_FINAL` | **0** |
| 合计 | **2/4** | **10,505/150,000** | | | **0/2** |

- 两个工具都可用、决定性证据被刻意藏起来，模型**仍直接终稿**；连同第十四轮累计 **0/4** 次工具调用。
- 两份产物引用 `[1]`、无无依据数字、无跨岗位断言；隐藏事实未被使用，但**也没编造"已完全掌握"**（ON 从 V2 可见的 missing_points 推断出方向相近的缺口）。
- **ON 的 WEAKNESS 建议被 resolver 拒绝**（CONFLICTED 只允许 FULL/RECHECK），确定性基线却给出合法 RECHECK → 本场景 Harness 建议质量**低于**零成本基线。
- ON 固定开销 +1,305 token（小 payload 下 **+28%**；第十四轮大 payload +3.5%）。
- 准入：2 条 `SETTLED`、actual=charged、无 UNKNOWN/RESERVED、无 QUOTA、`structured_retry=0`；正式库当日准入 0。

**测试**

- 专项 **4 passed**；Coach/训练/报告/幂等子集 **105 passed**。
- 全量业务段 @ `interview_agent_wb_r15_test_20261113`：**822 passed / 0 failed / 1 skipped**（272.68 s）；迁移段 @ `interview_agent_v21_wb_r15_20261113`：**10 passed / 0 failed**（63.59 s）。用例 818 → 822。
- 前端 `vue-tsc && vite build` 通过（本轮无前端改动、无浏览器 UI 验收）。

**结论（写进 `docs/94` §9）**

1. 工具各自能给的增量：评价工具能返回 V2 裁剪外的文本尾部与额外要点；历史工具只给引用明细，合法模式早已由状态与 `modes()` 决定。
2. 真实模型**没有**自主选择调用（0/4）。
3. 调用未改善建议（没有调用；ON 的建议还被拒绝）。
4. **确定性规则基线已能满足主要需求**——结构化建议有价值，但不来自工具调用。
5. **暂时搁置 Harness，保持 `INTERVIEW_COACH_HARNESS=false`**；不再改 Prompt 诱导调用、不加工具、不加轮次。若要把建议做成产品特性，走规则生成 + 现有 resolver 校验，单独审批。
6. 下一阶段**不转 Research / Matching**（都会扩 Agent 架构且无用户侧证据），转向普通用户闭环：① 简历上传→画像真实链路 ② 私人 JD 的 Skill 推荐接口（后端已有 `recommend_for_target`） ③ 能力记忆岗位维度 ④ 请求记录↔业务审计 join。

**未解决**：结论样本量小（1 个真实归档会话 + 1 个合成场景 + 2 次调用）；工具新形状只在 ON 路径；确定性基线仍是测试内实现、未接入产品；记忆无岗位维度；本轮无浏览器验证。

### 2026-11-13 · 第十六轮（私有 JD Skill 推荐 + 确定性训练候选 V1，0 次真实 HTTP）

开工 `6cf082f`（工作区仅 `.workbuddy/` 未跟踪）。未改 Harness OFF 的 Prompt/Schema/生成键，未改旧报告、Memory 核心聚合或报告 Schema；**零迁移**。

**提交**

| Hash | 内容 |
|---|---|
| `f611117` | `feat`: 私有 JD 的只读 Skill 推荐投影（逐字证据 + 输入指纹 + 不确定性） |
| `e86e4e2` | `feat`: 确定性训练候选（正式状态 + `training.modes()` + 可复核排序，不写库） |
| `7cd1275` | `test`: 18 条专项 |
| `ed03f56` | `feat`: JD/面试页展示推荐与一键预选；手动选择不被覆盖 |
| `70b73ab` | `feat`: 训练中心/报告页候选与来源标注 |
| `1904edc` | `docs/95` 十节 |

**复用与缺口（事实）**

- 已有 `recommend_for_target` 是纯确定性（权重 category 10 / title 8 / responsibility 6 / capability 3，`MIN_SCORE=9`，同分按 `skill_id`）；`discover()` 只收合法注册版本。本轮不改排序，只补：只读接口、确认校验、逐字证据投影、输入指纹。
- **缺口**：① 无查询接口 ② `evidence` 只有 `statement[:200]`，requirements 命中会被 `hits[:3]` 截掉 ③ `content_fingerprint` 只算 `raw_text`，编辑要求不变 ④ 前端私有路径写死"手动选择"。
- 最小修复：`recommend(..., full_evidence=False)` 仅供私有投影取全量命中（默认信封不变）；`target_skill_input_fingerprint` 覆盖 `version + title + requirements + responsibilities`；未确认 JD 不运行匹配。

**训练候选**

- `build_training_candidates(views, states)` 纯函数：`available_modes` 直接来自 `training.modes()`；排序 `STATE_PRIORITY*10 + min(target_priority,4)`，同分 `(knowledge_id, mode)`；反向输入顺序不变。
- 正式引用 `evaluation:<id>`、`memory_revision`、`observations`、`conflicts`；跨岗位标 `CROSS_JOB_TOPIC_AGGREGATE` + `general_only`；空原因区分 `NO_HISTORY_EVALUATIONS` / `NO_ACTIONABLE_WEAKNESS_OR_RECHECK` / `TARGET_EXPIRED_OR_UNAVAILABLE`。
- `TrainingService.candidates` 与 `workbench` 同样 `build_context` 预览 + `rollback()`；`session_id` 从冻结快照取来源与 Skill；点击候选只预选，创建仍走 `training_request_key` 幂等 + `freeze_training` 重校验。

**浏览器（真实 Chrome 157 headless / CDP 9222；离线 API 8000；Vite 5173；E2E 库 `interview_agent_wb_r16_e2e_20261113`）**

1. 粘贴 JD → 解析 → 确认 `Java后端工程师` v1；详情页显示 `Java 后端开发 v1.0.0`、第 1 位、权重 23、系统首推、规则理由、`JD 原文逐字证据`、无百分比声明。
2. 一键「开始面试」→ Skill 预选 `java_backend`、来源"根据岗位规则自动推荐"；会话 0→0。
3. 手动改 `ai_agent_application`：等待 2s 仍保留（`overwritten=false`）。
4. 一键「开始训练」→ 私有岗位/方向/FULL 预选；候选空态说明；会话 0→0。
5. 1 轮 FULL 作答 → 会话 1 `COMPLETED`；训练页候选 `Java 与并发 · WEAKNESS · 优先级 14 · evaluation:1`；点选后 mode/goal/skill 正确、URL 未变、会话 1→1。
6. 报告页规则区块独立显示"规则生成 · 读取时计算"与"来源不同，二者不等价"；报告存在但 `suggested_training=null`，故模型块未渲染（无建议不显示）。
7. 第二账号：推荐 `404 JOB_TARGET_NOT_FOUND`、目标列表 0。

截图 9 张 `r16_01`–`r16_09`（`.runtime/ui/shots/`，gitignored）。

**测试（真实计数）**

- 专项 18 passed（0 failed）。
- 全量第三轮 @ `interview_agent_wb_r16_full3_test_20261113`：**840 passed / 0 failed / 1 skipped**；迁移 @ `interview_agent_v21_wb_r16c_20261113`：**10 passed**。
- 第二轮曾 839/1/1：唯一失败是 pytest 内 `httpx.ASGITransport` 复用全局 engine 在 Windows 多事件循环下 `NoneType.send`，非产品缺陷；已改为服务层 404 + 路由注册断言，HTTP 跨账号由浏览器覆盖。
- 前端 `vue-tsc && vite build` 通过，0 类型错误；产物含全部来源标注。

**真实模型 HTTP：0**（replay；离线 API 阻断出站）。

**事故与纠正（如实）**：启动器 `api` 模式未采用 `--database`，首次启动连到 `interview_agent`；只有登录查询（401，无写入），立即停止并显式 `DATABASE_URL` 指向 E2E 库重启。

**未解决 / 下一阶段**

1. 真实模型抽取的 JD 与真实用户 `CONFLICTED`/`RECHECK_DUE` 未验证；报告 `suggested_training` 真实样本仍缺。
2. 推荐仍只看 title/职责/要求文本，编辑 requirements 不必然改变结果——用输入指纹识别过期，而不是假装结果会变。
3. 能力记忆无岗位维度，候选只标注跨岗位聚合，不声称岗位胜任度。
4. 维持 `INTERVIEW_COACH_HARNESS=false`；不转 Research/Matching；本轮完成后停止。

### 2026-11-13 · 第十七轮（验收库安全收口 + 简历到私有 JD 主流程回归，0 次真实 HTTP）

**误连生产库的真实根因**：`serve_offline_acceptance.py` 的 `--database` 只建了一个给 `seed/history` 用的一次性 engine；`api` 分支用 `uvicorn.run("interview_api.main:app")` 导入应用，而 `infrastructure/db/engine.py` 在**导入时**就用 `settings.database_url`（`.env` → `interview_agent`）建了全局 engine。守卫校验的字符串与服务实际使用的对象之间没有任何代码路径相连。

**修复**：新增 `interview_api/core/runtime_target.py`（复用 `db_targets.classify`，无第二套命名规则）
- `resolve()` 纯函数：`--database` 必填、必须是 `*_e2e_*`；缺失即拒绝，不回退 `.env`；库名只来自 CLI，host/账号来自 `.env`。
- `apply()` 在建 engine/Redis/Celery 之前写入 `settings` + `os.environ`；环境变量 `DATABASE_URL` 与 CLI 冲突时被覆盖并打印 `NOTICE`。
- `verify_settings/verify_engine/verify_optional_clients` 只读 `engine.url`，不建连接；失败时报告预期/实际/原因，密码用 `hide_password=True`。
- 隔离组：PG E2E 库 + Redis 9/10/11 + MinIO `interview-agent-r17-e2e`；API 与 Worker 由同一命令解析。
- 生产入口未动：`delivery.py config/revision` 正常（`interview_agent` / `0025`）。

**零连接证明**：① `create_async_engine` 与 `asyncpg.connect` 全部换成抛异常后仍能走完 resolve→apply→verify；② 子进程跑真实 CLI（无 `--database`、`interview_agent`、归档库、`postgres`）退出码 3 且 stdout 无 `ACCEPTANCE_TARGET`/`uvicorn`；③ 运行期 `pg_stat_activity` 无生产库连接。

**HTTP 跨账号恢复**：根因是进程级 engine 的池属于上一个事件循环（asyncpg 写入即 `NoneType.send`）。`conftest.py` 新增 `app_engine` fixture，在当前循环内 `dispose()`；测试用真实 `/auth/login` 拿 JWT，B 读 A 的推荐 → `404 JOB_TARGET_NOT_FOUND` 且不含公司/岗位/JD 原文，A → 200，匿名 → 401；未 override `get_db`/`get_current_user`。

**简历链路**：验收栈里是真实上传 → 真实 MinIO → broker(Redis 10) → 真实 Celery worker → Replay 模型 → `CandidateProfileService.persist`；pytest 版走同一上传路由与任务体（broker 一跳替换，文档已标注）。断言画像每条事实都挂 `resume_evidence` 且 excerpt 逐字来自上传文本；重投/旧 task 不产生第二版；失败后重试恰 1 版；B 看不到 A 的简历/画像。

**浏览器（Chrome 157 headless / CDP 9222；E2E 库 `interview_agent_wb_r17b_e2e_20261113`）**：注册→上传→画像（第 1 版 · Replay 回放）→粘贴 JD→解析 4 条→确认第 1 版→推荐 `Java 后端开发 v1.0.0`（第 1 位/权重 12/0 次模型调用/非百分比/逐字证据）→一键预选（会话 0→0）→`+ 新建` 得到 `READY`（skill v1.0.0、job_target v1、AUTO_RECOMMENDED）→B 全空 404→第二个 JD 给出 `AI / Agent 应用开发`→手动选 `bank_it` 等待 5 秒未被覆盖。截图 7 张 `r17_01`–`r17_07`。

**发现并修复的产品缺陷**：`InterviewChatPage` 入口处理器直接读并行的 `listTargets()` 结果，列表先返回空时走"目标不存在"分支导致一键预选丢失。改为 `await privateTargets` + `getTarget()` 兜底（另修一处 TS 收窄）。

**测试**：专项 20 + 4 + 2；全量 @ `interview_agent_wb_r17_full2_test_20261113` **866 passed / 0 failed / 1 skipped**；迁移 @ `interview_agent_v21_wb_r17b_mig_20261113` **10 passed**；前端 `vue-tsc && vite build` 通过。生产库 `revision=0025`、`sessions=19`、最新 2026-10-07、无 `user_job_targets`，本轮未写入。

**未解决 / 下一阶段**

1. 真实模型仍未参与抽取与画像选择；本轮证明链路与权限，不证明质量。
2. 规则对"只改要求文本"敏感性有限，用输入指纹标记过期而非制造变化；更强匹配属 Matching 改造。
3. Celery 未做 worker 崩溃重投的故障注入；Redis 限流键未做"不落生产 db 0"的断言。
4. 能力记忆仍无岗位维度；`suggested_training` 真实样本仍缺；`0028` 未实施。维持 `INTERVIEW_COACH_HARNESS=false`。

### 2026-11-13 · 第十八轮（真实模型输入质量：简历画像 / JD 解析 / Skill 推荐，6 次真实 HTTP）

**调用与闸门**：新增 `apps/api/scripts/live_model_gate.py`（`LIMITS = 8 次 / 150k token / 25 分钟`），
在 provider 边界计数，拒绝时 `request_sent=False`；启动器加 `--live`（仅 JD 抽取、简历抽取、画像选择走真实模型，
面试/Coach 仍 replay）与 `--bucket`。网络改为 transport 级白名单（只允许 modelscope），连 SDK 底层也覆盖。
实测 **6 次 / 11,470 token / 25.8s**，6 条 `model_admissions` 全 `SETTLED`，无 repair、无 UNKNOWN、无配额错误。

**样本**：A（合成 AI Agent 简历）、B（合成易误判简历）、C（真实归档华为 JD 的 579 字逐字切片，
`career.huawei.com/...advertisementId=36390`，17/17 行逐字可核）、D（合成 Java 对照版本）。
标注先于模型调用写定（`.runtime/r18/annotation.json`），锚点 100% 为原文子串。

**质量结论（样本 2 份简历 / 2 份 JD）**
- A：17 技能 / 3 项目 / 19 条证据全被引用，7/7 应有事实被支持，7/7 限定信息保留。
- B：6 技能 / 3 项目 / 1 工作，4/4 支持，5/6 限定信息保留。
- 结构防线有效：3 条技能状态被服务端下调（RAG DEMONSTRATED→MENTIONED；Java/Redis STRONG→DEMONSTRATED），
  Kafka/K8s/Flink/Vue 均停在 MENTIONED，`Go（没有使用过）` 没进技能而是进 unknowns。
- **真实缺陷 1**：第一阶段抽取把 A 的全职工作放进 `internships`、`work_experience` 为空，画像页显示成"实习经历"；
  本轮只记录根因与最小方案，未改抽取行为。
- **真实缺陷 2（已修）**：`update_requirements` 整表重建导致"编辑任意一条要求会清空所有要求的逐字证据"。
  改为按语句匹配保留未改动行的 `evidence/topic/source`，新增行仍无引用；`tests/test_requirement_edit_evidence.py` 3 条回归。
- JD：C 4 条 / D 5 条，全部逐字，`job_title` 正确，`company_name` 为 null 不编造；`REQUIRED` 恒为 0——
  这是 system prompt 的"必须/至少/要求"关键词策略所致，RULE 解析同样如此，属策略缺口而非模型错误。
- RULE vs LLM：LLM 的真正价值是分段去噪（RULE 把"工作地点/城市列表"当要求，2 份 JD 各多 2 条噪声），
  两者在强制/优先判断上一致。
- 推荐：C→`ai_agent_application`(20)，D→`ai_agent_application`(20) 而非 `java_backend`(15)。
  离线只改标题即可翻转（`title='Java 后端工程师'` → java 15 / ai 12），证明**标题 8 分压过要求文本**；
  D 的标题沿用原岗位，属部分样本人为因素，已如实标注。另记 `bank_it` 因"向量数据库"命中"数据库"的 1 处子串误判。
- C 的 v2：`input_fingerprint` 变化、`content_fingerprint` 不变；未改动条目仍 `VERBATIM_FROM_JD`，新增条目为
  `USER_ADDED_NO_VERBATIM_QUOTE`；推荐首位不变。

**浏览器（Chrome 157 / 9222）**：A 注册→上传→画像（DeepSeek 实时模型）→粘贴真实 JD→解析 4 条→确认→推荐
（第 1 位/权重 20/0 次模型调用/非百分比）→编辑要求→v2；B 上传易误判简历；A 从目标岗位进入面试页，
技能预选 `ai_agent_application`，手动改 `java_backend` 3.5s 未被覆盖，会话 0→0。截图 `r18_01/04/05/06/07/08`。
跨账号：B 读 A 的 `resumes/1`、`candidate-profile/versions/1`、`job-targets/2`、`/skill-recommendations`、`/match`
全部 404 且无内容泄露，B 的目标列表 0 条。

**测试**：闸门 7、要求编辑 3、输入指令边界 3、前端入口竞态 7（`node --experimental-strip-types --test`，新 `test:unit`）；
全量业务 `interview_agent_wb_r18_full_test_20261113` **879 passed / 0 failed / 1 skipped**；
迁移 `interview_agent_v21_wb_r18_mig_20261113` **10 passed**；`vue-tsc && vite build` 通过。
E2E 库 `interview_agent_wb_r18_e2e_20261113`（0027），MinIO `interview-agent-r18-e2e`，Redis 9/10/11；
公开表 0 行；生产库仍 `0025 / 19 sessions / 2026-10-07`，未写入。

**未解决 / 下一阶段**
1. 工作经历→实习的归类错误（第一阶段抽取），需按小节标题做一致性核对，单独立项。
2. `REQUIRED` 关键词策略导致真实 JD 全为"原文未标明"，需同时改 prompt 与 rule_extract。
3. 推荐"方向对、依据弱"：逐字要求只占少数证据，标题权重过大；改排序语义需单独立项。
4. 否定式提及仍以"仅提及"技能呈现；画像页空状态文案错误；"向量数据库→数据库"子串误判。
5. 真实模型的失败重试 / repair 本轮 0 次触发，无采集样本。
6. 维持 `INTERVIEW_COACH_HARNESS=false`；未做 0028；未新增 Agent。

### 2026-11-13 · 第十九轮（输入质量三项修复：经历类型 / JD 强度 / 推荐标题偏置，0 次真实 HTTP）

**问题一：全职工作被写进实习 —— 根因不在模型**
定位结论：第一阶段 Prompt 的 JSON 示例与 `ResumeSummary` **只有 `internships`，没有 `work_experience`**，
模型只是把全职经历放进唯一可用的桶；画像侧又不校验"证据所在小节是否与类别一致"。属 Schema/提示词缺陷，不是分类能力问题。
修复：① 补 `work_experience` 并写明"桶名不是雇佣类型，类型由原文小节决定"；
② 新增 `modules/resume/sections.py`（整行标题识别、目录块守卫、未识别短标题闭合小节）与
`modules/resume/employment.py`（锚点回原文投票：公司 3 / 岗位 2 / 其余 2·1，两小节都出现则弃权；平票 `CONFLICT`、无票 `UNRESOLVED`，
一律进 `unclassified_experience` 并留审计块 `employment_kind`）；在 `processor.py` 落库前调用；
③ 画像侧 `normalize_employment_categories` 按引用证据的小节裁决，可移动/丢弃并追加"待确认"话题，无小节简历保留并标未核实；
④ `interview/prompt_builder.py` 分读三类经历，不再把全职介绍成实习。
真实响应 A 重放：`internships` 1 条 → `work_experience` 1 条，11 个锚点全在 `work`（最长 59 字），`work_experience` 不再为空。

**问题二：JD 强度全 UNKNOWN —— 缺的是章节语义，不是关键词不够多**
`jd_section_of()` 用逐字证据定位其所在小节（REQUIREMENT / DUTY / None），`classify_requirement()` 按
"优先措辞 > 强制措辞且不在职责小节 > 任职要求小节 > 职责小节 > 无小节"裁决，并新增 `basis`
（`EXPLICIT_MANDATORY` / `LISTED_UNDER_REQUIREMENTS` / `EXPLICIT_PREFERRED` / `DUTY_SECTION` / `NOT_STATED` / `USER_PROVIDED`），
只存在 `requirements_json`，**无迁移**。关键取舍：**列在任职要求里不升级为 REQUIRED**，保留 UNKNOWN 并标来源。
RULE 走同一裁决，并新增 `location_only()` 过滤工作地点与纯城市列表（不误杀专业列表、"接受出差+地点"）。
实测：C 4 条 → 1 REQUIRED（"至少一种编程语言"）+ 3 LISTED；D 5 条 → 1 PREFERRED + 4 LISTED；
RULE C 3 / D 4 条、地点噪声 0（R18 各多 2 条）。RULE 少一条是"同行分号两句"的切分差异，非强度差异。

**问题三：标题压过要求 —— 阻止了错误自动决策，未改排序**
真实组成：标题 8 分 > 单条要求 6 分，标题 + 任一泛化能力组即过 9 分门槛。修复：
`content_points = points - title`，自动选择要求 `content ≥ 6`；分值领先者内容分低于其他合格候选 →
`AMBIGUOUS` + `TITLE_CONFLICTS_WITH_REQUIREMENTS` + `auto_select=False`；只有标题命中 → `TITLE_ONLY_LEADER`；
跨 Skill 长术语覆盖短术语（"向量数据库"不再给 `bank_it` 加分，3 → 0）；理由（规则）与证据（逐字）分开投影。
实测 D（AI 标题）：ai 20 / java 15 → **AMBIGUOUS，不自动选**；同文本换 "Java 后端工程师" → java 15 首推。
**没有按样本调权重**：排序分值仍是 AI 第一，改的是"不替你决定 + 说明冲突"。

**前端（Chrome 157 / 9222，E2E 库 `interview_agent_wb_r19c_e2e_20261113`）**
工作/实习分栏正确（工作 = 云计算公司后端 2022.07–2025.06；实习 = 电商公司 2021.07–2021.09），"其他经历"未进任一雇佣组；
JD 性质下拉下显示 `basis` 灰字；推荐分"规则理由 / JD 逐字证据"两段；改要求重新确认后重取推荐；
冲突样本无"系统首推"、可手选；旧会话仍冻结原 JD 版本（并修掉私有 JD 显示"岗位描述第 版"的空版本号）。
截图 `r19_01c/02/03/04/05/06/07/08`。

**测试**：新增 `test_employment_kind.py`(18)、`test_requirement_strength.py`(10)、`test_skill_recommendation_controls.py`(10)；
业务全量 `interview_agent_wb_r19_full3_test_20261113` **928 passed / 0 failed / 1 skipped**；
迁移 `interview_agent_v21_wb_r19_mig_20261113` **10 passed**；前端 `test:unit` 7 passed；`vue-tsc && vite build` 通过。
两条既有测试的期望值因行为有意改变而更新（"必须熟悉分布式系统"→REQUIRED；画像提示词 →1.0.5）。
生产库仍 `0025 / 19 sessions` 未写入；`alembic` 仍 0027，未新增迁移。

**未解决**：① 模型是否真会用 `work_experience` 需真实回归（本轮 HTTP=0，服务端只能兜"放错桶"）；
② 标题仍 8 分，要求文本过短时仍可能领先（此时返回 AMBIGUOUS，代价是多点一次）；
③ 长术语覆盖只对"已注册"长术语生效，"图数据库/时序数据库"仍会命中"数据库"；
④ `unclassified_experience` 进得了面试 Prompt，但前端没有独立区块，用户看不到被降级的条目。
