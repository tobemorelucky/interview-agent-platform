# 89 · 私人 JD 训练闭环、权限顺序与测试库隔离（第十轮）

> 归属：WorkBuddy。本轮不调用任何真实收费模型（真实模型 HTTP = **0**），不迁移、不写生产库、不碰历史 Live 验收现场。
> 结论标注与 `.codebuddy/CODEBUDDY.md` 一致：**事实** 有代码/命令/测试证据，**推断** 未验证，**待办** 未做。

---

## 一、测试库隔离方案（本轮补齐）

### 1.1 第九轮的问题与根因

**事实**：第九轮的回归 fixture 把 219 条 ACTIVE 公司与 347 条 PUBLISHED JD 写进了**浏览器验收库**。根因不在 fixture，而在"由人来决定把 `DATABASE_URL` 指向哪个库"：`.env` 里 `DATABASE_URL` 默认就是生产库 `interview_agent`，`config.py` 不校验库名，`conftest.py` 原先也不校验。

### 1.2 现在有两道闸

| 闸 | 位置 | 触发时机 | 作用 |
|---|---|---|---|
| 第一道：指向 | `scripts/run-workbuddy-tests.py` | 运行前 | 只允许 `interview_agent_wb_*` / `interview_agent_v21_*`，且名字必须含 `_test` / `_regression` / `_mig`；命中 `e2e/live/accept/demo/browser/prod` 直接拒绝 |
| 第二道：落盘 | `apps/api/tests/conftest.py` session 级 autouse | 测试会话开始 | 库名不是测试库、或 `DATABASE_URL != M1_TEST_DATABASE_URL` 时 `pytest.exit(2)` |

第二道的判定是"**白名单 + 黑名单 + 一致性**"三重条件：必须带 `interview_agent_` 前缀、必须带测试后缀（`interview_agent_v21_*` 是历史豁免名），命中黑名单即拒绝，且两个环境变量必须指向同一个库。没有设置 `M1_TEST_DATABASE_URL` 的纯离线单测不受影响。

**事实（已验证）**：把 `DATABASE_URL` 指向 `..._e2e_...` 库时，pytest 在收集阶段即以退出码 2 结束；生产库名 `interview_agent`、E2E 库名、历史 Live 批次库名在运行器第一道闸就被拒绝，且运行器**从不 drop / 清空任何库**。

### 1.3 本轮使用过的数据库（全部名字与用途）

| 数据库 | 用途 | 状态 |
|---|---|---|
| `interview_agent` | 生产库 | **本轮未被任何测试或迁移连接**；现有 19 条会话未变 |
| `interview_agent_wb_r10_test_20261112` | 承载数据的后端回归（单测 + API + Replay） | Alembic `0027`，67 表 |
| `interview_agent_v21_wb_r10_20261112` | 10 个迁移用例（需空库、会 upgrade/downgrade） | 跑完后停在 `0027`，67 表 |
| `interview_agent_wb_r10_e2e_20261112` | 浏览器验收库（预留） | Alembic `0027`，67 表，`companies` 仍为 0；**本轮未写入** |
| `interview_agent_thinking_long_20261110` 等历史 Live 库 | 封存现场 | 只读，未触碰 |

**事实**：`interview_agent_v21_wb_r10_20261112` 跑完迁移套件后不再是空库，**同一库不能第二次跑迁移用例**（会走"有数据"分支返回非零）。这是既有约束，本轮只记录。

### 1.4 可复用的安全命令

```bash
# 新建一个空库 + 迁移 + 跑非迁移用例（自动把两个环境变量指向同一个库）
python scripts/run-workbuddy-tests.py --database interview_agent_wb_r10_test_20261113

# 复用已存在的测试库，只跑某几个文件
python scripts/run-workbuddy-tests.py --database interview_agent_wb_r10_test_20261112 --reuse -- tests/test_private_training.py

# 迁移用例必须单独一遍（空库）
python scripts/run-workbuddy-tests.py --database interview_agent_v21_wb_r10_20261113 --migration
```

**待办（未修）**：闸门只覆盖 pytest。`scripts/delivery.py migrate`、`scripts/start-interview-platform.ps1`、`apps/api/scripts/prepare_v23_demo.py` 仍会按 `.env` 的 `DATABASE_URL` 直接迁移或直接导入 fixture（后者绕过 conftest）。本轮范围只到测试，不改交付脚本；建议下一轮在 `delivery.py` 的 migrate 前加同款库名闸。

---

