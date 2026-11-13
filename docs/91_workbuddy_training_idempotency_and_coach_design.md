# 第十二轮：训练创建幂等收尾、入口与启动文档、Coach 自主化设计审查

真实模型 HTTP = **0**。未迁移生产库、未连接历史 Live 库、未使用真实用户资料。
本轮新增的浏览器验收全部跑在 `interview_agent_wb_r12_e2e_20261113`（Replay provider + 出站 HTTP 阻断）。

---

## 1. Training 幂等的原始根因

不是"缺一套机制"，而是**一个路由条件**。

`apps/api/src/interview_api/modules/interview/core_service.py`：

```python
async def create(self, user_id, request):
    if request.training_mode and request.training_request_key:   # ← 缺陷
        return await TrainingService(self.factory).create(user_id, request)
    # 否则走普通面试路径
```

`build_context` 在 `request.training_mode` 有值时**无论如何**都会冻结训练块（`freeze_training` 写入 `context['training']`、收缩 blueprint、生成 `memory_guided_plan`）。于是缺键的请求会得到这样一个结果：

| 环节 | 缺键请求的实际行为 |
|---|---|
| 会话行 | 正常创建，201 返回 |
| 快照 | 含完整 `training` 块（mode/targets/before/fingerprint） |
| `training.request_key` | `None` |
| 去重 | 无（没进 `TrainingService`） |
| 并发串行 | 无（没取 `pg_advisory_xact_lock`） |
| 内容指纹冲突检测 | 无 |

也就是说：**创建出来的东西在 UI 上、在报告里、在后续 feedback 里都像一场合法训练，但它在创建那一刻没有任何幂等保护。** 点击两次、超时重试、刷新后重发，就是两条训练记录；而两条记录都能各自被作答、各自产生评价、各自进入能力记忆聚合。

为什么此前没被发现：第一方前端 `TrainingPage.vue` 每次都带 `training_request_key`（沿用 `sessionStorage.training_pending` 里同一签名对应的同一个键），所以 `and` 的两边永远同时成立，分支从来不会落到错误那一侧。这条缺陷只对"只发 `training_mode` 不发 key"的调用方存在——包括任何按公开 schema 写的新客户端。

**这条缺陷是被测出来的，不是被读出来的。** 把条件临时改回旧写法后跑新测试：

```
FAILED tests/test_training_idempotency.py::test_training_without_a_request_key_is_refused_not_silently_created
1 failed, 9 passed
```

其余 9 条在旧代码上通过——因为它们的请求都带键。这个分布正好就是缺陷的形状：带键时一切正常，缺键时才出事。

---

## 2. 修改后的请求键与并发语义

改动两处：

1. `MockInterviewService.create` 改为**只按 `training_mode` 路由**；
2. `TrainingService.create` 在**任何数据库操作之前**校验键，缺键 → `422 TRAINING_REQUEST_KEY_REQUIRED`。

### 2.1 完整语义表

| 场景 | 行为 | 依据 |
|---|---|---|
| 同一个键 + 同样的内容（重复点击、超时重试） | 返回**同一个 session id**，不新增会话/快照 | 按 `(user_id, training.request_key)` 查已提交快照，命中即复用 |
| 同一个键 + 不同内容（改轮数、改模式、改目标、改风格） | `409 TRAINING_REQUEST_CONFLICT`，不写入 | 比对 `training.request_fingerprint`（`digest(request.model_dump())`） |
| 不同的键 + 相同内容（用户确实要再开一场） | 新建一个 session | 键就是"这一次操作"的身份 |
| 缺键 | `422 TRAINING_REQUEST_KEY_REQUIRED`，零数据库写入、零模型调用 | 开库前校验 |
| 不同用户用了同一个键字符串 | 互不影响，各自一场 | 查询以 `user_id` 为前缀条件 |
| 公开岗位训练 / 私人 JD 训练 | 完全相同的语义 | 共用 `TrainingService.create`，差异只在 `build_context` 解析来源 |
| 普通面试（无 `training_mode`） | 不要求键，行为不变 | 路由条件只看 `training_mode` |
| 并发同键 | 只有一个插入，其余返回同一 id | `pg_advisory_xact_lock(2300, uid)` |
| 进程重启后再发同键 | 返回同一 id | 去重状态只存在数据库里，没有进程内缓存 |

