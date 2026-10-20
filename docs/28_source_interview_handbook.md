# Multi-Agent 求职与模拟面试平台——源码级面试手册

> 本文整理自冻结版本的只读源码审查与面试准备对话，不是新功能设计或宣传稿。
> 源码结论以审查时版本为准；测试与真实模型数字来自历史验收记录，本次整理没有重新运行验收。

## 审查基线

- Commit：`5d84ba697c93f7341727b9f7d5916f9105a395fc`
- Tag：`v0.9.0-finalization`
- 原只读审查前后 worktree clean。
- 本次只新增本文，不修改代码、配置、migration，不创建 commit。
- 后端最终验收：215 passed；前端：11/11；production build、Python compile、PowerShell parse 与本地浏览器 Demo 通过。
- Migration head：`0024`。
- 当前 Primary：`deepseek-v4-pro-ga-260813`；Fallback：`doubao-seed-2-1-turbo-260628`。
- F2 验证的是 **LIVE model + LOCAL_FIXTURE / REPLAY data**，不是全部 Live Search E2E。

相关历史记录：[F2 真实模型验收](25_real_model_acceptance.md)、[F3 产品体验收尾](26_product_ux_finalization.md)、[F4 工程交付](27_engineering_delivery.md)。

本文源码链接相对于 `docs/`，方便在仓库中阅读。

## 1. 项目定位、主流程与技术栈

最终定位：**面向秋招的多 Agent 求职与模拟面试系统**。

普通用户主流程：

```text
上传简历 → Parser / ResumeReport → ResumeEvidence
    → ResumeProfileAgent → CandidateProfileVersion
    → deterministic retrieval / eligibility / Evidence Matrix
    → JobMatchingAgent → Recommendation
    → 选择岗位 → 创建 InterviewContextSnapshot
    → InterviewMainAgent → Question → 用户 Answer
    → AnswerEvaluationAgent → SessionController 校验并提交
    → FOLLOW_UP / DEEP_DIVE / NEXT / COMPLETE
    → deterministic statistics → SummaryCoachAgent
    → FinalInterviewReport / PerformanceObservation / PENDING MemoryProposal
    → 用户 approve / correct / reject → 既有 Career Memory
```

公共数据后台链路：

```text
PublicDataRefreshTask → ToolGateway → Search / Fetch Provider
    → Source / SourceSnapshot → PublicDataResearchAgent
    → Claim / ClaimEvidence → Company / Job / JD / Requirement / 面经 / 题库
    → 质量门控与审核
```

普通用户不触发实时爬虫。数据不足时不由 Matching 或 Interview 偷偷联网补库。

六个正式角色按职责与权限划分：公共抽取、候选人事实、岗位匹配、出题、评价、复盘。六角色不意味着仓库只有六个 Agent 类或六次模型调用：旧面经 pipeline、ResumeReport 与知识问答仍有内部模型调用。

`SessionController`、Evidence resolver、matching policy、statistics、Snapshot builder、ReportService、MemoryProposalService 都是确定性组件，不是 Agent。

| 层 | 实际技术 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Vite、Pinia、Vue Router、Axios、pnpm |
| API | FastAPI、Pydantic v2 |
| 数据库 | PostgreSQL、SQLAlchemy 2.x AsyncSession、asyncpg、Alembic |
| 异步任务 | Celery、Redis |
| 文件存储 | MinIO / S3-compatible storage |
| 简历解析 | pypdf、python-docx、TXT |
| 网络正文 | HTTPX、trafilatura、BeautifulSoup/lxml |
| 模型 | OpenAI-compatible Provider、项目自有 Router / Prompt / Structured Output |
| 检索 | Milvus，主要服务既有知识和已发布内容检索 |
| 工程 | uv、Docker Compose、pytest、Replay E2E、Playwright 浏览器验收 |

新 M4 主要消费 PostgreSQL 中已固定的 Snapshot，不是每轮都调用 Milvus 做 RAG。

入口：[main.py](../apps/api/src/interview_api/main.py)。

## 2. 六个 Agent 源码拆解

### 2.1 PublicDataResearchAgent

- 入口：[research/agent.py](../apps/api/src/interview_api/modules/public_career/research/agent.py)，`PublicDataResearchAgent.run()`。
- Route：`public_research_extract`、`public_research_quality`。
- Prompt：JOB_JD 使用 `job_jd@1.0.0`、`quality@1.0.0`；面经分支复用 extraction/routing/reliability。
- 输入：成功 Fetch Snapshot 构造的 Evidence catalog。
- 输出：`JobJDExtraction`、`ResearchQuality`；INTERVIEW_DATA 使用旧 pipeline 兼容结构。
- Validator：真实 Evidence ID、身份支持、非空 requirement；模型不能输出 excerpt、URL、Snapshot ID 或 source count。
- Resolver：Evidence ID 精确回填 Snapshot、excerpt、字符位置。
- 持久化：Company、Job、JDVersion、Requirement、Claim、ClaimEvidence；面经分支写 Experience/Question。
- 失败：发布事务 rollback，Task/Audit FAILED。
- Repair：结构错误最多一次；业务 Evidence 失败不靠换模型或放宽校验通过。
- 无权：写用户画像、匹配、推进面试、编造来源和去重统计。

调用：`run → _snapshots → EvidenceCatalog → ResearchModelCalls → validation → gate → _publish_job / _publish_interview`。

**注意：Research gate 的 JD `APPROVED` 不等于 M4/M6 读取的 `PUBLISHED`，不能宣称质量批准后已自动进入用户可用岗位库。**

