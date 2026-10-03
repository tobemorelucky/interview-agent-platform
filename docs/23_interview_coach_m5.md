# M5：Interview Summary / Coach、Final Report 与受控 Memory

M5 在已完成的 M4 Session 之后增量生成报告；不修改 M2 Research、M3 Profile 或
M4 Controller/Agent 的业务流程。没有 Job Matching、Research 扩展或自动画像更新。

## 1. FinalInterviewReport 与原子发布

`final_interview_reports` 保存 Session、immutable ContextSnapshot、generation/key、
evaluation fingerprint、deterministic statistics、coach analysis、producer/provider/model、
prompt version、生成状态和时间。`interview_report_attempts` 保留每次失败/成功尝试，
关联已有 ExperienceAgentRun/StepRun 的 latency、usage、repair 和错误；不保存隐藏推理。

报告生成和 Session COMPLETED 解耦。先短事务记录 RUNNING attempt，模型期间关闭
DB 会话；再短事务锁定 Session/report，重新校验 attempt version、评价集合和旧 current。
Report、Observation、Proposal 及支持关系全部写入后，最后取消旧 current 并设新 current，
同一事务提交。partial unique index 保证每场最多一个 current，CHECK 要求其已 COMPLETED。
中间失败全部 rollback，旧 current 不变，仅保留 FAILED generation/attempt；提交后的响应
读取失败也不允许撤销成功 generation。成功报告内容由数据库 trigger 禁止 UPDATE。

自动 key = SHA-256(session、Snapshot fingerprint、有效 evaluation set fingerprint、
prompt version/content hash)。自动重试复用同 generation；并发允许审计多个尝试，但
attempt version/CAS 只允许一个有效发布。主动 regenerate 要求稳定 `request_key`，产生
新的 generation，保留旧内容与审计。旧成功 key 重试不会重新激活历史报告。

## 2. Deterministic statistics

只查询真实 DB Question/Answer/Message/Evaluation，同场且 Answer/Question COMPLETED
才视为有效评价。不信任旧 `completion_json` 或 Coach 数字。统计包含 answered/evaluated/
skipped/follow_up/deep_dive/dynamic count、五维均值和 overall_average。均值四位小数；
无有效 Evaluation 时所有均值为 NULL，不调用 Coach、不创建 Observation/Proposal。
此时只发布明确 `NO_PERFORMANCE_ANALYSIS` 的确定性空分析报告。

报告响应显式分开 `statistics`（程序）与 `coach_analysis`（Agent synthesis）。不更新某题
评分，也不把最后一题分数用作全场评分。

## 3. SummaryCoachAgent

新增 route `interview_coach`，输入仅为固定 Snapshot、实际 Questions、User Answers、
有效 Evaluations 与确定性统计。复用现有 ModelProvider/ModelRouter/PromptRegistry/
Structured Output、最多一次 constrained repair、concurrency guard 和共享 Agent audit。
模型/endpoint/key/timeout/output/input/concurrency 可配置，没有新增依赖。

严格 `CoachArtifact` 输出 overall_summary、strengths、weaknesses、recurring_issues、
job_alignment、recommended_actions、practice_plan、memory_proposals。Schema 禁止
多余 score/count/Question ID 字段；resolver 还拒绝可检测的评分/次数文字和绝对技能判断。
输入预算计量包含 system、schema、payload，超额失败，不静默删除支持证据。

## 4. Evaluation resolver

Agent 只能引用提供的 `evaluation:<id>`。resolver 校验属于本场有效 DB 集合，拒绝未知/
跨场 ID，并确定性回填真实 Question、评价摘要与原五维评分。
`occurrence_count = COUNT(unique valid evaluation ids)`，重复同 ID 不增加次数。
recurring issue 必须至少两个不同有效 Evaluation，否则拒绝；单次仅能作为 weakness/
observation。Coach 无权生成 excerpt、Question ID 或统计。

## 5. PerformanceObservation

`interview_performance_observations` 是本场回答表现的 immutable 观察，不是长期候选人
事实。包含 owner/session/report/type/text/supporting_evaluation_ids/count/time，另有真实
Evaluation FK junction。数据库校验同场归属、有效支持与 recurring 最小数量。
不写 CandidateProfile/UserSkill/经历或长期 Memory。

## 6. MemoryProposal 与单事务用户确认

`memory_proposals` 默认为 PENDING，类型为 GOAL/PREFERENCE/EXPERIENCE/
SKILL_OBSERVATION/PREPARATION_FOCUS。保留原文、支持 Evaluation/Observation、
来源 Session/Report、内容 fingerprint、修正内容、本人确认与时间。confidence 是
SINGLE_EVALUATION / REPEATED_EVALUATIONS 支持覆盖信号，不是能力等级或真实性概率。