### 2.2 并发的真实机制

- 锁是**事务级、数据库级**的：`SELECT pg_advisory_xact_lock(2300, :uid)`，在 `TrainingService.create` 自己的事务里取，`commit`/`rollback` 释放。跨进程有效，不依赖任何单实例状态。
- 锁的粒度是「一个用户的创建操作」，不是全局：不同用户互不排队。
- 等待方在拿到锁之后才执行 `SELECT`；PostgreSQL 默认 READ COMMITTED，每条语句取新快照，因此能看到先提交的那条快照行——不会出现"两个都以为自己是第一个"。
- `SessionController.create` 只做 `flush`，不提交、不调模型。所以整个"取锁 → 查重 → 建会话 → 提交"是一个原子区间。

### 2.3 模型调用与重复业务写入

训练**创建**这一步本身零模型调用（只写会话行与快照）。第一题由 `POST /interview/sessions/{id}/start` 生成，它自己的保护是 `status == READY` + `state_version` 乐观并发：

- 重复调用 `start`（比如创建成功后前端超时重发）：会话已是 `IN_PROGRESS` 且存在 active question → 直接返回该题，**不调模型**；
- 并发调用 `start`：两者都可能先算出首题（各调一次模型），但只有一个能提交，另一个在 `SessionController.start` 里因 `state_version` 不匹配得到 409，**不会产生第二道题、第二次状态写入**。

也就是说本轮保证的是"一次点击一场训练、一道首题"；并发 `start` 最坏浪费一次模型调用，这一点在 §10 里如实列为遗留（普通面试同样如此，不是本轮引入的）。

### 2.4 键的优先级（有意如此）

键的校验发生在所有数据库工作之前，所以一个"既没键、也没开 `adaptive_interview`"的请求会先收到键错误，再收到 `TRAINING_REQUIRES_ADAPTIVE`。这是刻意的：契约错误应当零成本失败，不该先做一次记忆聚合再报错。

### 2.5 前端如何产生键

`TrainingPage.vue`：把 `{source, skill, round, style, count, mode, goal}` 序列化成 `signature`，存入 `sessionStorage.training_pending = {signature, key}`。

- 签名不变（同一次操作的重试）→ 复用同一个键；
- 签名变化（用户改了配置）→ 新键；
- 创建成功后 `removeItem` → 下一次点击自然是新键；
- 创建成功但 `start` 失败 → 键保留，重试会拿回同一场，再补一次 `start`。

---

## 3. 是否需要 Schema 迁移

**不需要。** 本轮零迁移。

理由：

1. 去重查询用的是既有的 `interview_context_snapshots.context_json['training']['request_key']`，不需要新列；
2. 唯一性的保证来自事务级 advisory lock，不是来自唯一索引；
3. 加 `(user_id, request_key)` 唯一索引需要函数索引/生成列（`context_json -> ...` 不能直接进普通唯一索引），是一次真实迁移；在"锁已经给出保证"的前提下，本轮不做。

什么情况下必须补迁移：出现第二条创建训练会话的代码路径（绕过 `TrainingService.create`），或者部署成多个互不相通的数据库而锁失效。届时正确的做法是给 `interview_context_snapshots` 加一个 `training_request_key` 可空列 + 部分唯一索引，而不是继续依赖 JSON 路径。

---

## 4. 公共与私有岗位训练的兼容情况