### 2.2 ResumeProfileAgent

- 入口：[candidate_profile/agent.py](../apps/api/src/interview_api/modules/candidate_profile/agent.py)，`ResumeProfileAgent.run()`。
- Route / Prompt：`resume_profile` / `resume_profile@1.0.1`，保留旧 1.0.0 注册。
- 输入：ResumeReport、ResumeEvidence、已有画像。
- 输出：`CandidateProfileDelta`：摘要、技能、项目、教育、工作/实习、strengths、unknowns。
- Validator：引用真实；label/statement 必须是引用 excerpt 中规范化后的连续文本；unsupported strength 拒绝。
- Resolver：`normalize_skill_evidence_states()` 计算 maximum supported state，只向下收敛；`resolve_and_validate()` 最后校验。
- 持久化：Version、CandidateFact、规范化经历/技能、Evidence link、Generation。
- 失败：Profile Generation/Audit FAILED，不破坏已完成 ResumeReport。
- Repair：JSON/Schema 错误最多一次；Evidence/strength 业务错误不自动 fallback。
- 无权：制造证据、认证精通、覆盖用户确认事实、更新长期面试能力评分。

调用：`run → generation key → ensure_resume_evidence → model → state normalization → validation → CandidateProfileService.persist`。

### 2.3 JobMatchingAgent

- 入口：[job_matching/agent.py](../apps/api/src/interview_api/modules/job_matching/agent.py)，`JobMatchingAgent.call()`。
- Route / Prompt：`job_matching` / `job_matching@1.0.0`。
- 输入：冻结 Profile、批准偏好、候选岗位、资格、Evidence Matrix、等级上限。
- 输出：`MatchArtifact`：候选排序、推荐等级、支持关联、缺口和准备方向。
- Validator / Resolver：候选完整排列、真实 ID、精确矩阵关联、等级 ceiling、未知条件保持未知。
- Explanation 必须精确复制程序提供的 support explanation，不是自由生成新技能结论。
- 持久化：由 MatchingService 写三层 rank、Recommendation、Attempt、Run 状态。
- 失败：FAILED history，不发布部分推荐。
- Repair：结构错误最多一次；资格或关联失败不换模型刷过。
- 无权：翻转资格、增加岗位、猜学历、生成匹配百分比、联网补库。

调用：`Service.create → retrieval/policy → frozen Run → Service.generate → Agent → resolver → final policy → persistence`。

### 2.4 InterviewMainAgent

- 入口：[interview/agents.py](../apps/api/src/interview_api/modules/interview/agents.py)，`InterviewMainAgent.call()`。
- Route / Prompt：`interview_main` / `interview_main@1.0.0`。
- 输入：Snapshot、历史题、当前题、回答/评价、Controller 请求动作和来源策略。
- 输出：`MainArtifact`、`QuestionArtifact`：题目、expected points、dimension、difficulty、来源引用。
- Validator：动作与来源必须符合请求；引用存在于 Snapshot；公共题文本须与固定文本一致。
- 持久化：Agent 只写审计；Question/Session 由 Controller 写。
- 失败：不发布问题、不推进业务。
- Repair：严格结构错误最多一次。
- 无权：评分、插下一题、改 active question、直接 COMPLETE。

### 2.5 AnswerEvaluationAgent

- 入口：同 [interview/agents.py](../apps/api/src/interview_api/modules/interview/agents.py)，`AnswerEvaluationAgent.call()`。
- Route / Prompt：`answer_evaluation` / `answer_evaluation@1.0.0`。
- 输入：固定上下文、当前题、rubric、真实 Answer。
- 输出：`EvaluationArtifact`，五维严格整数 1–5，covered/missing points、issues、follow_up_focus、动作建议。
- Validator：Strict Pydantic，DB 评分范围 check；这不证明评分客观准确。
- Resolver / 持久化：Controller 绑定 Answer/Question，提交唯一有效 Evaluation 与合法状态转换。
- 失败：记录失败，不 EVALUATED、不 NEXT、不补 score=3。
- Repair：结构错误最多一次。
- 无权：写长期能力、Memory、Profile 或直接推进 Session。

### 2.6 SummaryCoachAgent

- 入口：[interview_report/agent.py](../apps/api/src/interview_api/modules/interview_report/agent.py)，`SummaryCoachAgent.call()`。
- Route / Prompt：`interview_coach` / `interview_coach@1.0.0`。
- 输入：Snapshot、Questions、Answers、有效 Evaluations、deterministic statistics。
- 输出：`CoachArtifact`：分析、建议、计划、Memory proposals；没有可计算评分/数量字段。
- Validator / Resolver：真实本场 Evaluation refs；recurring 至少两个不同有效 Evaluation；程序回填题目/评价摘要与 occurrence。
- 持久化：ReportService 原子发布 Report、Observation、PENDING Proposal。
- 失败：FAILED Attempt，旧 current 保留，Session 仍 COMPLETED。
- Repair：结构错误最多一次；业务引用失败不刷模型。
- 无权：重新评分、覆盖 statistics、写 Profile、自动批准 Memory。

## 3. 完整 API 与数据调用链

