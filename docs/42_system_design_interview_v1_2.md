# V1.2 Agent 工具驱动的系统设计面试

日期：2026-10-19。当前实现与自动化验收完成情况见下文；真实收费模型测试尚未执行，等待单独费用授权，不能据 Replay 宣称模型效果通过。

## 范围与真实边界

开始时检查了 Git、docs/40、docs/41 和实际面试/报告代码：仓库未开始 V1.2。已有所有 tracked/untracked 改动保留；本轮不提交、不推送，不改 `.env`、密钥、模型名称、历史 migration 或正式业务数据。

最小可执行业务场景为“限量名额预约系统”。用户除文字解释外，明确提交请求去重、名额保护及容量的结构化方案声明。模型不是事实解析器，不能替用户暗中补全方案。没有代码沙箱，也不宣称验证真实项目代码。

这是严格结构化的工具选择闭环，不是新 Agent、Planner、Supervisor，也未改造为原生 Chat API `tool_calls` 协议。沿用六 Agent，Main 负责工具选择与追问，Evaluation 负责评分，Controller 持有业务状态。

## 业务链路

```text
选择当前岗位 / Skill / 轮次 / 风格 + 系统设计模式
  → 原不可变 InterviewContextSnapshot 固定场景与权限预算
  → Main 生成开场问题
  → Answer + DesignPlan 原子持久化与版本化
  → Main 的 select_tool 阶段返回 ToolDecision
  → 严格工具名 / 参数 / 当前方案版本校验
  → Controller 预留并记录执行
  → 有界确定性工具执行（数据库事务外）
  → Controller 校验 state_version / 方案版本并提交结果
  → Evaluation 精确引用真实结果 ID 和 PASS/FAIL
  → Main 引用结果追问；Controller 决定追问/切题/结束
  → Report 展示逐题评价、方案修订与程序结果
```

Main 的两个阶段都沿用 `interview_main` route 和 `InterviewMainAgent` 审计角色，没有新增 Agent。V1.2 使用版本 1.2.0 的专用 Prompt；普通会话仍走原 Skill/旧 Prompt 路径。Evaluation 保留原五维 1–5。

## 工具和程序权限

- `verify_idempotency`：相同业务请求重复/竞争提交，检查是否出现重复预约。
- `verify_capacity`：不同业务请求争用容量，检查是否超售。

工具运行实际 Python 状态转换并返回事件 trace、提交数、接受数、violation 和 passed。模拟调度明确为所有非原子检查先于写入，原子策略则逐个执行不可分转换。因此 `CHECK_THEN_WRITE` 不能被当成原子保护。

每次 2–40 请求，容量 1–20；容量工具必须请求数大于容量，不能用不充分样本刷通过。一次回答最多选择一个工具，一场最多六次实际执行，失败尝试也计数。单次工具执行 2 秒 deadline。无 shell、网络、任意 SQL、用户代码或表达式执行。Skill 文件不能自行授予额外工具权限。

这是声明策略下的有界交错模拟，不是负载测试、真实数据库事务验证或对所有调度的完备证明。通过只表示该次模拟未发现所验证违例。

## 数据与并发/恢复

无需新表、新字段或 migration，复用：

- `InterviewContextSnapshot.context_json.system_design`：固定场景、工具描述、版本及预算。
- `InterviewSession.planner_trace_json.design_revisions / design_tools`：有限修订与调用账本。
- `InterviewMessage.metadata_json.design_revision / tool_evidence`：回答与证据的精确关联。
- `InterviewEvaluation.artifact_json`：工具结果引用与观察结论，原评分字段不变。
- 下一题 `candidate_refs_json.tool_result_ids`：追问的工具依据；仍保留原题源约束。
- 既有 `ExperienceAgentRun / StepRun / InterviewAgentCall`：选择、评价、追问调用的模型审计。

所有业务写入都经 SessionController。工具和模型调用期间不持有数据库长事务/行锁。短事务内重新校验 session state_version、最新方案版本及调用状态；迟到结果返回 `DESIGN_STALE`，不能覆盖当前结果。

Answer 复用原幂等键；相同键不能更换文字或方案。成功工具证据在精确同一 Answer/方案重试中复用；已完成 Answer 返回已提交事件，不重复调用模型或工具。工具失败不产生 Evaluation、不推进、不 COMPLETE。断连取消持久化失败状态，刷新读真实状态；失联请求的旧 RUNNING 调用在恢复时标为 interrupted，旧请求被版本隔离。

新增工具选择使单轮最多三次模型逻辑调用，V1.2 的回答恢复 lease 据此有界覆盖，不改变正式模型 timeout。仍受原全场时间/Token/题数/追问预算限制。

