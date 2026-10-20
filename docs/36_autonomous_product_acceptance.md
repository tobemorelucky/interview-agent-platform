# Autonomous Product Acceptance（2026-10-06）

## 范围和真实性

本轮只修复已有产品的普通缺陷，不新增 Agent、migration 或业务功能，不 commit/push。
进入任务前的 Profile、人工来源导入、Research 等未提交工作全部保留，本轮不继续修改它们。

验收使用正式业务库 `interview_agent`、真实账号 user 38、Resume 38、CandidateProfileVersion 1，
以及已经人工导入并审核的公开岗位/题库。模型调用均为
`openai_compatible / deepseek-v4-pro-ga-260813`，没有使用 Replay Model 或 Doubao。
Live Search 始终冻结；人工来源不冒称 Live Search。

**面试回答是明确标记的自动验收测试回答，不是真实用户作答或生产项目成果。**
故意构造不完整、完整技术方案、很短和答偏回答以检查产品行为。
没有将回答中的技术方案写回 Profile，也没有虚构生产指标。
回归中的模型故障注入和六题预算 fixture 是离线测试，不声称发生了真实 provider timeout。

## 浏览器与业务覆盖

通过 Chrome 中的 Playwright 页面交互以及正常 API/service 流程执行，数据库仅用于核对。
没有 SQL 修改实体归属、伪造 Token、绕过权限或直接 INSERT 最终业务实体。

| 场景 | 实际结果 |
| --- | --- |
| 登录、简历与画像读取 | 真实账号和 Profile 1 可读；本轮没有重新生成画像 |
| 四个真实岗位 | 华为 AI应用工程师、工行广东省分行科技菁英、嘉兴移动 AI工程师、湖北移动 AI研发工程师均可查看 |
| 正常匹配 | MatchRun 3 COMPLETED，真实 DeepSeek，定性推荐，不展示百分比 |
| 历史推荐启动面试 | 只传 company/job，按 M4 当前规则创建新 Snapshot，没有复制旧 MatchRun 输入 |
| Session 54 | 华为三题短面试，三 Answer/三 Evaluation，COMPLETED，包含两次 FOLLOW_UP |
| Session 55 | 工行三次有效回答/评价，NEXT 和 FOLLOW_UP；暂停超过真实时长预算后正常跳过未回答题并完成 |
| Report / Coach | Report 2（华为）、Report 3（工行）均为真实 DeepSeek 成功报告 |
| Memory 决定 | 正常页面完成批准、修改后批准、忽略及取消确认；没有自动 commit |
| 第二场 Memory | Session 55 固定 Memory 30/31，与 PerformanceObservation 7/6/5 分开 |
| 待批准不进入上下文 | Session 56 创建时 Report 3 提案仍 PENDING，不进入 approved memory |
| 创建后批准不漂移 | 后来批准产生 Memory 32；Session 54/55/56 Snapshot fingerprint 不变，未动态加入 32 |
| 页面刷新 | 回答/评价/当前题和报告决定从已持久化状态恢复，不重复生成评价 |
| 失败恢复 | 真实 Coach validator 失败保留历史，面试仍 COMPLETED；重试成功；离线 Eval/Main timeout 覆盖同键安全重试 |
| DEEP_DIVE | 状态、父题关联与预算由现有 Replay/Controller 回归覆盖；本轮真实模型没有选择 DEEP_DIVE，不虚报现场触发 |

Session 54 所有题为 COMPLETED。Session 55 三题 COMPLETED、最后未回答题 SKIPPED，
两场均无 PENDING/ASKED/ANSWERED 状态遗留，active_question_id 为 NULL。
Session 56 是为用户保留的华为第一题入口，IN_PROGRESS、零 Answer/Evaluation；这是待手测会话，不是结束状态泄漏。

## 发现的问题和最小修复

### 1. 推荐展示

