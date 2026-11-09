# V2.3 个性化面试训练中心

日期：2026-10-26。状态：隔离 PostgreSQL、真实前后端 API 与 Playwright Replay 验收完成；本轮未调用收费模型，未迁移正式库，未提交或推送 Git。

## 1. 复用与改动边界

审查本地 V2.1.1 / V2.2 后，继续复用六 Agent、Skill Registry、合法目标—题源组合、SessionController、能力聚合及用户审批。没有新增 Planner、Memory Agent、题库或模型账本。

原系统已有能力状态、MemoryGuidedPlan、面试创建、报告和更正撤销；缺少的是用户主动选择训练目标、创建时绑定该目标，以及完成时可复核的前后对比。本版补齐这些交互，而非重新实现记忆。

三种模式：

- **完整模拟面试 FULL**：保留原 Blueprint 与预算控制，考察该岗位的合法目标。
- **薄弱项 WEAKNESS**：仅可选择当前 GAP_SUSPECTED / PRACTICING 目标；将 Blueprint 限定为该合法目标，Main 继续选择问题和策略。
- **独立复测 RECHECK**：仅可选择 CONFLICTED / RECHECK_DUE 目标；不提供开场提示，温和引导风格不能用于独立复测。后续提示后的回答仍不成为独立验证。

已撤销的推断不能自动恢复为专项推荐。普通完整面试仍可考察岗位相关技术，这不等于恢复被撤销的历史推断。

## 2. 用户操作与真实 API

| 用户操作 | 后端调用 | 行为 |
| --- | --- | --- |
| 从岗位推荐进入训练 | 原匹配详情 API + `/training?job_id=…&recommended_skill_id=…` | 只传公开岗位和 Skill 引用，不复制历史 Prompt |
| 选择岗位与专业方向 | `GET /api/v1/interview/targets`、`GET /api/v1/interview/interviewer-options` | 继续只使用已发布岗位与动态 Skill 目录 |
| 查看训练建议 | `POST /api/v1/interview/training/workbench` | 复用创建时的上下文规则，读取有效能力状态和来源；预览事务最终 rollback |
| 选择模式与知识目标 | 现有 `POST /api/v1/interview/sessions` | 新增可选 training_mode / training_knowledge_id / training_request_key |
| 准备首题、回答和恢复 | 原 start / messages SSE / session detail 接口 | 不创建第二套 Controller 或 Answer 状态机 |
| 生成 Coach | 原报告接口 | 独立于训练反馈，不重新产生知识观察 |
| 查看训练变化与实际考察 | `GET /api/v1/interview/training/sessions/{id}` | 先验证归属，读取完成时已保存的反馈与有界审计投影 |
| 更正、排除评价、撤销推断 | 现有成长记录页面及能力记忆接口 | 不允许用户直接伪造 VERIFIED；原 MemoryProposal 审批仍保留 |

创建接口旧字段及旧 Session 行为兼容；未冻结训练起始状态的历史面试返回 `available=false`，不追补虚构成长结论。训练必须启用自适应模式；工具设计面试不与训练模式混用。当前没有合法目标时明确提示不可创建，不生成假训练任务。

## 3. 数据链路与一致性

```text
岗位/JD + 选定 Skill + 有效能力版本
  → 工作台合法目标与原因
  → 用户选模式/目标
  → 新 Snapshot：训练意图、起始状态、Blueprint、MemoryGuidedPlan
  → 原 Main：选择合法组合并生成问题
  → 原 Controller：校验组合、版本、预算、幂等后提交
  → 原 Evaluation：独立评价本题实际回答
  → 原知识观察与能力聚合
  → 完成时保存训练前后反馈
  → 原 Coach / MemoryProposal + 用户选择下一场
```

没有 migration。正式库保持 0025；隔离测试库使用已有 0026。

- Snapshot `context_json.training` 固定模式、目标、创建请求指纹和起始能力版本；旧 Snapshot 不修改。
- 专项目标从完整合法 Skill 范围选择，再缩小 Blueprint。临时读取更完整的目标目录不增加实际 question_count，也不扩大某个目标的允许题源集合。
- 新 Session 的 MemoryGuidedPlan 按最终目标范围生成，沿用原冻结时钟。
- 创建使用用户级 PostgreSQL transaction advisory lock 和用户归属的 Snapshot request_key 查询。相同键相同内容返回同一 Session，不同内容返回 409。浏览器保存待创建请求键，丢失响应后可以安全重试。
- 原 Answer/Evaluation 幂等、状态版本、失败恢复不另起实现；Main 失败可复用已验证 Evaluation。
- `completion_json.training_feedback` 在完成事务中保存，报告 regenerate / 刷新只读该结果。JSON 使用新对象赋值，避免 ORM 未追踪嵌套变更导致反馈未落库。
- “独立作答条数”是尝试数，不是独立正确支持数。能力状态仍由既有聚合规则决定；提示后正确不升级为 VERIFIED。
- 冲突只有现有规则产生明确解决记录才显示解决；否则 OPEN 或 UNKNOWN。无有效评价显示没有足够新观察。