## API / 前端

原创建接口新增 `design_interview: true`，要求已选专业 Skill；默认 false，旧调用兼容。

原 SSE Answer 请求新增可选结构：

```json
{
  "question_id": 1,
  "idempotency_key": "stable-request-key",
  "content": "我采用先查名额再写预约的方案",
  "design_plan": {
    "deduplication": "NONE",
    "capacity_guard": "CHECK_THEN_WRITE",
    "capacity": 1
  }
}
```

系统设计模式必须有方案；普通模式不能混入方案。没有新增可以绕开会话所有权直接执行工具的 API。调用及结果随受所有权保护的会话/报告读取；普通用户不能读取他人工具结果。

前端沿用既有面试创建流程，增加模式选择及方案表单，显示“验证中”阶段；已提交重试不得改方案。刷新恢复最近方案/待重试方案；完成后表单禁用。面试与报告复用 `DesignTrace.vue` 展示各版方案和真实结果；不会提前展示参考答案。

## 专用测试环境与启动

正式 `interview_agent` 数据库未写入 V1.2 验收数据。专用库：`interview_agent_v12_acceptance_20261019_a`，另有全量回归专用库。数据与账号都标为 DEMO，保留供复现，不自动删除。

准备全新专用库（已有同名库会拒绝覆盖）：

```powershell
# apps/api 内
.venv/Scripts/python.exe scripts/v12_acceptance_runtime.py --database interview_agent_v12_acceptance_<unique_attempt> --prepare
```

启动已有验收库：

```powershell
# apps/api 内，监听 127.0.0.1:8005
.venv/Scripts/python.exe scripts/v12_acceptance_runtime.py --database interview_agent_v12_acceptance_20261019_a
# 仓库根目录，另一个终端，监听 127.0.0.1:5175
node scripts/v12_acceptance_web.mjs
```

浏览器 `http://127.0.0.1:5175/login`。仅该专用库的公开 DEMO 账号：`v12.demo@example.com`，密码 `DEMO-v12-local-only`，不是正式用户凭证。真实登录、鉴权、API、数据库、模型结构化层、Controller、工具都保留；仅模型由显式离线 Provider 替换。未使用 Playwright route.fulfill 或静态 DEMO 页面代替 E2E。

普通 LIVE 环境继续使用原一键命令 `./scripts/start-interview-platform.ps1`；停用原命令 `./scripts/stop-interview-platform.ps1`。本轮没有改启动脚本默认模式。真正开始普通 LIVE 面试仍可能收费。

## 实际验收记录

### 单元 / PostgreSQL / Replay

- 首轮纯工具/contract：8 passed、5 skipped（当时未配置 PostgreSQL 变量，不能算集成通过）。
- 专用 PostgreSQL 首轮 V1.2：13 passed。
- 增加非法模型决策审计、并发重试、实际 deadline 与真实六次额度消费后，V1.2 + V1/V1.1 定向测试：49 passed，14.59 秒。
- 中间全量回归：385 passed，144.46 秒；最新完整回归：**389 passed，143.78 秒**，专用库 `interview_agent_f4_regression_v12_final_20261019`，包含所有新增专项。
- 前端 Node tests：38/38 passed，包含状态函数实际执行与明确标注的静态 UI 合约检查。
- Vue TypeScript / production build、Python compileall、修改文件 whitespace 检查通过。

### 真实浏览器、真实 API、Replay 模型

1. 登录专用 DEMO 账号，创建 Session 12：Java Skill / 项目深挖 / 标准 / 三轮。
2. 第 1 版无去重：Main 的离线 Provider 选择 `verify_idempotency`，程序执行 3 请求、接受 3 次，失败；评价引用真实结果，继续补问。
3. 第 2 版原子去重但先查后写名额：改选 `verify_capacity`，3 次接受、容量 1，失败；追问原子扣减和事务边界。
4. 第 3 版原子去重 + 原子名额：程序接受 1/3，通过；会话正常 COMPLETED，3 Answer / 3 Evaluation / 3 修订 / 3 工具结果。
5. 真实点击生成 Report 3，COMPLETED；页面显示统计、逐题评价及三版程序结果，刷新可恢复。
6. Session 51：在测试 Provider 的一次性 2 秒延迟期间主动刷新，观察到 `interview_request_cancelled`、回答已保存和安全重试入口；用原键重试，再提交第二版后完成。真实 API 核对仍为 2 Answer / 2 Evaluation / 2 修订 / 2 工具调用，没有重复。
7. 用另一测试账号经真实注册/登录访问 Session 12 和报告均为 404；匿名 Session API 为 401。该安全负例产生的 HTTP console errors 是预期结果，不伪称所有请求均 200。