| 步骤 | Endpoint | 服务与数据流 | 事务边界 |
| --- | --- | --- | --- |
| 上传 | `POST /api/v1/resumes/upload` | ResumeService → MinIO → Resume → Celery dispatch | 先保存 Resume，再投递；不是跨系统总事务 |
| 解析 | Worker task | MinIO download → ResumeParser → raw_text → 旧模型 ResumeReport | Report + Resume COMPLETED 提交后再做 Profile |
| 画像 | `POST /api/v1/candidate-profile/generate` | Evidence → ProfileAgent → resolver → ProfileService | 完整 N+1 后最后 current 切换 |
| 读取 | `GET /api/v1/candidate-profile`、`/versions` | 所属用户版本、规范化记录和依据 | 只读 |
| 纠正 | `POST /api/v1/candidate-profile/corrections` | 复制完整 snapshot → 用户证据 → N+1 | old/new/reason 与新版本同事务 |
| 匹配创建 | `POST /api/v1/job-matches` | Profile/Memory/Jobs → retrieval/policy → frozen Run | REPEATABLE READ 固定输入 |
| 匹配生成 | `POST /api/v1/job-matches/{id}/generate` | Agent → resolver → rank/Recommendation | 模型外调用，短事务发布 |
| 面试创建 | `POST /api/v1/interview/sessions` | 当前岗位规则 → build_context → Snapshot | Session + Snapshot 一次提交 |
| 首题 | `POST /api/v1/interview/sessions/{id}/start` | MainAgent → Controller.start | 重验 version 后短事务 |
| 回答 | `POST /api/v1/interview/sessions/{id}/messages/stream` | prepare Answer → Evaluation → Main → commit_answer | prepare 与正式发布分阶段；结果 commit 后 SSE |
| 报告 | `POST /api/v1/interview-reports/sessions/{id}/generate` | DB statistics → Coach → resolver → Report/Observation/Proposal | 完整成功后最后 current 切换 |
| 审批 | `POST /api/v1/memory-proposals/{id}/approve`、`/correct`、`/reject` | ProposalService → Item/Event/Decision | 外层统一一次 commit |

上传不是 transactional outbox：MinIO、PostgreSQL、Redis/Celery 之间不能声称原子成功。Profile 失败不影响已成功 ResumeReport。

历史推荐开始面试只传 Company/Job ID，M4 重新读取当前合法材料。一般 M4 创建 API 也允许显式合法 Profile/JD version，但推荐跳转不复制旧 MatchRun snapshot。

## 4. LLM 与 deterministic 边界

| 领域 | LLM | 程序/人 |
| --- | --- | --- |
| Public Research | 抽取、提出适用性/风险 | 正文证据、ID/身份、去重、来源门控、审核 |
| Profile | 选择材料事实、提出技能状态 | 稳定 Evidence、连续摘录校验、maximum state、向下收敛、版本 |
| Matching | 候选内 rerank、选择已有支持关联 | SQL、eligibility、matrix、等级 ceiling、pre/final rank |
| Main | 问题与追问内容 | 来源、active ID、状态、顺序、预算 |
| Evaluation | 本轮评分与动作建议 | 分数合法性、Answer 绑定、唯一有效评价、状态转换 |
| Completion | 不负责 | Controller 判断、清理未完成题、统计 |
| Coach | 定性复盘与 Proposal | Evaluation refs、occurrence、recurring 门槛、statistics |
| Memory | 提议 | 用户决定、事务、provenance、长期写入 |

技能最终状态：`final_state = min(proposed_state, maximum_supported_state)`。不向上提升。

状态、资格、统计、幂等与用户授权需要可重放的规则，不应该由模型自由裁量。

## 5. Interview Core 深挖

核心：[controller.py](../apps/api/src/interview_api/modules/interview/controller.py)、[core_service.py](../apps/api/src/interview_api/modules/interview/core_service.py)、[context.py](../apps/api/src/interview_api/modules/interview/context.py)、[models.py](../apps/api/src/interview_api/modules/interview/models.py)。

- Session：状态、active question、state_version、预算。
- Snapshot：本场唯一上下文版本边界。
- Question：实际文本、顺序、来源、parent、kind、status。
- Message：对话内容；Answer：可幂等与重试的业务提交身份。
- Evaluation：一个 Answer 的有效五维评价。
- AgentCall：模型调用与 Session/Answer/Audit 关联。

`active_question_id` 不受动态插题数组下标漂移影响。旧 `current_question_index` 仅兼容，由 Controller 派生同步。

并发流程：

```text
读取 Snapshot + version=N，关闭事务
    → 模型调用
    → 短事务 FOR UPDATE
    → 重验 version、active question、Answer status
    → 提交并推进版本；陈旧结果 conflict
```

这是手工 expected-version 校验结合短行锁，不是单纯 SQLAlchemy 自动 version mapper。

正常 Question 状态：`PENDING → ASKED → ANSWERED → EVALUATED → COMPLETED`。部分中间状态在同一事务完成。失败 Answer 留待重试，题目不成功 EVALUATED、不自动 NEXT。

FOLLOW_UP 补当前回答缺失点；DEEP_DIVE 更深入主题。**二者都保存独立 SessionQuestion/Answer/Evaluation，并有 parent**。FOLLOW_UP 来源是 FOLLOW_UP；DEEP_DIVE 通常是 DYNAMIC_GENERATED。都受根题次数、总题数、轮次、时间与 reservation 预算限制，题目预算不是“主问题之外无限追问”。

COMPLETE 由 Controller 决定。模型建议 COMPLETE 不代表授权；完成时清理 PENDING/ASKED、清空 active ID、保存 deterministic summary，不生成 Final Report。

Evaluation 成功但后续 Main 失败时，正式业务评价可能尚未发布；保留审计，用同一 Answer 重试。这避免半推进，但可能重复模型调用。