| 路径 | 状态 | 证据 |
|---|---|---|
| 公开岗位 FULL 训练 | 不变 | `test_public_training_and_a_plain_interview_are_unaffected`：同键两次 → 同一 id，`job_target_id is None` |
| 私有 JD FULL 训练 | 不变 + 幂等 | `test_one_key_returns_the_same_session...`、`test_weakness_creation_is_idempotent_too` |
| 私有 JD WEAKNESS | 幂等 | 同上（先跑一场真实评价解锁合法目标） |
| 私有 JD RECHECK | 幂等 | `test_recheck_creation_is_idempotent_too`（`GOOD,GOOD,WEAK,GOOD,WEAK` 造 CONFLICTED） |
| 普通面试（无训练） | 不变 | 无键要求，`context_snapshot` 里没有 `training` 块 |
| 双用户隔离 | 不变 | `test_two_accounts_never_share_a_key`：同键两个账号各自一场，跨账号读 feedback 得到 404 |

**四个既有测试跟着契约一起改了**（它们写于"键可选"的年代，测的不是键）：

| 文件 | 改动 | 为什么不是"迁就代码" |
|---|---|---|
| `test_v23_training.py::test_training_requires_the_legal_adaptive_scope` | 补键 | 它测的是 adaptive 门槛，不是键 |
| `test_v23_training.py`（非法目标那一处） | 补键 + 断言 `TRAINING_TARGET_INVALID` | 原来只断言"任意 AppError"，补键后必须让断言指名原因，否则它会为错误的理由通过 |
| `test_v23_training.py`（两处 FULL 创建） | 补键 | 同上 |
| `test_director_reference_repair.py` | 补键 | 同上 |

---

## 5. 浏览器端实际验证结果

环境：真实 Chrome（headless，CDP 9222）、前端 `node scripts/serve-web.mjs`（5173）、API 用 `apps/api/scripts/serve_offline_acceptance.py api`（同一 FastAPI 应用、同一服务与事务，仅替换模型边界并阻断出站 HTTP），库 `interview_agent_wb_r12_e2e_20261113`（0027 / 67 表）。

| # | 操作 | 结果 |
|---|---|---|
| 1 | 登录验收账号 | 通过。第一遍运行走了真实表单登录（API 日志 `POST /api/v1/auth/login 200`）；复跑时复用同一 token |
| 2 | 粘贴 JD A（远山数据·平台后端）→ 保存 → 解析 → 确认 | 通过，`parse_status=CONFIRMED v1` |
| 3 | 粘贴 JD B（远山数据·平台前端）→ 只保存不确认 | 通过，`DRAFT v0` |
| 4 | 看列表行 | A 行有 `开始训练`；B 行没有按钮，另起一行写「确认 JD 后可开始训练（当前为待解析）」 |
| 5 | 点 A 行的 `开始训练` | 进入 `/training?job_target_id=1`，下拉已选中该目标，显示「当前目标来自你自己提供的 JD…用户提供（未经企业核实）」 |
| 6 | 未选专业方向时 | 页面提示「请先在上方「专业方向」里选择一个已注册 Skill…」，不是空白页 |
| 7 | 选 Skill 后 | 「这个岗位适合练什么」面板加载出来，目标均为「信息不足 / 只有 FULL」 |
| 8 | 切到「薄弱项专项训练」 | 提示「当前没有符合此模式的有效知识目标。可选择完整模拟面试，不会凭空生成复测任务。」+ 开始按钮 disabled |
| 9 | 服务端校验已确认目标 | `POST /interview/training/workbench` → **200** |
| 10 | 服务端校验未确认目标 | 同一接口 → **409 `JOB_TARGET_NOT_CONFIRMED`** |

截图（`.runtime/ui/shots/`，gitignored）：`r12_01_target_rows.png`、`r12_02_skill_required.png`、`r12_02_training_from_row.png`、`r12_03_focused_mode_reason.png`。

一个只有打开页面才会发现的排版问题：`开始训练` 按钮与"确认 JD 后可开始训练"原本是行内元素，和版本信息挤成一句「第 0 版确认 JD 后可开始训练」。已改成独立一行（`80149e9`）。

**本轮没有验证的**：从列表进入后 Skill 需要手动选一次——这是第十轮的决定（私人 JD 不给自动推荐），不是本轮缺陷，但对用户是多一步操作，列在 §10。

---

## 6. 本地启动文档更新情况

