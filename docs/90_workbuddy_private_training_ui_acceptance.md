# 90 · 测试基础设施收尾、私人 JD 训练真实 UI 验收与 RECHECK 覆盖（第十一轮）

> 归属：WorkBuddy。本轮真实模型 HTTP = **0**；未迁移生产库、未触碰历史 Live 现场；未新增 Agent、未重构 Controller / Memory。
> 结论标注：**事实**（代码/命令/测试证据）、**推断**（未验证）、**待办**。

---

## 一、两类测试已经分库（事实）

第十轮的 `757 passed / 10 failed / 1 skipped` 里，10 个失败全部是迁移用例：它们要把空库从零 upgrade 再 downgrade，却被指向了承载业务数据的回归库。用例本身没有错，错在分组。

现在 `scripts/run-workbuddy-tests.py` 跑两段，各用各的库：

| 段 | 目标库 | 前置条件 | 内容 |
|---|---|---|---|
| 业务回归 | `interview_agent_wb_r11_test_20261112` | 自动建库 + `alembic upgrade head` | 全部用例，`--ignore` 十个迁移文件 |
| 迁移用例 | `interview_agent_v21_wb_r11b_20261112` | **全新空库**，本段不预先迁移 | 十次 upgrade/downgrade |

合并结果来自 JUnit XML 计数（不靠抓 stdout 文本），并同时打印每段明细。运行器从不 drop、清空或重建任何已存在的库；迁移库若已有表，直接拒绝并说明原因。

**事实（本轮实测）**：`combined: passed=782, failed=0, skipped=1, error=0`
（业务段 `772 passed / 0 failed / 1 skipped`，迁移段 `10 passed`）。

---

## 二、全量回归已消除环境性失败（事实）

| 轮次 | 结果 | 说明 |
|---|---|---|
| 第十轮 | 757 / 10 failed / 1 skipped | 10 个失败是"迁移用例跑在非空库" |
| 第十一轮 | **782 / 0 failed / 1 skipped** | 同一批用例，按用途分到两个库 |

用例总数从 768 增到 783：新增 `test_db_targets.py`（10 条，其中 1 条需要真实库做空库校验）与 `test_private_recheck.py`（5 条）。**没有删除、跳过或改写任何既有用例。**

跳过的 1 条仍是既有环境依赖（`test_v142_source_fragments.py:96`，缺 V1.4.1 快照库），与本轮无关。

---

## 三、运行与迁移脚本的保护范围（事实）

判定收敛到一处：`apps/api/src/interview_api/core/db_targets.py`（纯字符串逻辑，无 I/O），分类为 `PRODUCTION / ARCHIVED_LIVE / E2E / UI / DEMO / MIGRATION / TEST / SYSTEM / OTHER`。pytest、测试运行器、`delivery.py`、`prepare_v23_demo.py` 全部问它，不再各写一套。

| 入口 | 变化 | 实测 |
|---|---|---|
| `apps/api/tests/conftest.py` | 改为委托 `db_targets.test_run_problems` | 原 9 条判定表全过 |
| `scripts/run-workbuddy-tests.py` | 运行前校验 + 空库校验 + 两段式 | 生产/E2E/历史库三种名字被拒 |
| `scripts/delivery.py migrate` | 新增 `--authorize-database <名字>`；系统库与历史 Live 库**无覆盖** | 生产库拒绝（exit 3）、历史 Live 库即便带授权也拒绝、测试库放行 |
| `scripts/start-interview-platform.ps1` / `platform-common.ps1` | 新增 `-AuthorizeDatabase`，只在操作员显式传入时转发 | 未传时启动脚本无法隐式迁移生产库 |
| `apps/api/scripts/prepare_v23_demo.py` | 复用分类器，且必须 `*_browser_*` 夹具库 | 不允许写进 `*_e2e_*` 库 |

一个细节值得记下：`interview_agent_thinking_long_20261110`（docs/80 的封存现场）名字里既没有 `live` 也没有 `accept`，旧规则会把它当"未知库"而允许授权迁移。现在 `_thinking_long` 与 `_smoke` 都归入 `ARCHIVED_LIVE`，**永不迁移**。

---

## 四、私人 RECHECK 的真实准入条件（事实）

规则来自 `training.modes()` 与 `capability_rules.project()`，不是猜的：

```
RECHECK  <- state ∈ {CONFLICTED, RECHECK_DUE} 且未 revoked
WEAKNESS <- state ∈ {GAP_SUSPECTED, PRACTICING}
FULL     <- 恒可用
```