预览事务回滚不发布投影数据，但不是 PostgreSQL 的纯只读事务：复用上下文构造时可能短暂 flush，并可能消耗数据库序列值。没有调用模型。

## 4. 记忆使用解释

复用 AgentRun / StepRun / InterviewAgentCall / Question，未增加审计表。

1. StepRun 记录冻结计划指纹、候选知识目标与能力版本、已准备的模型输入指纹。
2. Main 的 validated artifact 保留 target / strategy / candidate_id。
3. 已提交 Question 关联 main_agent_run_id；Controller 拒绝可记录 REJECTED 原因。
4. 完成反馈关联实际 Question / Evaluation / KnowledgeObservation。

展示区分：候选计划、已准备输入、相关或其他目标选择、Controller 接受/拒绝/未达到/未知、有效评价和最终能力变化。没有证据的阶段保持 UNKNOWN，不宣称模型一定关注了计划，更不宣称因果收益。被输入或被选择次数不参与能力评分。

## 5. 文件清单

新增：

- `apps/api/src/interview_api/modules/interview/training.py`：工作台、冻结、创建幂等、完成反馈、只读审计。
- `apps/api/tests/test_v23_training.py`：训练 service / API PostgreSQL 回归。
- `apps/api/scripts/prepare_v23_demo.py`：空隔离库一次性 DEMO 准备，重复执行拒绝，禁止 LIVE。
- `apps/web/src/api/training.ts`：训练 DTO 和接口。
- `apps/web/src/pages/TrainingPage.vue`：训练工作台和安全创建。
- `apps/web/src/components/TrainingFeedback.vue`：完成反馈和下一步入口。
- 本文。

修改：

- API interview `schemas.py`、`context.py`、`core_service.py`、`controller.py`、`router.py`。
- `public_career/research/audit.py`：只在训练 Main 请求补充输入审计，不改变 Research 提取。
- Web `router/index.ts`、`i18n/zh-CN.ts`、`pages/JobMatchingPage.vue`、`pages/InterviewChatPage.vue`、`pages/InterviewReportPage.vue`、`components/CapabilityMemoryPanel.vue`。
- `apps/web/tests/product-ux.test.mjs`：共享导航新增训练入口，保留原断言。
- `docs/README.md`：索引。

关键函数：`TrainingService.workbench/create/feedback` → `build_context` → `freeze_training` → 原 `MockInterviewService.start/answer` → `SessionController.commit_answer/complete` → `completion_feedback`。

## 6. 本次真实浏览器 Replay 记录

数据库：`interview_agent_v21_v23_browser_20261026_a`，Alembic 0026。真实 Vue / FastAPI / PostgreSQL / Redis，不拦截 HTTP 响应，不 Mock Controller。仅模型边界使用预先固定 Replay；CompatibleModelProvider.generate 在演示进程中被禁止。

| 旅程 | 用户/业务记录 | 实际结果 |
| --- | --- | --- |
| 初次缺口 → 成长记录 → 专项 | first 用户；Session 6 FULL → Session 7 WEAKNESS | 6：UNKNOWN→GAP_SUSPECTED；7：2 道题、2 回答、2 Evaluation，围绕 ACK/事务深挖；提示后补充正确，GAP_SUSPECTED→PRACTICING，不是 VERIFIED |
| 历史冲突 → 独立复测 | conflict 用户；固定历史 Session 3/4/5 → Session 8 RECHECK | 新支付回调故障场景，不提前展示答案；1 回答/1 Evaluation；CONFLICTED→CONFLICTED，仍 OPEN，不强行抹除矛盾 |
| AI 岗位 → AI Skill → 报告 → 下一场 | ai 用户；MatchRun 1 / Job 3 → Session 9 FULL | 岗位和 ai_agent_application 自动带入；显示 Python、RAG、Agent 等合法目标，实际选 Python 工程；1 回答/1 Evaluation，UNKNOWN→PRACTICING；报告回链正确 |

以上均 COMPLETED，active_question_id 为空，没有重复有效 Answer/Evaluation；Session 7 第二题刷新、Session 9 报告刷新恢复。四场训练均保存完成反馈；Session 7/8/9 通过真实页面调用 Replay Coach 生成报告。DEMO / Replay 标签可见。

Playwright CLI 的实际点击、填写、跳转快照在 ignored `.playwright-cli/`，截图在 ignored `output/playwright/`：

- `v23-01-first-gap.png`
- `v23-02-weakness-result.png`
- `v23-03-report-coach.png`
- `v23-04-independent-recheck.png`
- `v23-05-conflict-result.png`
- `v23-06-ai-report.png`
- `v23-07-return-workbench.png`