`README.md` 新增「启动脚本与数据库授权」一节，因为第十一轮的闸门让旧说明失效了：旧 README 说"通用启动包含 migration"，现在对正式库会直接停下。

覆盖内容：

1. 三类库的判定表（可自动迁移 / 必须显式授权 / 永不）；
2. `.env` 的 `DATABASE_URL` **不构成授权**；
3. 三个可选路径：`-Demo`、隔离开发库（给出完整可复制的建库 + 指向 + 授权命令）、显式升级正式库；
4. 授权字符串必须与库名完全一致；
5. 不要把 `-AuthorizeDatabase interview_agent` 写进脚本或快捷方式（否则授权变成默认行为）；
6. 封存库没有任何授权入口；不要 `docker compose down -v`。

实测（不是读码推断）：

```
$ start-dev.bat -SkipInfrastructure        # DATABASE_URL 指向 *_e2e_* 库
MIGRATION_REFUSED: interview_agent_wb_r12_e2e_20261113 不是本地测试库（判定：E2E）。
生产/验收库迁移必须显式授权：python scripts/delivery.py migrate --authorize-database ...
Delivery check failed: migrate (exit 3).
Startup stopped. Existing services/data are retained; inspect status/logs.
```

没有启动任何服务，没有改动任何库。

---

## 7. Coach 当前自主能力审查（只读）

先给结论：**当前的 SummaryCoach 是一个"受限写作器"，不是一个 Agent。** 它拿到一份已经定死、已被裁剪的证据包，输出一份 schema 固定、引用必须可验证的 JSON；它没有任何查询能力，也没有任何决策权。

### 7.1 依据

| 事实 | 位置 |
|---|---|
| 单次结构化调用，没有工具回合 | `interview_report/agent.py` 的 `call()`：`ResearchModelCalls(...).call(COACH_PROMPT.name, INTERVIEW_COACH_ROUTE, payload, CoachArtifact)` |
| 输入由 `COACH_EVIDENCE_V1` 决定 | `interview_report/evidence.py`：`LIMITS`、`MAX_INPUT_BYTES=120_000`、`omitted` 逐项计数 |
| 输出 schema 固定、数字与引用受门禁 | `interview_report/schemas.py`（`CoachArtifact`，`extra="forbid"`）、`resolver.py`（`UNKNOWN_EVALUATION`、`RECURRING_SUPPORT_REQUIRED`、`COACH_COMPUTED_NUMBER`） |
| 记忆计划是纯规则函数 | `memory/capability_rules.py::memory_plan()`，`RULE_VERSION`，`priorities={CONFLICTED:4, RECHECK_DUE:3, ...}` |
| 报告生成本身已有幂等键 | `interview_report/service.py:58-60`：`regenerate` 无 `request_key` → `REPORT_REGENERATION_KEY_REQUIRED` 422 |

### 7.2 逐条回答

**Q1 现在 Coach 究竟能自主决定什么？**

能决定的：措辞、挑哪几条发现写进 `strengths/weaknesses/recurring_issues/job_alignment`（各自 ≤20 条）、每条引用哪些 `evaluation:<id>`（必须是本场已给出的）、写什么 `recommended_actions/practice_plan`（纯文本）、要不要提 `memory_proposals`（只能 PENDING）。

不能决定的：证据范围（就是给它的那些）、统计数字（必须与服务端一致，否则 422）、评价 id（只能从 catalog 里选）、是否要更多证据（没有通道）、下一场练什么（schema 里没有这个字段）、任何写入。

**Q2 它能否按需查询用户跨场次的能力演化？** 不能。payload 里与历史有关的只有两个字段，而且都是**被 `str()` 之后截断到 600 字符的 dict**：

```
"{'semantics': 'USER_CONFIRMED_CONTEXT_NOT_SKILL_PROOF', 'items': [{'id': 1, 'content': '希望做后端'}, ..."
```

