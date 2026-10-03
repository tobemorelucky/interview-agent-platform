# M4：Multi-Agent Mock Interview Core

M4 在原 Interview Session / Question / Message / SSE 上增量实现两类 Agent 和
deterministic SessionController。M2 Research、M3 Profile 保持冻结。没有新增
Job Matching、Coach、FinalInterviewReport、Career Memory Proposal 或自动画像更新。

## 1. 上下文与数据模型

```text
CandidateProfileVersion + Company / Job / JobDescriptionVersion
  + JobRequirement + approved InterviewQuestion / InterviewExperience
                 -> InterviewContextSnapshot (immutable)
                 -> InterviewSession (workflow_mode=m4)
                    -> InterviewSessionQuestion -> InterviewAnswer
                                                -> InterviewEvaluation
                    -> InterviewAgentCall -> ExperienceAgentRun / StepRun
```

创建 Session 的短事务内固定 Profile/JD version、公共实体 refs、所用题目/面经的
最小内容和 fingerprint、候选人规范化事实与真实 Evidence、题量和执行预算。
Snapshot 不复制整份 raw Resume；创建后各轮只读取 Snapshot，不读最新画像/JD。
Profile/JD 更新或管理员修改公共题目不改变已有 Session；数据库 trigger 禁止
UPDATE Snapshot、替换 Session 的 Snapshot 或绑定别人的 active question。

已选公共题目、面经和 Requirement 的 DELETE 受 0022 trigger 保护，即使尚未出题；
创建 Snapshot 时使用短期 KEY SHARE 防止同时删除。公共内容仍可修改。版本实体
采用 RESTRICT FK。删除本人 Session 可级联清除该场记录，保留通用 Agent 审计。

必须选有效 Company + Job，有本人 ProfileVersion、该 Job 的 PUBLISHED JD 和
Requirement。题库只选 APPROVED/AUTO_APPROVED 且 should_index 的内容，并验证
Company/Job 一致；没有公共题库不触发 Research，记录 coverage_insufficient。
当前快照上限为 100 道公共题和 30 条面经。创建失败不留下半成品 Session。

## 2. 两个 Agent 与共享模型设施

- InterviewMainAgent / route `interview_main`：返回下一题、来源和 action recommendation，
  不评分、不写 Session/Question。
- AnswerEvaluationAgent / route `answer_evaluation`：严格返回五个 1–5 整数量表、
  covered/missing points、issues、follow-up focus 和 action recommendation。
  量表只描述本轮回答，不声明长期能力。

复用已有 ModelProvider、ModelRouter、PromptRegistry、ResearchModelCalls 的
Structured Output、最多一次 constrained repair、provider concurrency guard 和
ExperienceAgentRun/StepRun。新增 InterviewAgentCall 关联 session/answer/run。
provider/model/prompt version/status/latency/token usage/repair/error 都在共享审计中，
不保存隐藏推理。未知 public ID / candidate Evidence ID 或非法来源组合明确失败。
模型、路由和预算在配置中；没有新依赖或新模型基础设施。

## 3. Controller 与并发、幂等

```text
Session: CREATED -> READY -> IN_PROGRESS -> COMPLETED
Question: PENDING -> ASKED -> ANSWERED -> EVALUATED -> COMPLETED
                       \-----------------------------> SKIPPED
Answer: RUNNING -> COMPLETED
             \-> FAILED -> RUNNING (同 key 重试)
```

SessionController 是 M4 的唯一业务状态写入口。active_question_id + Question status +
sequence 确定当前题；current_question_index 仅由 Controller 派生同步。
FOLLOW_UP 是当前主题缺失点补问；为独立回答/评分建立 child question，但不计为
dynamic。DEEP_DIVE 是更深入的新 child question，计为 dynamic。两者共同受根题
max_follow_ups、总题量、轮次、时间与模型预算限制。NEXT/ADJUST_DIFFICULTY/SKIP/
COMPLETE 均由 Controller 验证；不能仅因模型建议 COMPLETE 提前结束。