预算耗尽可保存回答后结束而不产生 Evaluation；这不是 score fallback。

SSE 顺序：`模型 → Controller → commit → evaluation/follow_up/question/complete`。提前发的 status 仅是处理提示。

前端 [sse.ts](../apps/web/src/api/sse.ts) 与 [api/interview.ts](../apps/web/src/api/interview.ts)：TextDecoder/SSEParser 在 read loop 外，buffer/event/data 跨 chunk 保留，空行才发完整事件。chunk 不等于 event。

Legacy 明确隔离，不猜 Profile/Job/JD，不补假 Snapshot，旧 mutation 不能偷偷推进 index。

## 6. Evidence 体系

公共：`Source → SourceSnapshot → deterministic span → ClaimEvidence → Claim → Requirement`。

候选：`Resume raw_text → ResumeEvidence → CandidateFactEvidence → CandidateFact → normalized records`。

用户确认：`UserConfirmedEvidence`，明确自述，不伪装成简历摘录。

Evidence Matrix 是双方证据的对齐视图，不是原始事实来源。Coach 的 `evaluation:<id>` 是表现观察支持，不是长期能力证明。

- Public Evidence ID：Snapshot ID、位置、excerpt 的 hash；Resolver 精确回填，不模糊匹配。
- Resume Evidence key：Resume ID、section、normalized hash、offset；同材料重试稳定。
- page 无可靠来源时 NULL；offset 是文本字符位置，不是页面坐标。
- Schema 格式校验不等于 ID 存在，仍要查本次 catalog。
- Profile 连续摘录校验更强；Public Requirement 有合法引用不自动证明完整语义蕴含。
- Job-side 与 candidate-side 不能混：岗位需要 Kafka 不代表用户会 Kafka。
- `NO_EVIDENCE` 仅表示当前材料未证明，不能说用户不会。

deterministic 分组：

```text
independent_content_count = unique normalized content fingerprint count
occurrence_count = 输入中内容出现数量
multi_source = independent_content_count >= 2
source_count = independent_content_count  # 旧字段
```

独立文本不等于经过证明的独立作者或绝对真实来源。转载同文不增加 independent count。

## 7. Version Pinning 与 Snapshot

- ProfileVersion：完整不可变快照；0021 trigger 保护版本、子记录与封存历史。
- JDVersion：`(job, version_number)`、`(job, fingerprint)` 双唯一；相同正文复用。不能说具有与 Profile 完全相同的 immutable trigger。
- InterviewSnapshot：固定 Profile/JD/Requirement、题目实际内容/fingerprint、面经摘要、Evidence、Memory/Observation、budgets。
- MatchRun：固定匹配当时 Profile、filters、偏好实际内容、候选/排除岗位、JD/Requirement、policy/prompt。
- Report：固定 Snapshot、有效 Evaluation fingerprint、statistics、Prompt、generation key。

MatchRun 解释当时推荐；InterviewSnapshot 固定本场面试。历史推荐点击面试重新检查当前状态，岗位关闭不能直接启动。

Memory 只在 Session 创建时读一次。中途批准内容只影响下一场，不让本场上下文漂移。

## 8. 幂等、事务、并发与重试

| 流程 | Key/约束 | 实际边界 |
| --- | --- | --- |
| Profile | Resume/raw/report/prompt/hash/显式 regen token digest | 成功 key 复用；Version/子记录/最后 current 同事务 |
| Answer | `(session_id, idempotency_key)`、每题 Answer 唯一 | 同 key 换内容 conflict；成功返回持久化 result events |
| Evaluation | answer_id unique | 防多个有效评价；状态与下一动作一起提交 |
| Report | Session/Snapshot/Evaluation/prompt/显式 request key | 自动 retry 同 generation，Attempt history 保留，version fencing |
| Memory | Proposal 行锁/PENDING、memory 关联约束 | Item/Event/Decision/Audit 同一个 commit |
| Matching | `(user_id, request_key)` unique | 输入冻结；生成 lease/attempt fencing；完整推荐发布 |
| Public | URL hash、source/content hash unique、Task 可选 key | 重复采集复用内容，保留每次 Invocation |
| Celery | stale task_id、Profile generation key | 防重投递重复发布；没有完整自动 retry/backoff 配置 |

幂等发布不等于模型 exactly-once。Profile/Interview/Report 并发可能重复模型调用和费用，最后有效发布受 DB 与版本保护。

Memory approve：`lock PENDING → Item flush → Event flush → Proposal决定 → API审计 → 一个 commit`。`decide()` 不自行 commit，也不拼接旧 service 内部 commit。任一步失败整体 rollback。

Profile correction：旧完整 snapshot + 修改 → 完整 N+1，保存 base/old/new/reason/user，不更新历史。

Report current：完整内容和子记录成功后，最后旧 current=false/new=true 同事务。partial unique + completed check 保证单个当前成功报告。

删除保护：被 Evidence/Profile 引用的 Resume 先查 DB 再删除 MinIO，409 + RESTRICT；存在 Report generation 的 Session 同样保护 provenance。

## 9. Memory 的五层语义

| 层 | 语义 |
| --- | --- |
| CandidateProfile/UserSkill | 简历或用户确认的相对稳定事实 |
| InterviewEvaluation | 本轮回答表现 |
| PerformanceObservation | 本场面试观察，不是稳定个人事实 |
| MemoryProposal | PENDING 建议 |
| UserMemoryItem | 用户批准/确认的长期记忆 |

一次 Kafka 回答不好不能自动变成“用户 Kafka 能力差”。