我用 `project_snapshot()` 直接验证过这个形状。它不是结构化 JSON，条目稍多就会在 token 中间被切断。更要紧的是：**会话快照里其实有 `memory_guided_plan`（含每个知识点的 state、priority、needs_recheck、evaluation refs），但投影白名单里没有它**，它被记进 `omitted.snapshot_fields` 后丢掉了。所以 Coach 连"这场为什么优先考这些点"都不知道。

**Q3 它能否自主选择需要进一步核查的 Evaluation 证据？** 不能。catalog 就是 `payload["evaluations"]`，`resolve_evaluations` 对任何不在其中的 id 直接 422。而投影本身是**有损**的（答案 600 字、评价 800 字、缺失点 4 条、问题 3 条），`omitted.clipped_text_fields` 会把裁剪次数记下来，但 Coach 没有任何手段去取被裁掉的那部分。

**Q4 它能否根据历史训练表现提出下一场训练目标？** 不能。`practice_plan` 是给人看的句子，没有 `knowledge_id`、没有 mode、没有"这个目标现在是否合法"的概念。合法的训练目标完全由 `capability_rules.project()` + `training.modes` 决定，Coach 对此零影响。

**Q5 当前 MemoryGuidedPlan 是规则生成还是模型参与？** 纯规则，模型零参与。`memory_plan(states, targets, now)` 是确定性函数：按状态优先级排序、按 `retrieval_days` 过滤、按 `max_plan_items/max_plan_chars` 截断，输出带 `fingerprint` 的冻结计划。这是好事（可复现、可审计），但也意味着"个性化"目前全部来自规则表，不来自对这个人具体答了什么的理解。

**Q6 如果为 Coach 加入工具调用，哪些工具具有真实必要性？** 见 §8。只说必要性判据：一个工具只有在"当前输入**确定性地**丢失了它需要的信息"，且"这个信息**不能被预算内的投影提前带上**"时才值得加。按这个判据，现在只有两件事符合：

1. 投影把单条评价裁到 800 字/4 个缺失点，Coach 却在被要求给"具体的机制级建议"——它需要能看一条评价的全文；
2. 跨场次状态（VERIFIED / CONFLICTED / RECHECK_DUE）根本没进 payload，而"这场表现和历史判断是否冲突"恰恰是最有价值的复盘结论。

**Q7 如何保持 Controller 对权限/证据/状态/写入的决定权？** 见 §8.4。核心是：工具只读、只读冻结范围内的东西、只返回引用与状态不返回他人原文；产物仍然过 `resolve_coach_artifact`；建议只作为建议存进 `coach_analysis_json`，读的时候还要按当时的合法目标再校验一次。

**Q8 如何避免 Main Harness 那种重复上下文与 Token 浪费？** Main Harness 每轮把整份 `wire` + 累积 `tool_feedback` 重发，最多 3 次模型调用/4 次 HTTP。Coach 的 payload 上限 120KB，照搬会非常贵。约束见 §9.3：最多 1 次工具回合（2 次模型调用）、工具结果 ≤4KB、只在"确有被裁掉的内容或确有跨场次状态"时才提供工具 schema（否则直接给 Final schema，一次调用结束）。

**Q9 如何用现有真实 Evaluation 和历史 Replay 评价新 Coach 更有用？** 见 §9。用已有的 Replay 捕获与被归档的真实评价做**同场对比**（V1 vs V2 输入同一份证据），度量：建议合法性、引用合法性、建议与规则系统的一致性、工具使用率、Token 增量、失败码分布。真实 HTTP 保持 0。

---

## 8. Coach 下一阶段最小 Harness 方案（待批准，本轮不实施）

### 8.1 先修数据形状，再谈工具（Phase 0，零模型成本）

这三件事不需要模型、不需要工具，先把 Coach 的输入从"字符串"变成"结构"：

1. `project_snapshot` 里 `approved_memory_context` / `recent_performance_observations` 改为**结构化投影**（不是 `str(dict)`），每个条目 ≤140 字、≤8 条，保留 `semantics`，超出记 `omitted`；
2. 新增 `capability_snapshot`：从快照里的 `memory_guided_plan.items` 投影 `{knowledge_id, label, state, needs_recheck, reason_tags, observation_refs≤3}`，≤10 条、≤1.5KB；
3. 新增 `training_context`：`{mode, selected_knowledge_id}`（若本场是训练场），让 Coach 知道这场是"补弱项"还是"复测"。