Answer 使用 UNIQUE(session_id, idempotency_key)、每个实际 Question 一个 Answer，
Evaluation.answer_id UNIQUE；同 key 必须对应同题同正文。成功重试返回持久化事件，
不再调用模型。失败保留 Answer 和失败审计，Question 留在 ANSWERED，不伪造评分或
NEXT。GET Session 返回 pending_answer 的原 key/正文，刷新后仍能安全重试。

模型前读取 Snapshot/state_version 后关闭 DB 会话；模型不持行锁或长事务。
提交时短事务锁 Session、再次比较 state_version/active question/Answer 状态，陈旧
结果返回 INTERVIEW_CONFLICT，不覆盖另一个请求的进展。并发重试允许多次被审计
的模型调用，但最多提交一个有效 Evaluation。

模型调用前先在 Controller 短事务预留预算。Live 采用 UTF-8 输入长度、schema、
输出额度和固定开销的保守上界，并为一次 repair 预留两次调用；预留不退回，失败
重试也受限制。实际 provider token usage 另行完整审计。此策略不是精确 tokenizer，
可能保守提前结束；Replay 明确预留 0。调用 timeout 不超过本场剩余时间。
预算不足时可确定性结束，未评分回答不产生 Evaluation 或默认分；结束原因记录为
MODEL_OR_TIME_BUDGET。时间约束在请求时检查，未新增后台计时器。

## 4. 来源与统计

常规题优先 unused 公共题 -> Requirement -> 候选人事实（项目优先）-> dynamic。
公共题使用快照中原文、ID 和 fingerprint；岗位题必须引用快照 Requirement；
候选人题必须引用真实 fact + 对应 Resume/UserConfirmed Evidence；Agent 不生成
Evidence excerpt。FOLLOW_UP/DEEP_DIVE 保存 parent refs 并继承相关候选人证据。

COMPLETE 查询数据库有效 InterviewEvaluation 计算 answered_count/evaluated_count、
skipped_count、follow_up_count、dynamic_question_count、五维均值及 overall_average。
兼容 avg_score = overall_average。无评分时均值为空，不使用最后一题分数或 LLM。
所有残留 ASKED/PENDING 在结束时转 SKIPPED，active_question_id 清空。
M4 热路径不调用旧 Memory 自动捕获/写入，也不更新 Profile/UserSkill；旧显式
Memory API 保留。旧 UserSkillProfile 仍是独立的面试动态能力估计表，本阶段不更新。

## 5. API / SSE / UI

沿用 `/api/v1/interview`、本人权限和 `/messages/stream` SSE。

- GET `/targets`：已存的有效 Company/Job + PUBLISHED JD，不联网。
- POST `/sessions`：company_id、job_id、可选 Profile/JD version、question_count 1–40。
- POST `/sessions/{id}/start`：READY -> IN_PROGRESS；首次题目生成失败可重试。
- POST `/sessions/{id}/messages/stream`：content、question_id、idempotency_key。
- GET `/sessions/{id}`：Snapshot、当前题、已存消息、completion_json、pending_answer。
- 原 questions / current / detail / skip 和本人 Session 删除接口保留。

结果事件 evaluation / follow_up / question / complete / done 只在事务提交成功后发送；
status 可在调用前发送，失败发送明确 error。前端 SSEParser 的 buffer/event/data
跨 ReadableStream.read 保留，支持 UTF-8 分片、CRLF、多行 data；无 done 或截断时
明确失败并保留同 key 重试。没有 WebSocket 或自动连接重试基础设施。

InterviewChatPage 最小更新为 Company/Job 选择、Snapshot version/coverage、来源证据、
五维评价和确定性完成统计；恢复历史消息时用户回答在对应评价前。Replay 明示。
旧 resume-bind/target-position/questions-generate 不允许改本场 Snapshot，返回 409。
legacy Session 保持可读，禁止继续写入；不猜测历史 Profile/Job 或生成假 Snapshot。

## 6. Migration 与部署

只新增 `0022_add_mock_interview_core.py`；0017–0021 未修改。
新增 snapshot/answer/evaluation/agent-call 表、Session 和 Question 可空兼容字段、
唯一约束、五维 CHECK、索引及上下文保护 triggers。旧 Session 默认 legacy。
正式数据库尚未代用户升级；部署前备份并在 API 目录执行：

```powershell
uv run --frozen alembic upgrade head
```