- **CONFLICTED**：同一 `scope`（job、轮次、难度、题源、rubric、题面文本）上，先有独立"好"观察、后有"不达标"观察，且之后没有 ≥2 条独立好观察把它解决。冲突一旦成立，该目标**只**提供 FULL + RECHECK，不再提供 WEAKNESS。
- **RECHECK_DUE**：状态先是 VERIFIED（≥2 个独立会话的好观察），且距上次独立验证超过 30 / 60 / 90 天（按 scope 多样性取阈值）。它只提供 FULL + RECHECK。
- 两种模式都要求 `interviewer_style` 不是带提示的 `gentle`，否则 `INDEPENDENT_RECHECK_STYLE`（422）。

**如何在没有真实时间流逝的情况下验证 RECHECK_DUE**：`CapabilityMemoryService` 本来就接受 `clock`，`build_context` 与 workbench 都通过它读记忆。测试只把**读时钟**前移 120 天，观察本身完全是真实评价落库的结果（`evaluation_id`、`scope`、`session_id` 都在），没有手写任何 `VERIFIED`/`CONFLICTED`。

---

## 五、FULL / WEAKNESS / RECHECK 测试结果（事实）

`apps/api/tests/test_private_recheck.py`，5 条，全部通过：

| 用例 | 断言要点 |
|---|---|
| 无冲突时拒绝 RECHECK | 单场好答 → `PRACTICING`，只有 FULL+WEAKNESS；直接建 RECHECK 得 `TRAINING_TARGET_INVALID`(422)；无关目标仍为 `UNKNOWN` 只有 FULL |
| 相反独立观察产生冲突 | 五场 `GOOD, GOOD, WEAK, GOOD, WEAK` 后 `kafka_reliability = CONFLICTED`，可用模式恰为 `["FULL","RECHECK"]`，冲突 `OPEN`；`gentle` 被 422 拒绝；创建成功且 blueprint 收敛到单一目标；`memory_guided_plan` 引用 `evaluation:*` 且 `needs_recheck=true` |
| 验证过期是另一个合法入口 | 两场独立好答 → `VERIFIED`、`independent_sessions==2`、只有 FULL、建 RECHECK 被拒；读时钟 +120 天后 → `RECHECK_DUE`、FULL+RECHECK、创建成功；回到真实时钟仍是 `VERIFIED` |
| 冻结 JD 不被改写 | 冲突后建 RECHECK（`job_target.version=1`），随后把 JD 改到 v2，该会话 `context_snapshot` 不变；另一账号读该训练反馈得 404 |
| 记忆是真实聚合 | 存储投影里 3 条观察 / 2 个 scope / 状态 `CONFLICTED` / 冲突原因 `OPPOSITE_INDEPENDENT_OBSERVATIONS`，并同时确认 redis 为 `VERIFIED` |

FULL 与 WEAKNESS 的证据来自第十轮 `test_private_training.py`（本轮继续全绿）：私人 FULL 冻结版本、改 JD 不改会话、不产生公共行；WEAKNESS 需要真实评价才出现。

---

## 六、浏览器实际完成的操作步骤（事实）

真实 Chrome 157（headless，CDP 直连 9222），前端 Vite 5173，API 8000。API 由 `apps/api/scripts/serve_offline_acceptance.py` 启动：**同一个 FastAPI 应用与全部服务**，只把模型边界换成 replay provider，并阻断一切对外 HTTP。

| # | 操作 | 结果 |
|---|---|---|
| 1 | 登录 `r11.owner@example.com` | 进入 `/dashboard`，无弹窗 |
| 2 | 粘贴 JD → 保存 → 解析 → 确认 | 目标 1，`CONFIRMED` v1，5 条要求，来源标注"用户提供（未经企业核实）" |
| 3 | 打开"我的画像" | 显示当前版本（离线预置画像，见 §八） |
| 4 | 岗位页点"用这个岗位开始训练" | 跳 `/training?job_target_id=1`，页面出现私人来源说明 |
| 5 | 选 Java 后端 Skill → 轮数 1 → 开始 FULL | 建会话 1 并进入答题，作答一次后 `COMPLETED` |
| 6 | 训练页切 WEAKNESS | 目标下拉出现「缓存工程 · 疑似缺口」，建会话 12 并作答 |
| 7 | 训练页切 RECHECK | 目标下拉出现「缓存工程 · 表现存在分歧」，页面提示"第一题先独立作答"，建会话 14 并作答 |
| 8 | 报告页 `/interview/reports?session_id=14` | 生成 Coach（replay）后显示优势/弱点/建议；训练反馈显示"训练前：表现存在分歧 / 训练后：表现存在分歧 / 冲突仍未解决" |
| 9 | 回到岗位页追加一条要求 → 保存 → 再次确认 | 目标升到 v2；会话 14 的 `context_snapshot.job_target.version` 仍为 **1**，`company_id/job_id` 仍为 **null** |
| 10 | 用第二用户 token 访问 | `GET /job-targets/1` → **404**、`GET /interview/training/sessions/14` → **404**、`GET /job-targets` 列表 **0 条** |