这一步做完，Q2/Q4 的相当一部分就在**不增加任何调用**的情况下被满足；它也让 §9 的对比有意义——否则我们无法区分"模型不会用工具"和"模型根本没拿到数据"。

### 8.2 两个只读工具（Phase 1）

**T1 `read_evaluation_detail(evaluation_id)`**

- 作用域：仅本场；`evaluation_id` 必须已在 `payload["evaluations"]` 的 catalog 里，否则 `ERROR/EVALUATION_OUT_OF_SCOPE`。
- 返回：该评价未裁剪的 `answer`、`evaluation`、`missing_points`、`factual_or_reasoning_issues`、`reference_answer`，单次 ≤4KB，带 `truncated`。
- 为什么必要：投影是**有损**的，且 Coach 被要求给机制级建议。这是唯一"信息确实存在、只是被预算裁掉"的情况。

**T2 `read_capability_history(knowledge_id)`**

- 作用域：`knowledge_id` 必须属于本场冻结 blueprint 的 targets；用户来自 `session.user_id`（经 `owned()` 校验）。
- 返回：`state`、`revision`、`independent_sessions`、`last_observed_at`、`conflicts[{status, reason}]`、最近 ≤5 条观察的**引用**（`evaluation:<id>`、`session:<id>`、`state`、`observed_at`、`assisted`）。**不返回任何答案原文**。
- 为什么必要：跨场次冲突是"这场表现与历史判断不一致"的唯一来源。只给引用不给原文，既够用于论证，也不把别人的（其实是同一用户其他场次的）长文本塞进预算。

两个工具都不写库、不调模型、不跨用户、不越出冻结范围。

### 8.3 循环与预算

| 项 | 值 | 理由 |
|---|---|---|
| 工具 schema 是否提供 | 仅当 `omitted.clipped_text_fields > 0`（T1 有意义）或 `capability_snapshot` 非空（T2 有意义） | "不为用工具而调工具" |
| 工具调用上限 | 1 次（`COACH_TOOL_LIMIT=1`，可配 2） | 两个工具都是"补一次信息"，不需要反复试探 |
| 模型调用上限 | 2 次（`COACH_MODEL_LIMIT=2`） | 第 2 轮强制 `FinalArtifact` schema，不可能有第 3 轮 |
| 单次工具结果 | ≤4KB（T1）/≤1.2KB（T2） | 上限写进常量，超出截断并标 `truncated` |
| 工具超时 | 2s | 与 Main Harness 一致 |
| 重复工具 | 同名同参第二次 → `ERROR/DUPLICATE_TOOL` | 复用现有判定 |
| 工具失败 | 返回 `ERROR/...`，模型必须照常给出终稿 | 工具是增强，不是前置条件 |
| 输入预算 | 第 2 轮上限 = 第 1 轮 + 6KB；超出 → `COACH_INPUT_BUDGET` 422，不静默裁剪 | 与现有行为一致 |

### 8.4 Controller 保留什么

| 权限 | 现状 | 加工具后 |
|---|---|---|
| 证据范围 | payload 决定 | 工具只能在 payload 或冻结 blueprint 范围内取，越界返回 ERROR |
| 统计数字 | resolver 校验 | 不变；工具不返回任何统计 |
| 引用合法性 | `resolve_evaluations` | 不变；T2 返回的 `evaluation:<id>` 也必须在 catalog 里才能被引用（否则 422） |
| 会话归属 | `_session(..., user_id, lock=True)` | 不变；T2 用同一个 `user_id` |
| 训练状态 | `capability_rules.project()` 决定合法目标 | Coach 的 `suggested_training` 只能**提名**，合法性由服务端在写入与读取时各校验一次 |
| 最终写入 | `coach_analysis_json` 由服务端组装 | 不变；字段新增在 JSON 里，**不需要迁移** |