继续使用纯 presentation adapter，而不改底层 Evidence Matrix 或重新写推荐事实。
按 requirement 聚合、去重支持关系；相同 Fact 合并时保留全部真实 Evidence references。
Java 不因 JavaScript 字符串片段误变为匹配亮点。
硬资格风险优先显示真实要求值，湖北的“博士”不会被技术缺口列表挤掉。
没有材料证明仍表示未覆盖，不表示候选人不具备。
原文、offset、版本、排序和指纹留在默认关闭的详细依据中。

### 2. 面试上下文重复与预算

完整 frozen Snapshot 同时包含 profile、normalized records、candidate_facts 和 candidate_evidence，
多份重复表示会放大模型请求和保守 admission estimate。
新增 model-facing projection，仅移除 profile/normalized 的重复表达，并从本场固定数据补回 Fact label/state。
保留全部 Fact ID、statement、Evidence key/excerpt/kind、Requirement、公开题和两类 Memory 上下文。
Evidence state 不提升；不重新查询最新 Profile/JD/Memory；不修改 canonical Snapshot。

Session 55 序列化 Snapshot 44114 UTF-8 bytes，模型 projection 25350 bytes；
32 Fact、32 Evidence 保留，Evidence keys/excerpts 完全相同。
这是请求表示去重，不是裁剪真实证据，不修改时长、Token 预算、timeout 或 validator。
离线审计形状的六题回归验证默认题目预算不再被重复表示堵塞。

### 3. 问题与追问体验

InterviewMain prompt 新增 1.0.3，保留旧版本。
要求一次一个主要问题，FOLLOW_UP 聚焦回答缺失点，避免重复索要用户已说明不存在的生产数字；
DEEP_DIVE 要深入机制/失败边界/取舍而不是换个说法重复提问。
公共题只作为 intent，实际问题自然口吻；公开题 ID/fingerprint 仍受 Controller 校验。
参考答案在有效 Evaluation 之前不可见。

追问计数错误曾把不同主问题的追问累加显示到当前题。
Controller 和浏览器按 parent/root 计算同一主问题的 FOLLOW_UP/DEEP_DIVE 消费，历史题只计算其当时序号。
不改变现有上限、不允许无限追问。

### 4. Coach 失败和可恢复性

华为第一次 Report 1 因 `COACH_COMPUTED_NUMBER` 失败，失败 history/audit 保留。
当时没有保存安全 field diagnostics，不能事后猜测具体字段。
现有禁止模型编统计数字的 validator 没有放宽。
新增 prompt 1.0.1 更清楚说明数字边界及测试回答语义；numeric guard 记录安全 field path/rule/matched fragment。
页面显示中文错误和安全重试提示，不展示内部异常或整份模型响应。
华为重试成功为 Report 2；工行首轮成功为 Report 3。
工行报告的“安全重试 / 查看当前报告”复用 Report 3，没有新增 Agent 调用或等价报告。

### 5. Memory 审批交互

浏览器原生确认框曾阻塞自动化；已取消，取消时未写入 Memory。
批准/忽略改为显式页面确认与取消，仍使用既有权限、PENDING 校验和单事务 APIs。
修改后批准保留原文、修改内容、用户确认及 provenance。
成长记录摘要等于正文时不重复显示。

| Proposal | 决定 | Memory |
| --- | --- | --- |
| 1 / Report 2 | CORRECTED；修改内容明确为自动验收练习目标，不代表长期能力 | 31 |
| 2 / Report 2 | APPROVED | 30 |
| 3 / Report 3 | REJECTED | NULL |
| 4 / Report 3 | APPROVED | 32 |

均有真实 Evaluation/Report 支持，没有自动写长期技能事实。

### 6. 登录过期误称回答已保存