## 二、权限顺序修复（存在性预言机）

**问题**：用户 B 引用用户 A 的 `job_target_id` 时，原顺序先查画像，于是没有画像的 B 得到 `PROFILE_REQUIRED`（409），有画像的 B 得到 `JOB_TARGET_NOT_FOUND`（404）。错误码本身成了"这个 id 是否存在"的探测器，且泄露"平台里有私人 JD"这一事实。

**修复（`8354b2a`）**：`build_context` 改为**先解析岗位来源与归属，再查画像**：

1. `job_target_id` 与 `company_id/job_id/job_description_version_id` 同时出现 → `JOB_SOURCE_CONFLICT`（422）；
2. 两者都没有 → `JOB_SOURCE_REQUIRED`（422）；
3. `private_target()` 校验 `user_id` + `parse_status == CONFIRMED`，缺失与他人目标**统一** `JOB_TARGET_NOT_FOUND`（404）；
4. 之后才查画像 → 正常用户仍是 `PROFILE_REQUIRED`。

**事实**：会话创建与训练入口共用 `build_context`，因此两处同时生效（`test_job_target_ownership.py` 用两个独立用户覆盖了 session create、training workbench、list/detail/match/update/delete，以及"有画像的正常用户仍然拿到 PROFILE_REQUIRED"）。公开岗位语义未改。

---

## 三、私人 JD 进入训练中心（本轮主功能）

**核心结论（事实）**：私人 JD 训练**不需要迁移、不需要新 Agent、不需要新状态机**。`build_context` 早就支持 `job_target_id` 并把 `job_target` 写进冻结快照；缺的只是两处投影和前端入口——训练面板把 `company_id/job_id` 直接透传，私人目标下这两个值恒为 `None`，页面因此无法区分"没选岗位"和"这是一个私人岗位"。

**改动**：
- `training.py`：workbench 与 feedback 增加 `job_target_id`；workbench 增加 `source_label="用户提供（未经企业核实）"`；`company_id/job_id` 允许为 `None`（不再伪造 0）。
- `TrainingPage.vue`：加载用户自己**已确认**的私人岗位列表并与公开岗位互斥；支持 `?job_target_id=` 与 `?session_id=`；从报告进入时，私人目标 id 与 Skill **取自该场会话的冻结快照**，不取可能已被编辑的 JD；页面常驻私人来源说明。
- `TrainingFeedback.vue`：私人来源时给出"继续本私人岗位训练"，公开岗位分支原样保留。
- `JobTargetPage.vue`：已确认目标新增"用这个岗位开始训练"。

**只用冻结 JD + 已注册 Skill（事实）**：私人目标的岗位要求来自 `target.requirements_json`（用户在确认前编辑过的那份），Skill 由用户在页面选择后经 `SkillRegistry().freeze()` 冻结；公开题库、公开公司、公开 JD 在私人路径上始终为 `None`/空（`context.py:65-70`）。

**不进入公共数据（事实）**：`test_private_training.py` 对 `companies / jobs / job_description_versions / job_requirements` 四张表在训练前后做计数比对，断言完全不变。

**改 JD 不悄悄改写已开始的训练（事实）**：会话创建后再把同一目标改要求并重新确认（版本 1 → 2），该会话的 `context_snapshot` 与创建时**逐字节相同**（`test_private_full_training_freezes_the_confirmed_version_and_creates_no_public_rows`）。

---

## 四、FULL / WEAKNESS / RECHECK 的真实状态

| 模式 | 私人 JD 下是否可跑 | 证据 |
|---|---|---|
| `FULL` | **可以**（事实） | 真实创建会话，`training_mode=FULL`，快照冻结 `job_target.version=1`，`company_id/job_id/jd_version_number` 全为 `None` |
| `WEAKNESS` | **有真实评价后可以**（事实） | 全新用户先被拒 `TRAINING_TARGET_INVALID`（422）；跑完一场私人 FULL 并产生 1 条正式 Evaluation 后，面板出现 `WEAKNESS` 可用目标，创建成功且 blueprint 收敛到该单一目标 |
| `RECHECK` | **未验证**（待办） | 代码路径与 WEAKNESS 同属 `freeze_training` 的聚焦分支，额外要求"标准/严格风格"；但私人 JD 下没有造出 `CONFLICTED` 状态（需要两条真正独立的相反观察），本轮没有证据 |

**不编造记忆（事实）**：新用户在私人目标下 `available_modes` 全部只有 `FULL`；`WEAKNESS/RECHECK` 只在 `CapabilityMemoryService` 真的积累了该 topic 的合法观察后才出现。