approve/correct 在一个短事务内锁 Proposal、校验 PENDING、创建已有 UserMemoryItem、
创建 USER UserMemoryEvent、更新 Proposal 并 commit。repository/domain 只 flush，不调用
内部自行 commit 的旧 Memory service。API AuditLog 也参与同一事务；任一失败全部回滚。
correct 保留原文并保存 corrected_content、changed_by=user、reason/time；修正与批准是一
个动作。reject 不写 Memory。相同决定幂等，冲突决定 409，并发批准只有一个 Memory。
SKILL_OBSERVATION 映射 EPISODIC，而非旧 SKILL / UserSkillProfile；现有浮点 confidence
仅为兼容字段，metadata 明确不是能力评分。原 Proposal 和已完成决定由 DB trigger 保护。

旧 `/memory/consolidate` 对明确 workflow_mode=m4 的新 Session 返回
`409 CONTROLLED_MEMORY_REQUIRED`，不能绕过审批。只有明确 legacy 保留旧兼容行为；
不按创建时间猜测。

## 7. 下一场 Memory 固定上下文

仅在创建 InterviewContextSnapshot 时读取，分别保存：

- `approved_memory_context`：有效、不超期、最新内容事件由本人确认的长期 Memory；
  Proposal 来源须真实 APPROVED/CORRECTED，READ/SYSTEM 事件不是本人确认。
- `recent_performance_observations`：本人当前成功 Report 的近期本场表现观察，历史
  generation 保留但不重复注入；明确 not stable facts / not user-confirmed。

两个字段独立排序/数量/预算，保存 ID、Memory version、fingerprint、实际截取内容、
支持来源、selected/omitted count 和预算信息。默认 Memory 8 条/4000 保守 token budget，
Observation 5 条/2000。按完整 UTF-8 JSON bytes 保守计量（不是精确 tokenizer）；小于
固定 envelope 开销的预算只保留空 envelope 并显式标记。Agent 每轮使用已有 Snapshot，
不读最新 Memory；用户中途批准/修正只会影响下一场。M4 仅增加创建时的只读装配，
不改状态机或 Agent。

## 8. API / UI