跨小时暂停后 JWT 到期：Session 55 的 SSE POST 返回 HTTP 401，尚未进入业务处理，
没有写入新 Answer 或 Agent audit。旧前端却保留乐观气泡并显示“已保存回答”，属于真实 UX 错误。
Fetch/SSE 现在识别 401，与 Axios 共同清理过期授权和 Pinia 状态；跨标签正常登录同步授权状态。
401 时移除未被服务端接受的乐观气泡、保留输入草稿，提示尚未提交并提供正常重新登录入口。
只有 DB pending_answer 确认存在时才提示已保存；未知网络结果保留同键重试，不假称已持久化。
用户通过正常登录页恢复账号，没有重置密码、伪造认证或改数据归属。

Session 55 的真实 7200 秒预算也已到期，不把暂停时间冒称模型延迟。
通过既有 service.skip/Controller 显式跳过 ASKED 且无 Answer 的 Q38，按真实结束条件 COMPLETE。
此前三次有效 Evaluation 保留；不是把 401/失败回答标成成功，也没有重置计时。

## 持久化核对

- 正式 Alembic revision 0024；0017–0024 八份 migration 原 hash 不变。
- Profile current=1，版本数量仍为 1，source_resume_id=38。
- MatchRun 3 输入 fingerprint：`2420242a681296b6cf553006ff9755c7593f93843476d670978224376f71db07`。
- Session 54 Snapshot：`2f93fa1c08d9a1efdb9c40c03bca651aec92c41163729db2a245db1a6419150c`。
- Session 55 Snapshot：`d29579750eb20b41af4213e6532d472af9b37030d1b76cce841541aa0cfce944`。
- Session 56 Snapshot：`017f2f31aeee824ea69b4b64ed781c7b24c3d2a510727acd1a9e836f709c0f40`。
- Answer/Evaluation 的 session/question/answer 关联一致；每场最多一个 current successful Report。
- 54 综合平均 2.5333，55 综合平均 3.5333；来自数据库有效 Evaluation，不取最后一题评分。
- 后续 Memory 决定、读取和报告生成没有使旧 Snapshot 漂移。
- 原 Session 51/52 未改写；旧失败 Report 1 未删除。

## 真实模型审计（部分关键调用）

以下为 provider 返回或程序计时，不自行估算缺失 usage/cost，不保存隐藏推理。

| 调用 | Prompt | latency ms | input | output | reasoning | repair |
| --- | --- | --- | --- | --- | --- | --- |
| MatchRun 3 / Matching | 1.0.0 | 126054 | 20814 | 10840 | 5775 | 0 |
| Report 2 / Coach | 1.0.1 | 70914 | 22979 | 4499 | 3311 | 0 |
| Session 55 / 首题 Main | 1.0.3 | 23693 | 10604 | 1294 | 1079 | 0 |
| Session 55 / Answer 5 Evaluation | 1.0.0 | 69806 | 10687 | 4086 | 3525 | 0 |
| Session 55 / Answer 6 Evaluation | 1.0.0 | 84690 | 10830 | 5295 | 4841 | 0 |
| Session 55 / Answer 7 Evaluation | 1.0.0 | 29063 | 10666 | 1880 | 1474 | 0 |
| Report 3 / Coach | 1.0.1 | 70643 | 22406 | 4502 | 3370 | 0 |
| Session 56 / 首题 Main | 1.0.3 | 14395 | 13075 | 867 | 674 | 0 |

这些真实调用使用 DeepSeek，没有 fallback。本轮不修改 resume_profile reasoning policy。

## 回归与命令

后端最终全量：**311 passed / 121.53s**，保留入场前 303 项，新增 8 个验收回归实例；覆盖 M1–M6。
测试在明确新建的空回归库执行，migration downgrade 等测试不接触正式库。
前端：**24/24**，包含纯展示/SSE 分块/HTTP 错误逻辑与页面契约检查。
浏览器主流程实际交互与持久化核对通过；不把静态页面断言说成浏览器测试。
生产构建、Python compile、migration hash、secret scan 和 whitespace check 均通过。