**记忆维度（事实，属限制）**：`CapabilityMemoryService.refresh` 按 `(user_id, topic)` 聚合，`observation_scope` 里带 `snapshot.job_id`（私人会话为 `None`）。因此私人训练与公开岗位训练**共用同一个 topic 记忆根**，只是不会把两者的观察当成"同一条件下的重复证据"。面板没有"按岗位看状态"的能力。

---

## 五、前端路径（用户实际怎么走）

| 入口 | 路径 | 说明 |
|---|---|---|
| 我的岗位 → 已确认目标 | `/jobs/targets` →「用这个岗位开始训练」 | 只有 CONFIRMED 才可点 |
| 训练中心直选 | `/training` →「我的岗位（自己提供的 JD）」 | 与公开岗位下拉互斥；选了私人岗位不会再带公开 id |
| 报告页 | `/interview/reports/:id` →「针对本场岗位继续训练」 | 携带 `session_id`，目标与 Skill 从该场冻结快照解析 |
| 训练结束反馈 | `TrainingFeedback` →「根据当前状态继续本私人岗位训练」 | 携带 `job_target_id` |

**无画像时（本轮补）**：训练面板读取失败且错误码为 `PROFILE_REQUIRED` 时，除中文提示外给出「去上传简历」链接，而不是让用户停在一个永远加载不出来的页面。

**公开岗位训练未回归（事实）**：`test_public_training_entry_is_unchanged_for_the_same_user` 断言同一用户的公开入口仍返回 `job_target_id=None`、`source_label=None`、原 `company_id/job_id`。

---

## 六、改动文件

**后端**
- `apps/api/src/interview_api/modules/interview/context.py`（权限顺序）
- `apps/api/src/interview_api/modules/interview/training.py`（私人身份投影）
- `apps/api/src/interview_api/modules/job_target/service.py`（`reused`、`support_counts`）

**测试**
- `apps/api/tests/conftest.py`（session 级库名闸）
- `apps/api/tests/test_database_isolation_guard.py`（9 条离线判定表）
- `apps/api/tests/test_job_target_ownership.py`（5 条，双用户）
- `apps/api/tests/test_private_training.py`（4 条，私人 JD 全链路）
- `scripts/run-workbuddy-tests.py`（运行前闸 + 建库 + 迁移 + 跑测）

**前端**
- `apps/web/src/api/training.ts`、`apps/web/src/types/job-target.ts`
- `apps/web/src/pages/TrainingPage.vue`、`apps/web/src/components/TrainingFeedback.vue`
- `apps/web/src/pages/JobTargetPage.vue`、`apps/web/src/pages/InterviewReportPage.vue`

**没有**：迁移文件、Provider 配置、`.env`、`AGENTS.md`/`CLAUDE.md`/`.claude/`、`docs/00–80`。

---

## 七、提交哈希

| Hash | 内容 |
|---|---|
| `9eb66a8` | `test(workbuddy)`: pytest 拒绝写入 E2E / 生产 / 历史 Live 库 |
| `8354b2a` | `fix(workbuddy)`: 私人 JD 归属先于其他校验（消除存在性预言机） |
| `1f2071f` | `fix(workbuddy)`: JD 复用与逐事实证据计数如实汇报 |
| `7715ccc` | `feat(workbuddy)`: 用已确认的私人 JD 进入训练（含 4 条新测试） |
| `b7d4cb9` | `fix(workbuddy)`: 岗位页与报告页不再误报自身状态 |
| `3b99f6c` | `test(workbuddy)`: 带闸门的隔离测试运行器 |
| 本文档 | `docs(workbuddy)`（单独提交） |

---

## 八、测试结果（真实计数）

**后端（全部离线/Replay，真实模型 HTTP = 0）**

| 范围 | 库 | 结果 |
|---|---|---|
| 全量后端 | `interview_agent_wb_r10_test_20261112` | **757 passed / 10 failed / 1 skipped**（246.95 s） |
| 10 个迁移用例 | `interview_agent_v21_wb_r10_20261112`（空库） | **10 passed / 0 failed / 0 skipped**（64.52 s） |
| 受影响子集（私人训练 + 岗位 + 训练 + 隔离闸） | 同上测试库 | **31 passed / 0 failed / 0 skipped**（27.85 s） |
| 运行器冒烟（2 文件） | 同上测试库 | **13 passed** |