### 8.5 受控建议输出

在 `CoachArtifact` 增加可选字段：

```
suggested_training: {mode: FULL|WEAKNESS|RECHECK, knowledge_id: str, reason: str, supporting_evaluation_ids: [evaluation:id]}
```

`resolver` 侧校验（任一不满足 → 422 `COACH_TRAINING_SUGGESTION_INVALID`，带 diagnostics）：

1. `knowledge_id` 必须在**当前** `project(states)` 给出的 targets 里；
2. `mode` 必须在该 target 的 `available_modes` 里（RECHECK 还要求 `CONFLICTED|RECHECK_DUE`，且风格不是 gentle）；
3. `supporting_evaluation_ids` 必须是本场真实 id；
4. `reason` 过数字门禁与 `validate_observation_content`。

存进 `coach_analysis_json.suggested_training`（附 `validated_at` 与当时的 `memory_revision`）。训练中心展示时**按当时的合法目标再校验一次**，不合法就不展示（静默降级为文字建议），绝不拿它直接创建会话。

### 8.6 必要改动文件

| 文件 | 改动 | 规模 |
|---|---|---|
| `interview_report/evidence.py` | Phase 0 结构化投影 + `capability_snapshot` + `training_context` | 中 |
| `interview_report/tools.py`（新） | T1/T2 实现，纯函数 + 只读查询 | 小 |
| `interview_report/schemas.py` | 工具回合 schema（`CoachRequest`/`FinalArtifact`）+ `suggested_training` | 小 |
| `interview_report/agent.py` | 复用 `ResearchModelCalls.call(..., checkpoint_action=True)` 的两轮循环 | 中 |
| `interview_report/resolver.py` | `suggested_training` 校验 | 小 |
| `interview_report/prompts.py` | 新增 `1.1.0`，说明工具用途与"没有新信息就不要调" | 小 |
| `interview_report/service.py` | 把 `payload` 与合法 target 集合传给 resolver | 小 |
| `core/config.py` | `interview_coach_tool_limit`、`interview_coach_model_limit` | 小 |
| `tests/test_coach_harness.py`（新） | 见 §9 | 中 |

不新增 Agent、不新增表、不改 `InterviewSession`/training 状态机。

---

## 9. 效果评测与 Token 预算控制

### 9.1 数据来源（全部离线）

1. **已归档的真实评价**：历史 Live 验收库中的 `interview_evaluations`（只读、只导出到测试库）；
2. **Replay 捕获**：`.runtime/acceptance/wb_budget_live_*/captures.jsonl`（第十二轮没动，只用其存在性）；
3. **既有夹具**：`v22_support.MemoryReplayProvider`、`test_director_reference_repair.py` 的长引用场景。

### 9.2 指标（同一批会话，V1 vs V2）

| 指标 | 目标 | 为什么重要 |
|---|---|---|
| 建议合法性率 | 100%（不合法即 422，不该出现在成功产物里） | 建议不能指向系统不会执行的训练 |
| 引用合法性率 | 100% | 与现状一致，不能因为加工具而退步 |
| 数字门禁触发次数 | 不高于 V1 | 防止"多看到证据就多说数字" |
| 工具使用率 | 30%–70% | 过高说明 Phase 0 投影没做好；接近 0 说明工具没价值 |
| 相对规则系统的建议一致性 | ≥80% 命中 `project()` 的最差状态目标 | 这是"更有用"的操作化定义 |
| 输入 Token（P50/P95） | 单场不超过 V1 的 1.35×（仅用工具的场景） | 预算纪律 |
| 模型调用次数 | 未用工具的会话 = 1；用工具 = 2 | 不能出现第 3 次 |
| 失败码分布 | 不出现新的失败类别（`COACH_*`） | 工具失败必须降级而不是中断 |
| 端到端时延（P95） | +1 次往返以内 | 工具是本地查询 |

### 9.3 具体做法