截图：`output/playwright/v12-real-api-report.png`。Playwright snapshots/console 日志保留本地 ignored 目录。Session 12 / 51 仅属于上述专用数据库，不能混同正式用户的同号 Session。

### 发现与修复

- 新增验证阶段未完整加入前端状态联合类型、loading 和禁止重复提交集合：production TypeScript 检查发现，统一补齐并重建通过。
- 非 pending 情况刷新时方案表单原本回到默认值：修复为恢复最近持久化修订，避免用户误提交退化方案。
- 完成会话的方案表单原本仍可编辑：补禁用，不改变历史。
- 补强程序边界：拒绝无执行记录却引用工具/观察结论；容量验证请求数必须超过容量；追问题持久化工具 ID。
- 浏览器脚本曾精确等待“面试结束”而实际标题带 🎉，导致 locator timeout。通过真实 API/页面核对确认业务已完成，改为等待可见报告入口并核对持久化计数，没有改业务来迁就错误定位器。

## 真实模型验收：未执行，待费用授权

只读检查当前配置：Main / Evaluation / Coach 均为 `openai_compatible / doubao-seed-2-1-turbo-260628`；面试输出上限 3000，当前面试调用 timeout 180 秒。本轮没有改变这些配置。

计划两个隔离场景，各两轮：

- 无幂等 → 重复请求失败 → 修订 → 重新验证。
- 有幂等但非原子名额 → 并发工具失败 → 修订 → 重新验证。

完整包含开场、工具选择、评价、追问、修订及 Coach 时预计 14 个逻辑模型请求；每个结构化请求最多一次现有 constrained repair，最多 28 次实际 HTTP 调用。全批次截止 15 分钟，使用当前配置，不无限重试；业务/证据失败立即停止并保留事实，不筛选成功样本。价格未配置或不可确认时不编造金额/token usage。

只有获得用户明确费用授权后才执行；当前不宣称真实模型能正确选工具、使用结果或自然追问。Replay Provider 的选择规则不能替代这一结论。

## 最终一次人工体验（真实模型验收通过后）

10–15 分钟：登录 → 选择已发布岗位 → 选 Skill/轮次/风格并开启系统设计 → 设置三轮 → 提交无去重/先查后写 → 看真实程序结果及追问 → 改原子去重但保留非原子名额 → 改原子名额 → 完成 → 打开报告、展开各版 → 刷新检查恢复。

在隔离 Replay 环境体验不会收费，但只能验产品流程；在正常 LIVE 环境体验会调用当前模型。两者必须明确区分。费用授权前不要求用户进行中间手工测试。

## 文件与局限

新增：`modules/interview/system_design.py`、`design_prompts.py`、`tests/test_v12_system_design.py`、`tests/v12_support.py`、`apps/api/scripts/v12_acceptance_runtime.py`、`scripts/v12_acceptance_web.mjs`、`apps/web/src/components/DesignTrace.vue`、`apps/web/tests/system-design.test.mjs`、本文档。

增量修改：面试 `schemas.py / artifacts.py / context.py / agents.py / controller.py / core_service.py`，报告 `statistics.py / service.py`，前端 `api/interview.ts`、`types/interview.ts / interview-report.ts / interview-processing.ts`、`pages/InterviewChatPage.vue / InterviewReportPage.vue`、`i18n/zh-CN.ts`。

无表结构变更、无 migration、无新依赖、无新 Agent。暂不扩展 V1.3、代码执行、任意系统设计场景、通用任务引擎或搜索。

当前仅覆盖一个明确场景和三类策略声明，不能从自由文字推断实际架构，也不证明真实代码正确。规则模拟只覆盖一种明确竞争交错；最终模型自然语言质量、真实工具选择与证据使用仍需上述有限 LIVE 验收。适合展示“模型决策—受控工具—环境反馈—修订—评价”的工程边界，但必须如实说明模拟及个人项目规模。

## 后续 LIVE 验收更新（2026-10-19）

以上“未执行/待授权”是原交付时的事实，予以保留。用户随后明确授权有界 DeepSeek 请求，现已完成 14 逻辑/14 HTTP、76,322 Token、115.803 秒的真实批次：两场工具/修订/评价均完成，A 报告成功，B 独立 Coach 因第 15 次会超过逻辑预算而未执行。详见 `docs/45_deepseek_v12_v13_live_acceptance.md`；不能将它扩大表述为全部场景/风格的 LIVE 验收。