第二场 Snapshot 分开 `approved_memory_context` 与 `recent_performance_observations`，各自数量/预算、ID/version/fingerprint、实际截取内容和 omitted 信息。默认 8/4000 与 5/2000；预算是保守 UTF-8 字节，不是 tokenizer。

读取还检查有效期、scope、最新内容变更的用户确认和 Proposal 决定；READ event 不算确认。

新 UserSkill 用有限 evidence state；旧 UserSkillProfile 保留 level_score/confidence 等 legacy 表现语义。新 M4/M5 不自动写旧技能表。

新式 Session 的旧 `/memory/consolidate` 返回 conflict，不能绕过审批；只对明确 legacy 兼容，不根据时间猜。

## 10. Public Data Research Infrastructure

- Gateway：先 RUNNING audit commit，再调用 Provider；成功 audit 与 Source/Snapshot 同事务，失败有 FAILED audit。
- 未注册工具在注册检查处拒绝，不能说所有无效工具名都建立 Invocation。
- Search：Source 与 metadata，不建正文 Snapshot。
- Fetch：真实正文成功才建立 Snapshot；snippet 不能作为正式网页证据。
- SearXNG：共享 SearchProvider Contract，JSON `/search`、time range、URL normalization、结果去重、结构化错误；旧 query builder 保留。
- Safe Fetch：HTTP/S、URL canonicalization、非公网/DNS检查、逐跳重验、timeout、大小/type限制、固定 UA、正文提取。
- DNS check 与连接未完整 IP pinning，不能宣称完全防 DNS rebinding。
- Snapshot content_hash 当前是实际提取正文 SHA-256；独立分组是 normalize 后 fingerprint，语义不同。
- Replay：fixture-only，无真实网络 fallback；共用 Contract/Gateway/audit/persistence/resolver。
- 旧 ExperienceCollectionTask 通过 adapter 绑定 PublicDataRefreshTask，不是第二套采集状态真相。
- Refresh policy：missing/stale/insufficient，阈值配置化。

状态：`PENDING → SEARCHING → FETCHING → READY_FOR_EXTRACTION → EXTRACTING → VALIDATING → PUBLISHING → COMPLETED`，失败 FAILED。

Live Search 因历史上游 CAPTCHA/rate limit 冻结；本次未重新联网验证，不宣传稳定通过。

## 11. Job Matching 深挖

默认 Top-N 20、SQL window 200；读取活跃 Company/Job、最新 PUBLISHED JD、Requirements、tags/地区/校招类型/岗位族/排除项。

Eligibility：SATISFIED / NOT_SATISFIED / UNKNOWN。非空学历/专业/年份限制当前仍 UNKNOWN，因为候选侧没有可靠结构字段。明确不满足主要来自地区、类型、标签或显式排除，不是完整学历硬筛。

Matrix：normalize + literal overlap + label + 合法证据；Java 不匹配 JavaScript。不是 embedding 或自由技能别名推断。

```text
pre_rank   = qualification/evidence tiers + supported/partial/gaps/tags/id
agent_rank = 合格候选内模型顺序
final_rank = qualification tier + evidence ceiling tier + agent/pre rank + id
```

等级有 deterministic ceiling：高度匹配、较为匹配、可尝试、信息不足。不是录取概率，不显示虚假百分比。

仅批准 preference/goal 参与软偏好；Observation/任意 Memory 不作为硬资格证明。

零候选 coverage insufficient，不调用 Research；非零少于 Top-N 可为 LIMITED，并非一律拒绝。

## 12. Model Infrastructure

- `CompatibleModelProvider.generate()` 调 `/chat/completions`，`response_format=json_object`；是 JSON mode + 应用 Pydantic，不是所有 Provider 原生 strict JSON Schema。
- Router 从 route-specific 或统一配置取模型；Agent 不硬编码。六角色有至少七 routes，因为 Research extract/quality 分开。
- PromptDefinition 保存 name/version/system/content_hash。
- Structured Output：parse → schema → 最多一次 constrained repair → hard fail。
- Repair 保护事实字符串，但不是所有数字与语义的形式化等价证明。
- Fallback 只用于明确 availability：model/endpoint missing、provider retriable availability、rate/quota、HTTP 429/502/503/504 等。
- Schema/Evidence/业务失败、普通 timeout 不自动 fallback。响应慢不能刷模型。
- Guard 是 event-loop/进程内 Semaphore，不是跨 Worker 分布式全局上限。
- 审计复用泛化后的 ExperienceAgentRun/StepRun，再由各领域关联 Task/Generation/Call/Attempt。
- 保存实际 provider/model/fallback reason、prompt、latency、usage、repair、error，不保存隐藏推理。
- 未返回 usage 不猜；未配置价格 cost=NULL。

## 13. 测试与 Real Model 验收边界

最终历史基线：backend 215、frontend 11、build/compile/parse/browser 通过。前端主要 Node tests，不等同 11 个完整浏览器组件测试；Playwright 为 CLI 流程验收记录，不应说已有完整 CI E2E spec。

测试层次：纯规则、PostgreSQL integration、Replay E2E、Real Model smoke、Browser。

F2 真正成功：六角色 DeepSeek；Profile → Matching → M4 → Coach 核心持久化；没有 fallback。

F2 数据边界：Research fixture Snapshot、TEST TXT、fixture ResumeReport、DEMO PUBLISHED岗位/approved题目、固定 TEST answers。没有验证完整 HTTP upload/Celery/旧 ResumeReport 真实模型链，也没运行 Live Search 或真实 Memory approval。