1. 对同一份 payload 分别跑 V1（现状）和 V2（Phase 0 + 工具），把两次产物都存下来对比，模型用 replay provider，HTTP=0；
2. 人工抽查 10 场：只看"建议是否指向了真正该练的机制"，不看措辞；
3. 回归：现有 `test_v23_report*.py` / `test_interview_report*` 必须全绿，且旧报告的 `generation_key`（含 prompt version）不变——加 `1.1.0` 后旧报告不会被静默重生。

### 9.4 Token 预算的硬约束

- 第 1 轮：`payload`（≤120KB）+ schema + system；第 2 轮：同上 + `tool_results`（≤6KB）；
- 不提供工具时 schema 直接是 Final，**一次调用结束**；
- 工具结果不计入 `statistics`，不参与任何数字校验；
- 超出预算 → 422 并给出 `section_bytes`，与现有 `COACH_INPUT_BUDGET` 行为一致（绝不静默裁剪证据）。

---

## 10. 当前剩余高风险问题

1. **缺键即 422 是一个破坏性契约。** 第一方前端不受影响，但任何只发 `training_mode` 的第三方调用方现在会失败。这是有意的（静默退化更糟），但需要写进对外说明。
2. **幂等保证来自锁，不是来自约束。** 任何未来新增的"创建训练会话"路径若不走 `TrainingService.create`，会重新引入本轮的缺陷，而数据库不会拦。§3 给了补约束的条件。
3. **并发 `start` 最坏浪费一次模型调用**（业务状态只写一次）。这是既有行为，普通面试同样存在，本轮没有改。
4. **私人入口仍需手动选一次专业方向。** 从列表进入训练的路径上，用户必须先选 Skill 才能看到训练目标。页面有提示，但多一步；根因是私人 JD 没有 Skill 推荐接口（后端有 `recommend_for_target`，未暴露）。
5. **能力记忆没有岗位维度。** 私人与公开训练共用一个 `(user, topic)` 根。这是 §8.2 里 T2 只按 topic 查询、不按岗位过滤的原因——它在语义上是"这个用户在这个知识点上的历史"，不是"在这个岗位上的历史"。
6. **Coach 当前收到的记忆是 `str(dict)` 截断串**（§7.2 Q2）。这是本轮发现的最实质的输入缺陷，Phase 0 就是为了修它；在此之前不应评价 "Coach 不会利用历史"。
7. **`suggested_training` 若实施，只存在于 JSON 里**，没有数据库约束；合法性靠写入与读取两次校验。需要在实现时补一条"非法建议不得展示"的测试。
8. **浏览器验收用的是 replay provider。** 简历上传 → 解析 → 画像这条链路仍然没有在本轮走过（需要 Worker + 额度）。
9. **并发测试是单进程 3 协程**，多进程争用由 advisory lock 的语义保证，没有做多进程实测。

---

## 附：本轮实测计数与库

| 项 | 结果 |
|---|---|
| 后端全量（业务段） | **782 passed / 0 failed / 1 skipped**（245.75s） |
| 后端全量（迁移段，独立空库） | **10 passed / 0 failed**（54.83s） |
| 训练/岗位/记忆/隔离子集 | **87 passed** |
| 幂等专项 | 10 passed（旧条件下 1 failed / 9 passed，已复现） |
| 前端 `vue-tsc && vite build` | 通过，0 类型错误 |
| 真实模型 HTTP | **0** |
| 浏览器 | 真实 Chrome headless + CDP，10 步（§5） |

| 库 | 用途 | 状态 |
|---|---|---|
| `interview_agent` | 生产 | 未连接、未迁移（`start-dev.bat` 拒绝路径已验证） |
| `interview_agent_wb_r12_test_20261113` | 业务回归 | 0027 / 67 表 |
| `interview_agent_v21_wb_r12_20261113` | 迁移用例空库 | 跑完 0027，不可二次跑迁移用例 |
| `interview_agent_wb_r12_e2e_20261113` | 浏览器验收 | 0027 / 67 表 / 2 用户 / 2 私人目标 |
| 历史 Live 库 | 封存 | 只读，未连接 |