upgrade -> downgrade 0021 -> upgrade 已在空隔离数据库验证。downgrade 会删除 M4
记录，勿对有业务数据的库重跑迁移测试。

## 7. 实际验收（2026-10-02）

- 空隔离 PostgreSQL：interview_agent_m4_release_20261002，0017–0022 migration 测试
  和 M1/M2A/M2B/M3/M4 pytest 共 **114 passed in 55.81s**。
- Web：Node 内置 SSE parser 测试 **3 passed**，pnpm build 成功。
- 最新独立 smoke：Session **31** / User **44**，mode `replay / fixture / demo`；
  httpx 外网请求被 sentinel 拒绝。真实数据库落地完整六轮：公共题 -> FOLLOW_UP ->
  Requirement -> DEEP_DIVE -> 调整难度的项目题 -> 技能题 -> COMPLETE。
  6 Answer / 6 Evaluation，follow_up=1、dynamic=1、skipped=0，五维及 overall=3.1667；
  最后一题为 1，验证没有用最后评分冒充全场平均。全部题 COMPLETED，无 PENDING/
  ASKED 泄漏，同回答重试不增加调用。Main 6 次、Evaluation 6 次。
  Snapshot fingerprint：4b98fc62a7da3cb6257c943f857a1758d0e17de9b53d54b53cf55a9d420d9cad。
- 测试期间真实修正 Profile、创建 JD v2、修改公共题；已有 Snapshot fingerprint 和
  下一题上下文不变。失败/repair hard fail/未知 ID/跨用户/并发陈旧结果/预算/选中公共
  refs 删除/事务回滚均验证；无自动 Memory/Profile/UserSkill 写入。
- Playwright 真实浏览器对接 Replay API/PostgreSQL：Session **21** 六轮完成，刷新
  恢复 COMPLETED 与 DB 统计，输入禁用，Profile/JD version 和 Replay 标识保留。
  截图 output/playwright/m4-interview-complete-restored.png（gitignored）。页面没有
  M4 运行时错误；开发服务 favicon.ico 有两次 404。
- 旧 Experience Agent graph / review / Memory context smoke 仍通过。

复现：先创建名字包含 m4_ 的独立数据库并升级，API 目录运行：

```powershell
$env:DATABASE_URL='postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_m4_release_20261002'
uv run --frozen python scripts/smoke_mock_interview.py
# 全量测试仅对新建空测试数据库运行，M1_TEST_DATABASE_URL 和 DATABASE_URL 指向同一个库
uv run --frozen pytest -q -p no:cacheprovider --tb=short
# 浏览器 Demo：仅隔离数据库，固定响应明确为 Replay，不用于正常服务
uv run --frozen python scripts/m4_demo_runtime.py seed
uv run --frozen python scripts/m4_demo_runtime.py api
# web 目录
node --test tests/interview-sse.test.mjs
pnpm build
```

## 8. 修改归属与停止点

进入 M4 前，M1–M3、面经审核、0017–0021、模型基础设施、测试、锁文件已有未提交
修改，全部保留。未 reset/checkout/clean，未改 JobPilot、旧 migration、依赖/lock。

M4 新增 interview/{artifacts,agents,context,controller,core_service}.py、0022、
test_05_m4_migration.py / test_m4_interview.py、smoke_mock_interview.py / m4_demo_runtime.py、
web/api/sse.ts / web/tests/interview-sse.test.mjs、本文档。增量编辑 interview 的 models/
schemas/router/service、config/shared ModelRouter、web/api/interview.ts/types/interview.ts/
InterviewChatPage.vue、.env.example、README、docs/05/08/12。旧 InterviewChatService
mutation（包括私有写方法和后台生成入口）明确停用，避免 index 推进旁路；显式
Memory 接口保留。

真实模型没有进行 Live 验收；Replay 不证明真实模型的准确性或可用性。Live 预算是
保守计数而非精确 tokenizer；legacy 只读；未加 Session 后台超时关闭；题库 selection
有明确上限。本阶段没有 Final Report/Coach/Job Matching/Memory Proposal。
M2 Live Search 限制继续保留，不扩搜索。完成 M4 后停止，不进入 M5。