成功场：3 Answer/3有效Evaluation、两主问题+一FOLLOW_UP、COMPLETED、无未完成泄漏；Report current；7 Observation、2 PENDING Proposal；Memory/旧技能表未自动写。

五维平均：3.6667 / 2.6667 / 2.3333 / 4.6667 / 4.3333；overall 3.5333，程序计算。

| Role | 成功/失败请求 | 累计 latency ms | Input | Output | Reasoning | Repair |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Research | 2/0 | 39967 | 1618 | 2578 | 2360 | 0 |
| Profile | 1/0 | 49231 | 2106 | 3622 | 2863 | 0 |
| Matching | 1/1 | 172761 | 6971 | 9189 | 6283 | 0 |
| Main | 6/0 | 86008 | 28677 | 5559 | 4598 | 0 |
| Evaluation | 5/0 | 140046 | 23128 | 8885 | 7082 | 0 |
| Coach | 1/0 | 73893 | 6574 | 4891 | 3570 | 0 |

表含保留的失败与预算场次。已返回 total=103798；reasoning 是 detail，不重复相加；超时 usage 未知，不是完整账单。

Matching 首次 60s timeout，延长等待后同 DeepSeek retry 成功；第一场 reservation 结束，2评价+1跳过，保留；只调 smoke 子进程 reservation 创建新场，不改旧 Snapshot/Controller。

真实 Redis STRONG_EVIDENCE→DEMONSTRATED，说明模型提出的可计算字段应受证据约束。真实调用 repair=0；repair/hard-fail 与 fallback 由测试覆盖，备用模型未真实验收。

## 14. 数据库领域与重点表

领域：Auth/User；Resume/Profile；Public Career；Interview；Report；Memory；Matching；Agent/Tool Audit。

重点 20 表：

| 表 | 面试应讲的点 |
| --- | --- |
| users | 所有权根、角色 |
| resumes | 原材料、文件、raw_text、状态 |
| resume_reports | 解析成果与 Profile 失败隔离 |
| candidate_profiles | user unique、current 可空 |
| candidate_profile_versions | 完整 snapshot、version/key、不可变 |
| resume_evidence | 稳定依据、offset/hash、删除保护 |
| candidate_facts | 统一事实与 provenance |
| companies | normalized name unique、独立 Alias |
| jobs | Company FK、公司内 title unique |
| job_description_versions | 版本号/正文 fingerprint 双唯一 |
| job_requirements | JDVersion 与 Claim 关联 |
| source_snapshots | 正文、source/hash unique |
| claim_evidence | 陈述到真实 excerpt/location |
| interview_sessions | active/state_version/budget/workflow |
| interview_context_snapshots | 固定本场材料 |
| interview_session_questions | 实际题/来源/parent/kind/status |
| interview_answers | 幂等业务提交 |
| interview_evaluations | answer unique、五维 check |
| final_interview_reports | generation/current/statistics/analysis |
| memory_proposals | 原始提议、支持、审批与 Memory关联 |

还需熟悉 normalized experiences/skills、CandidateFactEvidence、UserMemoryItem/Event、Observation、MatchRun/Candidate/Recommendation/Attempt、ReportAttempt。

迁移：0017 review notes；0018公共领域；0019工具/刷新；0020Research；0021Profile；0022Interview；0023Report/Proposal；0024Matching。历史 migration 冻结。

## 15. 源码阅读顺序（30 个文件）

| # | 文件 | 重点/读完应理解 |
| ---: | --- | --- |
| 1 | [main](../apps/api/src/interview_api/main.py) | router/异常/实际模块 |
| 2 | [provider](../apps/api/src/interview_api/infrastructure/models/provider.py) | 请求、usage、availability |
| 3 | [router](../apps/api/src/interview_api/infrastructure/models/router.py) | route/config/guard/fallback |
| 4 | [structured](../apps/api/src/interview_api/infrastructure/models/structured.py) | Schema、repair、hard fail |
| 5 | [audit](../apps/api/src/interview_api/modules/public_career/research/audit.py) | ResearchModelCalls 与共享审计 |
| 6 | [resume router](../apps/api/src/interview_api/modules/resume/router.py) | 上传与投递边界 |
| 7 | [resume processor](../apps/api/src/interview_api/modules/resume/processor.py) | Parser/旧Report/Profile隔离 |
| 8 | [profile models](../apps/api/src/interview_api/modules/candidate_profile/models.py) | 根、版本、事实、规范化表 |
| 9 | [profile evidence](../apps/api/src/interview_api/modules/candidate_profile/evidence.py) | 稳定ID、state、validator |
| 10 | [profile agent](../apps/api/src/interview_api/modules/candidate_profile/agent.py) | generation 与模型 |
| 11 | [profile service](../apps/api/src/interview_api/modules/candidate_profile/service.py) | persist/correct/current |
| 12 | [public models](../apps/api/src/interview_api/modules/public_career/models.py) | ER/unique/nullable |
| 13 | [gateway](../apps/api/src/interview_api/modules/public_career/tools/gateway.py) | 审计、Search/Fetch 持久化 |
| 14 | [public evidence](../apps/api/src/interview_api/modules/public_career/research/evidence.py) | exact span catalog |
| 15 | [research agent](../apps/api/src/interview_api/modules/public_career/research/agent.py) | targets/Claim/JD/gate |
| 16 | [retrieval](../apps/api/src/interview_api/modules/job_matching/retrieval.py) | bounded SQL/snapshot |
| 17 | [match policy](../apps/api/src/interview_api/modules/job_matching/policy.py) | eligibility/matrix/rank |
| 18 | [match resolver](../apps/api/src/interview_api/modules/job_matching/resolver.py) | IDs/explanations/ceiling |
| 19 | [match agent](../apps/api/src/interview_api/modules/job_matching/agent.py) | 模型角色权限 |
| 20 | [match service](../apps/api/src/interview_api/modules/job_matching/service.py) | REPEATABLE READ/lease/发布 |
| 21 | [interview context](../apps/api/src/interview_api/modules/interview/context.py) | snapshot/policy/source validation |
| 22 | [interview agents](../apps/api/src/interview_api/modules/interview/agents.py) | Main/Evaluation/Prompt |
| 23 | [controller](../apps/api/src/interview_api/modules/interview/controller.py) | 唯一业务状态写入口 |
| 24 | [core service](../apps/api/src/interview_api/modules/interview/core_service.py) | 读→模型→短事务→SSE |
| 25 | [statistics](../apps/api/src/interview_api/modules/interview_report/statistics.py) | 有效评价与数字 |
| 26 | [coach agent](../apps/api/src/interview_api/modules/interview_report/agent.py) | 输入/预算/输出 |
| 27 | [coach resolver](../apps/api/src/interview_api/modules/interview_report/resolver.py) | 引用/recurring/定性限制 |
| 28 | [report service](../apps/api/src/interview_api/modules/interview_report/service.py) | generation/attempt/current |
| 29 | [proposal service](../apps/api/src/interview_api/modules/memory/proposal_service.py) | 单事务审批/provenance |
| 30 | [memory snapshot](../apps/api/src/interview_api/modules/memory/snapshot.py) | 两组语义/预算/固定 |