```powershell
$env:PYTHONPATH = 'apps/api/src'
apps/api/.venv/Scripts/python.exe -X utf8 scripts/run-regression.py --database interview_agent_f4_regression_20261006_autonomous_f
# 在 apps/web 中
node --experimental-strip-types --test tests/*.test.mjs
pnpm build
# 在仓库根目录
apps/api/.venv/Scripts/python.exe -m compileall -q apps/api/src apps/api/tests
apps/api/.venv/Scripts/python.exe scripts/check-whitespace.py
git diff --check
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/status-interview-platform.ps1
```

API/Worker/Web、PostgreSQL/Redis/MinIO/Milvus 均在运行；SearXNG 保持停止。
`.env`、`.runtime` 和 `output/playwright` ignored，扫描没有发现当前秘密值进入 diff/非 ignored 文件。
既有 Git ignore 权限、CRLF 提示不代表测试失败；没有为此改 migration 或全局关闭 whitespace 检查。

## 本轮修改文件边界

- `interview/agents.py`、`context.py`、`core_service.py`、`controller.py`：聚焦问题、固定上下文 projection、追问消费计数。
- `interview_report/prompts.py`、`resolver.py`、`agent.py`：版本化 Coach 指令与安全失败诊断，validator 标准不变。
- web `match-presentation.ts`、`JobMatchingPage.vue`、job/interview types：显示聚合、字面边界、硬资格及追问序号。
- web `InterviewReportPage.vue`、`MemoryPage.vue`、`zh-CN.ts`：明确审批确认、重复内容去除、中文恢复提示。
- web `api/client.ts`、`api/interview.ts`、`api/stream-http-error.ts`、`stores/auth.ts`、`InterviewChatPage.vue`：401 与回答保存状态。
- 验收 backend/frontend tests 与本文件；`.runtime` 中核对脚本和 screenshot 属 ignored 本地验收材料。

入场前 Profile/Research/importer/config 等修改不属于本轮新增修改，不重写、清理、提交或回滚。
本轮无 Schema/API 权限变更、无新增 Agent，无 commit/push。

## 已知限制与用户入口

1. M6 qualification policy 需要可靠的学历/专业/毕业年份结构化资格字段。
   当前教育 Fact 是带原文依据的分项经历，不等于 typed 已获最高学历/毕业资格。
   因而湖北“博士”仍为 UNKNOWN，并清楚显示待核实；不宣称 SATISFIED，不由 LLM 猜测。
   本轮不扩展冻结的 M3，也不把自由文本 Memory 当硬资格证据。
2. literal Evidence alignment 比语义推断更保守；华为当前真正可展示的匹配亮点是 Python/Java，
   不为凑足 2–4 条擅自把整个宽泛大模型要求算成支持。公开库仅四岗位，页面如实提示覆盖不足。
3. DeepSeek 带 reasoning 的 Evaluation/Coach 仍可能需要几十秒；遵守现有 timeout 和失败安全重试。
4. Memory/Observation 上下文预算会确定性截取，并标记省略/截断；Observation 永远不是稳定能力事实。
5. 登录 JWT 和真实会话时长会到期；跨小时暂停应重新登录/新建会话，不篡改预算。
6. 本轮真实模型未选择 DEEP_DIVE；该动作有回归覆盖，不虚构真实触发结果。

推荐从 `http://127.0.0.1:5173/profile` 查看自己的当前画像，
进入 `http://127.0.0.1:5173/jobs/matches/3` 的华为卡片；
可以直接在 `http://127.0.0.1:5173/interview?session_id=56` 回答第一题，未替用户作答。
若已跨越时长预算，使用岗位推荐的“开始模拟面试”正常新建，不复活旧会话。
完成后走面试报告、长期记忆建议，再新建面试检查已批准内容。

已完成报告入口：`http://127.0.0.1:5173/interview/reports?session_id=54`、
`http://127.0.0.1:5173/interview/reports?session_id=55`。
页面截图在 ignored `output/playwright/acceptance-report.png`。

达到本轮验收范围后停止开发；不继续优化、不进入新阶段。