已查看工作台截图核对布局。第三条旅程结束时浏览器 console：0 errors / 0 warnings。命令层曾出现 Playwright eval 参数格式错误，改用 CLI 支持的操作完成；未把失败的自动化命令算作业务成功。

## 7. 测试与工程核验

- 后端完整回归分两批在独立 `interview_agent_v21_v23_full_20261026_a` 执行；使用 `scripts/run-v21-tests.py`，包括 retained V1.4.2 真快照离线回归。
- 首批 135 passed / 0 failed / 0 skipped（80.45秒）；第二批最终 380 passed / 0 failed / 0 skipped（106.87秒）。合计 **515 passed / 0 failed / 0 skipped**。每个命令都有175秒测试上限，未并行运行整批测试。
- 新增训练专项覆盖：无历史、合法范围、训练必须自适应、并发同键创建、目标拒绝、跨用户404、提示后改善、冲突未解决、Main 未采纳计划、非法组合拒绝后复用 Evaluation、安全重试、报告 regenerate、用户撤销、跳过无观察。
- 既有 V2.2、Director、V1.2 工具、审批、Skill、共享资料、幂等和事务回滚测试保留，未降低断言或 validator。
- 前端 Node 测试 43 passed / 0 failed / 0 skipped；包含逻辑与静态 UI 契约测试，不能代替上述真实浏览器旅程。
- `pnpm --dir apps/web build`：vue-tsc / Vite production build 通过。
- 修改的 Python 模块与测试 `py_compile` 通过；`git diff --check` 通过。新文件另行检查尾部空行及行尾空白，未改历史 migration 或全局 whitespace 设置。
- 正式库只读核对前后均 0025，64 表行数和逐表内容指纹无变化。留存 `.runtime/acceptance/v23_formal_20261026_a.json` 与 `v23_formal_after_20261026_a.json`。
- 审计实际 provider/model 仅 `replay/fixture` 和 `replay/DEMO_V21_NOT_LIVE`，本轮收费请求 0。

## 8. 亲自演示

当前隔离入口：<http://127.0.0.1:5177/training>。

三个 DEMO 账号：`v23.first@example.com`、`v23.conflict@example.com`、`v23.ai@example.com`；密码统一为 `DEMO-v23-local-only`，仅用于此隔离库，不是真实账号凭证。

推荐路径：ai 用户登录 → `/jobs/matches/1` → AI 岗位“选择个性化训练” → 选择模式 → 开始 → 回答 → 查看报告 → 下一步训练。既有结果可看 `/interview/reports?session_id=7`（first）或 `session_id=8`（conflict）；所有页面仍检查账号归属。

服务已启动。如服务退出，分别在两个终端从仓库根目录重启：

```powershell
# 仅连接已经准备好的隔离库，不加 --prepare，不初始化正式库
$env:REDIS_URL='redis://localhost:6379/15'
uv run --project apps/api --no-sync python apps/api/scripts/v21_acceptance_runtime.py --database interview_agent_v21_v23_browser_20261026_a --memory-replay
```

```powershell
node scripts/v21_acceptance_web.mjs
```

首次重新准备必须使用新的空隔离库，先用现有 runner 在该库升级0026，再运行 `prepare_v23_demo.py --database <新隔离库名>`。脚本拒绝正式库和有用户的库，不清理或覆盖之前验收记录。当前数据库无需再准备。

## 9. 边界和正式发布限制

- 本轮证明的是 **Replay 决策 + 真实业务事务/API/浏览器**，不是新一轮 DeepSeek 的训练效果验收。旧 V2.1.1 / V2.2 Live 记录分别见50和53，不替代本版 Live 验证。
- Replay 示例问题不代表真实公司历史题。AI 旅程没有强制固定考 Agent：其合法 Skill 包含 Python 工程，实际模型边界 Replay 选择该目标，如实记录。
- 正式库仍0025，没有部署 V2.2 所需的0026能力表；不能直接宣称正式账号已可使用新训练中心。正式升级仍须按57单独授权。
- 没有新增训练迁移，没有改 Profile/Research Evidence contract、Prompt、模型配置、`.env`；没有自动触发搜索或收费匹配生成。
- 工作台未完成的表单草稿不保证刷新后原样保留；已创建 Session、训练意图与完成反馈可以恢复。
- 本版下一步建议采用现有确定性规则，尚不是经过统计校准的效果预测或新的学习算法。记忆选择不能证明因果增益。
- 独立复测完成不保证冲突立即解决；条件不足保留待复核，符合既有能力规则。

完成后停止，不继续 V2.4。

后续独立授权的完整Live批次已执行并因真实结构化错误停止，见[59验收失败记录](59_v23_live_full_interview_acceptance.md)。本文件的Replay通过不等同于该Live批次通过。