结合 0021–0024 migration 阅读，区分应用校验与 DB 强制不变量。

## 16. 面试最值得讲的十个设计

| 问题 | 设计/原因 | Trade-off | 位置 |
| --- | --- | --- | --- |
| 模型越权推进 | artifact 与 Controller 分离 | 编排复杂、可能重复调用 | interview/controller、core_service |
| 事实无依据 | 两类 Evidence + exact resolver | 保守，不等于语义真理 | profile/research evidence |
| 历史漂移 | Version + Snapshot | 复制与存储成本 | profile service、interview context |
| 模型慢且并发 | 事务外调用、短锁 version 重验 | 陈旧调用浪费 token | core_service/controller |
| 重试重复写 | key/unique/attempt fencing | 不保证计费 exactly-once | 各 service/models |
| 技能夸大 | min(proposed,max_supported) | literal/section可能漏判 | profile evidence |
| 高fit覆盖硬资格 | eligibility/fit分层、固定rank | UNKNOWN多、保守 | matching policy |
| 表现污染长期事实 | Evaluation→Observation→Proposal→用户批准 | 增加确认步骤 | proposal service/snapshot |
| regenerate破坏旧报告 | 最后current切换、partial unique | generation管理成本 | report service/0023 |
| 外部不可重放 | Replay复用同领域路径 | 不证明真实依赖稳定 | Providers/E2E tests |

## 17. 十个真实限制

1. Live Search：上游限制；稳定 Demo 用 Replay；未来考虑稳定合法源与健康观测，不承诺绕反爬。
2. Parser：无通用 OCR、DOCX表格/页位置能力有限；未来保留位置的解析与扫描件处理。
3. Profile：连续摘录、行/section规则保守；未来事实组合与字段级支持规则。
4. 资格：学历/专业/年份 UNKNOWN；未来明确结构化用户确认，不自由猜。
5. 公共发布衔接：APPROVED/PUBLISHED断点与 Requirement整句topic；未来明确发布和受验证归一化。
6. Budget：保守字节reservation、失败不退款；未来tokenizer与usage reconciliation。
7. 延迟：真实 Matching 超过100秒、Coach较慢；未来异步生成/缓存/输入优化，无现成性能提升数字。
8. 并发/事务：进程内guard、可能重复模型调用、无outbox、Research读取事务较长；未来分布式限流/lease/outbox/短事务。
9. 安全/语义：DNS TOCTOU、regex防护不是形式证明；未来egress/IP pinning与对抗校验。
10. 生产差距：本地与Fixture验收，没有规模/SLO/生产负载证明；Milvus并不贯穿六Agent主链；未来部署安全、监控和负载测试。

这些是能力边界和可能方向，不是本阶段开发授权。

## 18. 五十个源码面试问题与简答