**关于那 10 个 failed**：全部是 `test_00`–`test_09` 迁移用例，原因是"承载数据的回归库不是空库、且 `test_09` 要求库名前缀 `interview_agent_v21_`"。在专用空库里同一批用例 **10/10 通过**。这是既有约束（第九轮文档已记录），不是本轮改动引入的失败，也没有一个失败落在业务代码上。

**前端**：`npm run build`（`vue-tsc && vite build`）通过，类型检查 0 报错，构建 3.95 s。

**浏览器**：本轮**没有实际操作浏览器**。私人 JD → 训练 → 报告这条链路只有构建与代码级验证；第四项那些"界面是否还在骗人"的修改，也只做了读码 + 构建确认。

---

## 九、已知限制

1. **闸门未覆盖脚本**：`delivery.py migrate`、启动脚本、`prepare_v23_demo.py` 仍按 `.env` 行事（见 1.4 待办）。
2. **私人 RECHECK 未验证**：没有构造出 `CONFLICTED` 状态，因此"私人 JD 能否独立复测"目前只是同代码路径的推断。
3. **记忆无岗位维度**：同一 topic 的私人训练与公开岗位训练汇总到同一个根，无法区分来源岗位。
4. **无画像的 Skill 推荐**：后端有 `recommend_for_target`（纯确定性），但没有暴露接口；私人目标下用户必须手选 Skill。页面措辞已按"请你选择"如实写，没有声称系统没有推荐能力。
5. **`training_mode` 单独出现时绕过幂等**：`core_service.create` 只在 `training_mode` 与 `training_request_key` 同时存在时才走 `TrainingService.create`；只给 `training_mode` 会落到普通创建路径。前端两个字段一起发，但 API 层没有强制（未改，属契约风险）。
6. **未操作浏览器**：见第八节。
7. **`.runtime/ui/cdp.mjs` 依旧 gitignored**，浏览器复现脚本不进仓库。

---

## 十、下一阶段建议

### 10.1 请求记录与业务审计的最小关联（只读评估，本轮不实现）

**现状（事实）**：
- 真实 HTTP 的正式入口是 `MockInterviewService.call_agent` → `with_model_reservation` → `agent.call`；每次调用生成一个随机 `call_key`（`uuid4().hex`）作为 `model_admissions` 主键，会话侧在 `interview_plan_json.model_call_reservations` 里记额度。
- 业务审计落在 `experience_agent_runs` + `interview_agent_calls`（session/answer/agent_run 关联），**没有** `call_key`，也**没有** provider 的请求 id。
- `ModelResponse` 只有 `text` 与 `usage`，**不携带** provider request id；因此业务库无法与 provider 侧对齐。
- Live Capture（`.runtime/acceptance/<batch>/captures.jsonl`）是**批次脚本自己的**层：每行有 `sid`、`uid`、`provider_request_id`、`request_sha256`、`messages`、`business`，但**没有** `agent_run_id` / `answer_id` / `call_key`。

**结论**：三份记录（准入账本 / 业务审计 / 抓包）目前只有时间和会话号能对齐，"哪一条抓包对应哪一次 AgentRun"无法证。

**最小方案（建议，需先批准再动）**：
1. `experience_agent_runs` 加两列 `call_key String(64) NULL`、`provider_request_id String(128) NULL`（一次迁移，`0028`），`call_key` 建唯一索引；
2. `ModelResponse` 加可选 `request_id`，provider 从响应头/响应体填；`agent.call` 落 run 时写入；
3. Capture 行在发送前把 `call_key`/`agent_run_id`（若可得）写进 `business` 字段，形成三向 join：`model_admissions.call_key = agent_runs.call_key = captures.business.call_key`；
4. 抓包内容只保留 `messages` 与参数，**永不记录** Authorization/Cookie/真实简历以外的认证头；`messages` 本身含简历文本，必须继续走现有 `sanitized()` + `PROTECTED_TEXT_IN_OUTBOUND_INPUT` 闸；
5. **不要**把历史批次标成"可精确回放"：那些行没有 run id，且 prompt 版本已变（Main `2.4.0-harness.*`），只能做"内容指纹相同"的近似比对。

### 10.2 其它

- 在 `delivery.py migrate` 前加同款库名闸，把两道闸扩到交付路径。
- 私人 RECHECK 需要一条真正冲突的观察才能验证；下一轮可用离线 provider 造两条相反评价来做。
- 若要让私人训练的记忆可区分岗位，需要给 `capability_memories` 加维度——属于核心变更，须先出文件级方案再批准。