所有接口后端校验本人权限，跨用户 404。生成复用现有 interview_chat_limit，审批复用
memory_write_limit，没有新队列或网络基础设施。

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/api/v1/interview-reports` | 本人报告历史，limit/offset |
| GET | `/api/v1/interview-reports/sessions/{id}` | 当前报告、全部 generations、DB 统计、逐题评价 |
| POST | `/api/v1/interview-reports/sessions/{id}/generate` | `{regenerate:false}`；显式 true 另需 request_key |
| GET | `/api/v1/interview-reports/{id}` | 历史 generation / 支持评价 / proposals |
| GET | `/api/v1/memory-proposals` | 本人提案，可按 report_id 筛选 |
| POST | `/api/v1/memory-proposals/{id}/approve` | `{reason?:string}`，本人批准 |
| POST | `/api/v1/memory-proposals/{id}/reject` | `{reason?:string}`，不写 Memory |
| POST | `/api/v1/memory-proposals/{id}/correct` | `{content,reason}`，修正并批准 |

Web `/interview/reports?session_id=N` 显示独立 Statistics/Coach Analysis、岗位公司、
支持引用、逐题评价、练习计划和三种审批；`/interview/reports/:id` 查历史。
M4 完成卡只增加报告链接。旧 Memory 页面显示 proposal provenance，与动态技能估计区分。
页面明确 Replay / fixture / demo。失败报告状态和统计仍可读取，安全重试不改变面试完成状态。

## 9. Migration / 删除保护

仅新增 `0023_add_interview_reports_and_memory_proposals.py`，没有修改 0017–0022，
不猜测补历史报告。新增 7 张表、唯一/partial unique/CHECK、provenance/immutable triggers。
任何 Report generation（包括 FAILED）存在时，Session 删除 service 返回
`409 INTERVIEW_REPORT_REFERENCED`，数据库 FK RESTRICT 再保护并发和绕过 service。
正式业务库未代用户升级；部署前备份，API 目录执行 `uv run --frozen alembic upgrade head`。
downgrade 删除 M5 数据，迁移测试只对全新空隔离库运行。

## 10. 测试与复现

实际验收（2026-10-03）：

- 全新空 PostgreSQL `interview_agent_m5_final_20261003`：M1–M5 **149 passed in 81.58s**，
  无 skip。包含 0018–0023 upgrade/downgrade、M1–M4 回归和 M5 35 项新增用例。
- Coach：未知 Evaluation/重复支持/单次 recurring/评分字段/数字 prose/绝对技能拒绝，
  repair 一次成功与 hard fail、timeout、输入预算失败、零有效评价、失败重试审计、同 key
  并发 CAS、新 generation 竞态、成功/失败 regenerate 及发布事务 fault injection。
- 审批：item 与 USER event 已实际 flush 后，最终 Proposal flush 注入失败，整体回滚；
  暂定 Proposal 确认字段已 flush 后 Memory 写失败也整体回滚。DB CHECK 实际拒绝
  APPROVED 无 Memory。重复/并发 approve 只有一条 Memory 和确认事件，跨用户拒绝。
- Session 删除 API 实际 409；绕过 service DELETE Session/Evaluation/旧 Report 被 FK 拒绝。
  新 Session 旧 consolidate 409；无 Profile/UserSkill/UserSkillProfile 自动更新。
- 独立 `smoke_interview_coach.py`：User **71**、第一场 Session **60**、Report **30**
  COMPLETED；6 Answer/Evaluation，follow_up=1、deep_dive=1、dynamic=1、skipped=0，
  五维及 overall=**3.1667**，末题 1，不冒充全场均分。Coach 一次，自动同 key 复用。
  三提案 APPROVED/CORRECTED/REJECTED，仅 Memory **14/15** 两条；Observation **55/56/57**。
  第二场 Session **61** 完整运行至 COMPLETED，所有题完成。批准/修正文进入 Memory 字段，
  rejected 不进入；Observation 独立字段。随后本人修改/新增 Memory 后，各轮实际 Main/
  Evaluation 输入仍与固定 Snapshot 相同，没有 READ/自动长期写入事件。
  第二场 fingerprint：`9bae4c694bb0d23ea52c678c9cc5d5d44456f5c61c95afb50f3543e65739b961`。
  httpx sentinel 阻断所有真实 HTTP，mode 为 **replay / fixture / demo**，不是 Live 验收。
- Web：Node 内置测试 **5 passed**（Report helpers + 原 SSE），`pnpm build` 成功。
  Playwright skill 真实浏览器连接隔离 Demo API/PostgreSQL：Session **39**，Report **68**
  初次因 Replay fixture 数字表述被 validator 拒绝，改 fixture 而未放宽 validator，
  同 key 安全重试，DB 保留 FAILED/SUCCEEDED 两 attempt。approve/correct/reject 三操作
  和刷新恢复通过，Career Memory **24/25**、原文/修正文/用户原因可见。显式 regenerate
  Report **90** 成为 current，旧 **68** 不变仍可查。截图检查修复长标题换行；M5 Memory
  显示有限支持信号，不显示兼容浮点的“真实性百分比”。Report/Memory 页面 console
  无运行时错误；登录开发服务 favicon.ico 两次 404 是已知非业务资源问题。
  截图在 gitignored `output/playwright/m5-report-historical-generation.png`、
  `m5-report-approved-corrected-rejected.png`、`m5-approved-career-memory.png`。
- `git diff --check` 通过；没有新增依赖/lockfile 修改。本次临时 API/Vite/browser 验收后关闭，
  隔离测试数据库保留供只读复核，未升级正式业务库。

```powershell
# 先创建全新空隔离库；迁移测试不能对已有业务数据运行
$env:M1_TEST_DATABASE_URL='postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_m5_final_20261003'
$env:DATABASE_URL=$env:M1_TEST_DATABASE_URL
uv run --frozen pytest -q -p no:cacheprovider --tb=short
uv run --frozen python scripts/smoke_interview_coach.py
# 浏览器 fixture Demo，仅用于名字含 m5_ 的隔离库
uv run --frozen python scripts/m5_demo_runtime.py seed
uv run --frozen python scripts/m5_demo_runtime.py api
# web 目录
node --test tests/interview-sse.test.mjs tests/interview-report.test.mjs
pnpm build
```

## 11. 修改归属

进入 M5 前已存在 M1–M4、面经审核、0017–0022、锁文件、依赖、worker、文档及 UI 的
未提交修改；全部保留，不 reset/checkout/clean，也不修改只读 JobPilot。

M5 新增：`modules/interview_report/`、`memory/proposal_*` 与 `memory/snapshot.py`、0023、
`test_06_m5_migration.py`、`test_m5_report.py`、`test_m5_memory.py`、
`test_m5_two_session_e2e.py`、`smoke_interview_coach.py`、`m5_demo_runtime.py`、
web report api/types/page/test 和本文。增量修改 config/shared ModelRouter、main/model registry、
Interview context 的只读装配、router 的删除保护、旧 Memory consolidate 防绕过/policy、
Web router/UserLayout/完成卡链接/Memory provenance、.env.example、README、docs/05/08/12/19。
不新增依赖，不修改 uv.lock/pnpm-lock 或 M2/M3/worker 业务代码。

## 12. 已知限制与停止点

本阶段验收是 Replay/fixture，不证明真实模型输出质量、模型服务可用性或 Live Search
稳定性。M2 CAPTCHA/rate-limit 是保留的已知限制，不再投入搜索扩展。
数字/绝对技能文字的 deterministic pattern guard 不是完整自然语言事实判定器，仍需用户
审阅 Coach synthesis；它不影响 DB 数字真相源。预算保守 UTF-8 计量，可能提前截取/失败。
报告生成目前请求内执行，前端该请求 timeout 150 秒；超时后查看生成历史并同 key 安全重试。
无可靠 Snapshot 的 legacy Session 不生成报告。没有有效评价不生成表现推论或提案。
现有正式业务库尚未升级 0023；部署前备份、正常 Alembic upgrade 是必要下一步，不是扩展 M6。
完成 M5 后停止，不进入 M6/Job Matching。