1. 为什么六角色？——输入、输出、权限不同，分离公共/候选/匹配/出题/评价/复盘。
2. 是否只有六次模型调用？——否，多轮、多步骤与旧模型链都存在。
3. Controller为什么不是Agent？——业务不变量要确定性。
4. 用户推荐会实时爬岗位吗？——不会，只读公共库。
5. LangGraph编排整个产品吗？——否，旧面经有，新核心由service/controller。
6. ResumeEvidence如何稳定？——Resume/section/hash/offset digest。
7. 相同文字不同上传为什么ID不同？——材料身份和provenance不同。
8. STRONG_EVIDENCE条件？——至少两条不同有效具体使用excerpt，不是精通认证。
9. 为什么只降不升？——约束夸大，不由程序扩张模型结论。
10. Profile失败为何不影响Report？——已提交报告与best-effort画像分阶段。
11. 一个用户多个Profile？——根唯一，历史Version多个。
12. current怎么不指半成品？——子记录完整后最后切换，同事务。
13. correction为什么复制完整版本？——每版独立解释，旧版不可变。
14. 用户确认是简历证据吗？——不是，单独UserConfirmedEvidence。
15. 不可变只靠Python？——Profile有0021 DB trigger。
16. 为何不全部Job送LLM？——bounded SQL降低成本/上下文/黑盒排序。
17. Eligibility与Fit区别？——资格/选择与材料对齐分开。
18. 学历为何UNKNOWN？——候选结构字段不可靠。
19. Matrix怎么防幻觉？——literal事实和真实Evidence，模型只选已有支持。
20. Explanation可自由改写？——当前必须复制程序支持文本。
21. 三rank是什么？——确定性预排、模型排序、固定政策合成。
22. 为何不用百分比？——没有校准概率，避免虚假精确。
23. NOT_SATISFIED入rerank吗？——不入，只保留excluded audit。
24. Memory能证明学历/技能？——不能，批准偏好只作软输入。
25. 旧推荐面试为何重读？——历史解释与当前合法开场不同。
26. Main为何不能插题？——Controller要检查状态/预算/来源/并发。
27. active ID优于index？——动态插题不漂移，明确实体。
28. version怎么用？——调用前expected、调用后短锁重验，不同则conflict。
29. 为何不持锁等模型？——延迟不确定，会阻塞并占连接。
30. 回答retry新增Message？——合法相同key复用，成功重放已存事件。
31. 同key换内容？——conflict。
32. 幂等等于一次模型调用？——否，只保证有效发布。
33. Eval成功Main失败？——可能尚未发布，保留审计，同Answer重试。
34. 为什么不fallback score=3？——伪造统计和下游Evidence。
35. 模型COMPLETE直接结束？——否，Controller确定性决定。
36. FOLLOW_UP没有独立Question？——当前有，带parent与独立Answer/Eval。
37. DEEP_DIVE区别？——更深主题，kind/source不同，仍受预算。
38. 如何防无限追问？——根题次数、总题、轮次、时间、reservation。
39. SSE为何commit后？——防浏览器看到未持久化状态。
40. Parser为何跨chunk保留？——事件与UTF-8字符可能跨chunk。
41. Report分数来源？——有效Evaluation DB重算，不是Coach/最后一题。
42. answered_count所有提交数？——当前是有有效评价的不同题数。
43. recurring怎么判断？——至少两个不同有效Eval，occurrence去重计数。
44. 同根follow-up两评价能算两次？——当前能，按Eval不是主题根。
45. failed regenerate替换current？——不，完整成功后才切换。
46. approve怎么原子？——行锁，Item/Event/Decision/Audit只flush，外层commit。
47. 并发approve？——后者看到终态，相同决定幂等，不重写Memory。
48. Memory与Observation为何分开？——确认长期内容与本场表现语义不同，分别固定。
49. 何时fallback？——明确availability，非Schema/Evidence/业务失败/普通慢响应。
50. F2证明什么？——真实DeepSeek结构/证据/核心持久化；不证明LiveSearch/生产规模/真实效果。

## 19. 事实与包装边界

### 可以写的事实

六职责Agent；统一Provider/Router/Prompt/Structured Output；公共/私有证据分层；画像版本与纠正；Controller状态机；回答幂等/短事务/SSE；数据库统计；报告current原子切换；用户审批Memory；deterministic资格/矩阵/排序；六角色真实DeepSeek smoke；最终215+11测试与本地浏览器验收。

个人简历仍须与本人实际参与范围一致。

### 需要限定的概括

- Evidence grounding：约束输出，不是彻底消除幻觉。
- 并发一致性：防陈旧覆盖，不是已证明任意高并发。
- Memory：用户审批的长期建议，不是自动自我成长。
- Real Model E2E：LIVE model + Fixture data核心链，不是全Live网络。
- RAG：既有检索能力，不是新面试每轮向量检索。
- Safe Fetch：基础SSRF防护，不是绝对安全。
- 历史不可变：区分DB trigger和应用版本化写入路径。

### 不应宣称

生产大规模用户、高并发吞吐、性能/成本提升百分比、录取成功率、评分客观准确率、稳定LiveSearch、完整学历筛选、所有LLM已统一、exactly-once计费、分布式事务、成熟语音面试、全简历OCR、production-grade认证。

推荐概括：基于 FastAPI/Vue/PostgreSQL 构建秋招求职与模拟面试平台，通过六个职责分离的Agent完成抽取、画像、匹配、出题、评价和复盘；以Evidence、版本快照、确定性Controller和用户审批Memory限制模型权限，并完成Replay与真实DeepSeek核心验收。

## 20. 源码与历史描述差异 / 使用方式

应特别记住：六正式角色不等于仅六内部Agent；Matching解释是选择/复制；Profile连续摘录；FOLLOW_UP独立题；answered_count是有效评价题数；APPROVED不等于PUBLISHED；raw hash不等于normalized fingerprint；guard非分布式；Celery非完整自动retry；F2 Report为fixture；真实repair/fallback未触发；M4短事务不能推广到全部Research；FastAPI metadata仍0.1.0，与Git 0.9.0不是同一版本；浏览器验收不是完整CI spec。

学习优先级：先理解第5/6/8/11/12节，再按第18节逐题练习。读文件时标注输入、输出、校验、权限与commit边界，而不是只背Agent名字。

本文新增后 worktree 会包含这一未提交文档；这与原始源码审查时的clean基线不同，不应改写历史事实。未进入新开发阶段。