截图 14 张，位于 `.runtime/ui/shots/`（该目录 `.gitignore` 忽略，不进仓库）：

`r11_01_login.png`、`r11_02_private_jd.png`、`r11_03_profile.png`、`r11_04_training_entry.png`、`r11_05_full_session.png`、`r11_06_weakness_options.png`、`r11_06_weakness_session.png`、`r11_07_recheck_options.png`、`r11_07_recheck_session.png`、`r11_08_report_page.png`、`r11_08_report_generated.png`、`r11_08b_report_provenance.png`、`r11_09_jd_edit.png`、`r11_10_second_user.png`。

驱动脚本同样在 `.runtime/ui/`（`r11_e2e_part1..4.mjs`、`r11_check_report.mjs`），复用第九轮的 `cdp.mjs`。

---

## 七、UI 验收发现的问题（含已修）

1. **报告页把用户自填的公司名当成雇主展示**（已修，`ccd7ff8`）。截图里报告标题是"示例科技 · 消息可靠性后端工程师"，通篇没有任何来源说明；而"示例科技"是候选人在 JD 里自己写的。修法：报告详情 payload 补 `job_target_id` / `job_target_version`（原来只有 `context_snapshot_id`），页头在私人来源时明确写出版本与"未经企业核实、不代表真实雇主"。公开岗位报告标题不变。
   - 这个问题**类型检查与第十轮全部测试都是绿的**，只有真正打开页面才看得见。
2. **训练页模式不可用时的解释**：现状已有"当前没有符合此模式的有效知识目标。可选择完整模拟面试，不会凭空生成复测任务。"，本轮未再改。
3. **私人入口的可发现性**（未改）：`用这个岗位开始训练` 只在"打开某个目标"后出现，列表行本身没有入口。属可改进项，不属于误导，本轮不动。
4. 报告页正确显示 `DEMO · Replay 回放模式` 标记，没有把离线验收伪装成真实模型结果。

---

## 八、双用户隔离结果（事实）

- 服务端：目标详情 404、训练反馈 404、列表 0 条（截图 `r11_10_second_user.png`）。
- 测试侧：`test_job_target_ownership.py`（5 条，会话创建 + 训练工作台 + list/detail/match/update/delete）与 `test_private_recheck.py` 的 404 断言本轮全绿。
- 归属检查顺序仍是第十轮修复后的语义：缺失与他人目标统一 404，正常用户仍 `PROFILE_REQUIRED`。

---

## 九、数据库与真实调用

| 数据库 | 用途 | 状态 |
|---|---|---|
| `interview_agent`（生产） | 正式 | 未被连接/迁移（本轮所有命令都指向隔离库） |
| `interview_agent_wb_r11_test_20261112` | 业务回归 | `0027`，67 表 |
| `interview_agent_v21_wb_r11_20261112` | 第一遍迁移用例 | 跑完 → `0027`，**已非空，不能二次使用** |
| `interview_agent_v21_wb_r11b_20261112` | 第二遍迁移用例 | 同上 |
| `interview_agent_wb_r11_e2e_20261112` | 浏览器验收 | `0027`，2 个用户、1 个私人目标、14 场训练会话 |
| `interview_agent_thinking_long_20261110` 等 | 封存现场 | 只读；本轮只验证了名字判定与拒绝路径，**没有连接** |

真实模型 HTTP：**0**（验收 API 在 `httpx.AsyncClient.request` 上直接抛错，任何外呼都会失败而不是静默发出）。未使用历史 Live Capture 回放。

前端：`vue-tsc --noEmit` 0 错误，`npm run build` 通过。

---

## 十、已知限制与下一轮

1. **简历上传未走真实链路**：画像由验收启动器用 replay profile agent 预置；上传 → 解析 → 画像需要 Worker 与真实模型，本轮额度为 0。页面显示的是真实画像版本，但"上传"这一步没有在本轮浏览器里跑。
2. **`serve_offline_acceptance.py` 与 CDP 脚本分别在仓库与 `.runtime/`**：前者入库可复用，后者按第九轮惯例留在被忽略目录。
3. **私人入口可发现性**：见 §七.3，未改。
4. **记忆仍无岗位维度**：私人训练与公开岗位训练共用 `(user, topic)` 根；同一 topic 的不同岗位观察只靠 scope 区分。
5. **`training_mode` 缺 `training_request_key` 时绕过幂等**：契约风险仍在，未改。
6. **下一轮建议**：先做 `delivery.py migrate` 在团队文档/README 里的授权说明（现在不传参数会拒绝启动）；再补私人岗位的 Skill 推荐接口；请求记录与业务审计三向 join 仍按 `docs/89` §10.1 的方案等批准。
