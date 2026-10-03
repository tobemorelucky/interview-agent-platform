# M6 Evidence-backed Job Matching

实现边界：M2 Research / M3 Profile / M4 Interview Core / M5 Report–Memory 全部冻结。
这是最后一个 Agent 功能阶段；完成后不进入 M7 或新增 Agent/主要业务功能。
参考 JobPilot 保持只读。没有 Research/联网补库、投递、简历优化或新的 Memory 系统。

## 1. Schema 与输入边界

`JobMatchRun → JobMatchCandidate → JobMatchRecommendation`，另有 `JobMatchAttempt`。
Run 属于 User，引用本人 immutable CandidateProfileVersion。Run 创建采用单个
REPEATABLE READ MVCC 事务，固定本次实际输入，不在调用模型时持有数据库事务/行锁：

- ProfileVersion ID、facts、normalized record refs、Resume/UserConfirmedEvidence 的实际原文/位置/hash；
- 显式 filters、已确认 preference/goal 的 ID/content/confirmation event/version/fingerprint 和预算；
- 有界 candidate/excluded Job IDs、Company/title/status、JDVersion ID/version/content fingerprint、
  structured matching constraints、Requirement ID/statement/normalized value/Claim ref、JobTags；
- deterministic Eligibility/Evidence Matrix/rank components、policy version、prompt version/hash；
- 实际输入 SHA-256 fingerprint 和 DEMO_REPLAY / PERSISTED_PUBLIC_DATA 标记。

不复制完整 JD raw_text，但固定足够的本次实际比较字段和版本指纹。完成后 Profile、Memory、
JD、Job 状态改变都不能改写旧 Run。数据库 trigger 阻止输入 UPDATE/历史 DELETE、完成后
追加或改写候选/推荐；检查 Profile ownership 和 Company–Job–JD 一致性。来源 FK RESTRICT。

## 2. 有界 deterministic retrieval / eligibility

SQL 先读取 ACTIVE Company/Job、最新 PUBLISHED JD、有 Requirements 的岗位，再按成熟
结构化 campus type / job family / tag / 明确排除项 / JD structured locations 筛选；NULL、
缺失或畸形数据保留 UNKNOWN，不猜值。JobTag overlap 是确定性的 SQL 初排信号。
最多物化 `JOB_MATCHING_RETRIEVAL_LIMIT=200` 个 admitted 候选，另有有界 excluded 审计。
代码计算完整 Eligibility/Matrix/rank 后取 `JOB_MATCHING_TOP_N=20`（允许配置 1–50），
只有这个集合交给模型，excluded candidates 不在模型输入。

首版不承诺整个库的穷尽最优排序；覆盖状态有 SUFFICIENT/LIMITED/INSUFFICIENT、
window_may_be_truncated、Top-N/窗口预算与省略数量。无候选时保留 Run/失败尝试，
返回 MATCH_COVERAGE_INSUFFICIENT，不调用模型或 Research。窗口不是市场覆盖率。

硬条件只读 JD `structured_data_json.matching_constraints`，例如：

```json
{"locations":["北京"],"education_minimum":null,"majors":[],"graduation_years":[]}
```

显式 null/空数组表示发布数据明确没有该资格限制；字段缺失表示 UNKNOWN。
学历/专业/毕业年份非空要求也保持 UNKNOWN：冻结的 M3 没有可靠结构化资格字段，
不能从教育 description、自由文本 Memory 或技术交集推断。没有扩展 M2/M3 或回填这些字段。
NOT_SATISFIED 单独保存，rank 全部 NULL，数据库 CHECK 和 resolver 禁止混进正常推荐。

## 3. Evidence Matrix / Agent / ranking

Matrix 状态 SUPPORTED / PARTIAL / NO_EVIDENCE / NOT_APPLICABLE。只做规范化字面交集，
例如 Java 不匹配 JavaScript，不使用语义别名、embedding 或开放式技能推断。
每个支持关联保存 ProfileVersion / CandidateFact / Project–Work–Education–Skill record refs、
真实 ResumeEvidence/UserConfirmedEvidence keys 和 JDVersion/Requirement refs。
项目/工作/实习字面支持或已有 DEMONSTRATED/STRONG_EVIDENCE 技能才计 SUPPORTED，
提及/摘要等仅计 PARTIAL；资格要求不作为技术匹配证据。

NO_EVIDENCE 固定表达为当前材料没有相关证明，绝不转成“不会/不具备”。
JobMatchingAgent route `job_matching` 复用既有 ModelProvider/Router/PromptRegistry/
Structured Output/最多一次 fact-preserving constrained repair/concurrency guard/
ExperienceAgentRun–StepRun。Agent 不写 M2–M5。

严格 MatchArtifact：候选 ID 的完整排列、定性 band、matched strengths、alignment reasons、
真实 gap/preparation refs、待核实字段。ID 采用 job:/alignment:/fact:/requirement: 或真实
Evidence key；resolver 校验精准关联、支持级别、已提供的确定性解释原文。不让模型发明
证据、能力结论、岗位或资格；可选择真实关联，不接受开放式编造理由。gap 必须完整保留，
UNKNOWN 不能隐藏或猜成满足。Schema 不提供分数/百分比/可覆盖的 Eligibility 字段。

版本化 policy `literal-evidence-eligibility-v1`：

- pre_rank：Eligibility tier → evidence tier → supported/partial/gap counts → SQL tag overlap → Job ID；
- agent_rank：只在非 NOT_SATISFIED 集合中排序；
- final_rank：固定 Eligibility tier → evidence tier → agent rank → pre rank → Job ID。

band 的确定性上限：无交集 UNCERTAIN；少量交集 EXPLORATORY；至少一半要求有
SUPPORTED 交集 GOOD_MATCH；全部适用要求有支持、至少两个支持要求、Eligibility
SATISFIED 才允许 STRONG_MATCH。模型可以保守降级，不能突破上限；不是真实能力认证。
三层排序均持久化，UI 展示语义等级而非虚假精确能力百分比。

## 4. preference、重试与事务

只读现有 UserMemoryItem/UserMemoryEvent/MemoryProposal；使用有效 PRIVATE Memory，
最新内容事件必须本人 USER 确认，类型 PREFERENCE 或明确 goal。MEMORY_PROPOSAL 来源
还必须真实关联 APPROVED/CORRECTED preference/goal proposal。PENDING/REJECTED/技能
Memory/自动 Agent 来源/PerformanceObservation 不进入稳定偏好；Memory 不能作资格证据。
独立数量和 UTF-8-byte 预算固定实际截取内容、版本、fingerprint 和 omitted count，不写 READ event。

`(user_id, request_key)` 唯一。相同 key/请求是同一冻结 Run；不同 filters 用相同 key 返回 409。
用户主动新匹配用新 key，自动 retry 复用 Run。READY/FAILED → RUNNING → COMPLETED/FAILED。
短事务领取 attempt/version，模型在事务外执行，resolver 完成后一次事务写所有推荐/rank，
最后封存 COMPLETED。发布失败全部 rollback，失败历史保留；不会改 InterviewSession。
RUNNING 重入返回 409，过期 worker lease 可重试，attempt_version fencing 阻止旧模型覆盖。
Prompt/hash 不匹配时拒绝用新 Prompt 替换旧输入。成功 Run 重试只读，不重复模型调用。

## 5. API/UI 与 M4 handoff

所有接口必须本人认证，跨用户 detail/generate 返回 404：

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | /api/v1/job-matches | request_key + optional own profile version + filters；创建固定 Run |
| GET | /api/v1/job-matches?limit=20&offset=0 | 本人历史分页 |
| GET | /api/v1/job-matches/{id} | 固定输入、候选/排除、推荐、审计尝试 |
| POST | /api/v1/job-matches/{id}/generate | 固定 Run 生成/安全 retry；FAILED 为持久化可读取状态 |

用户页 `/jobs/matches/:id?`：显式地区/校招类型/岗位族/技术标签/排除标签，历史、覆盖不足、
DEMO/REPLAY banner、Eligibility 与 gap、逐 Requirement 真实候选原文/位置、三层排序和面试按钮。
History ID 是 ProfileVersion 数据库引用，不是版本序号。

推荐按钮只把 company_id + job_id 交给现有面试页。消费 query 后清除，防止 reload 重建。
由已有 M4 create/start endpoint 重新读取当前 Profile/JD/已审核题库/批准 Memory、校验岗位
仍 ACTIVE，再固定全新 InterviewContextSnapshot。历史 Run 不变；关闭岗位返回明确提示。
只对前端增加引用入口，不改 M4 Controller/Agent/Workflow/服务创建规则。

## 6. Migration / 修改归属

正常 Alembic 只新增 `0024_add_job_matching.py`，upgrade/downgrade 已在空的独立库验证。
无历史 backfill，不触碰正式数据库。正式部署需先备份，再手动 `uv run alembic upgrade head`。

进入 M6 前工作树已有大量 M1–M5 未提交/未跟踪文件；全部保留，没有 reset/clean/checkout/
stage/commit。M6 新增范围：

- `apps/api/src/interview_api/modules/job_matching/`：models/schema/policy/preferences/retrieval/
  prompts/resolver/agent/service/router；
- `apps/api/alembic/versions/0024_add_job_matching.py`；
- `apps/api/tests/test_07_m6_migration.py`、`test_m6_job_matching.py`、fixture `m6_jobs_demo.json`；
- `apps/api/scripts/smoke_job_matching.py`、`m6_demo_runtime.py`（仅显式隔离 demo DB）；
- web matching API/types/page/测试。

已有文件仅最小接入：core/config、shared model router、统一 ORM registry、main/.env.example；
web router/nav/InterviewChatPage 的 references-only 入口；README 和 docs/05/08/12/19/24。
131 个前序领域/worker/migration0017–0023/依赖锁文件 SHA-256 baseline 均未改变。
未加依赖，uv.lock/pnpm-lock/pyproject 不变；JobPilot 未修改。

## 7. 实际验收（2026-10-03，非 Live）

最终完整 suite 在新空库 `interview_agent_m6_release_20261003` 执行：178 passed，1 warning，126.20s。
此前在 `interview_agent_m6_accept_20261003` 验证177项；新增并发创建幂等测试后，M6专项28项
通过（18.03s），再进行上述最终完整回归。
前端 vue-tsc + Vite build 成功，node tests 7/7。迁移 upgrade/downgrade 0024/0023/0024 包含在 suite。
首跑仅 M1_TEST_DATABASE_URL 隔离导致旧 M5 API 测试默认工厂仍读旧 schema，176 passed/1 failed；
两变量统一到新的空隔离库后全部通过，未改冻结 M5 代码/测试或旧数据库。

专项覆盖：Profile/Memory/JD pinning、真实批准/修正/拒绝/待审批 preference 投影、readonly/no READ
event、SQL Top-N/稳定 pre rank、Eligibility 三态、真实 Matrix、未知 ID/伪造理由/隐藏 UNKNOWN/
gap/虚假 score/band 拒绝、repair/hard fail、安全 retry/concurrency、输入预算/timeout、发布故障
整体 rollback、DB immutability/完整 seal、无数据不调用模型/Research、owner API、M4 handoff。

独立三岗位 smoke 实际落库：User 94、Run 26、M4 Session 62：

| DEMO 岗位 | Eligibility | band | pre/agent/final |
|---|---|---|---|
| AI Application Engineer（Job 135） | SATISFIED | STRONG_MATCH | 1 / 2 / 1 |
| Java Backend Engineer（Job 136） | UNKNOWN | EXPLORATORY | 2 / 1 / 2 |
| Data / Risk Analyst（Job 137） | NOT_SATISFIED 地区冲突 | 排除 | NULL / NULL / NULL |

Profile 96 → 用户 correction 97、JD 136 → 新版139、批准 Memory 内容更新后，旧 Run 全部
序列化内容未变化。M4 使用当前 Profile97/JD139 和更新后的批准 Memory，而非 Match 输入；
再将 Job135 关闭，M4 新建请求被拒绝，历史 Run 保持不变。输入 fingerprint：
`7b39121a2329cd6a2e954f636eaff35576e5cadd5e69e83c6c5ae1341b8730d5`。
所有 Replay smoke 以失败 sentinel 阻断 httpx 外网调用，不是仅配置约定。

Playwright 真实浏览器在独立库 `interview_agent_m6_browser_20261003`：实际认证登录 → 填北京/
CAMPUS → Run2 COMPLETED → 真实 Evidence/UNKNOWN/地区排除可读取 → 推荐按钮 → M4
Session1、Snapshot1、IN_PROGRESS → Answer1 对应 Evaluation1/provider=replay → Q2。
模型 Provider 和岗位均明确 DEMO/REPLAY/fixture；全程 API outbound HTTP 阻断。
最终页面还实际读取 Evidence5/projects/Resume1/offset76–104 的原文与位置；将合成 Job1
关闭后点击旧推荐，浏览器明确提示无法新建，Session 数仍为1，历史 Run1/2 均保持 COMPLETED。
Playwright 截图位于 gitignored `output/playwright/m6-match-final.png`。
仅发现已有 favicon.ico 404，无新增业务 console 错误。

## 8. 复现

使用新建的独立 m6_ 空库，切勿对有用户资料的数据库执行 migration downgrade 测试：

```powershell
$env:DATABASE_URL='postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_m6_<empty_test_suffix>'
$env:M1_TEST_DATABASE_URL=$env:DATABASE_URL
# apps/api
uv run pytest tests -q
uv run python scripts/smoke_job_matching.py
# 独立 DEMO DB 先 upgrade head，然后 seed/api
uv run python scripts/m6_demo_runtime.py seed
uv run python scripts/m6_demo_runtime.py api
# apps/web
pnpm build
node --experimental-strip-types --test tests/*.test.mjs
pnpm dev --host 127.0.0.1
```

新 route 可配置 JOB_MATCHING_MODEL/BASE_URL/API_KEY；空值复用已配置 LLM。Replay 依赖
显式 demo runtime 或测试注入，不会生产静默 fallback。Browser/demo 登录凭据仅为合成用户。

## 9. 已知限制与停止点

- 尚未进行 M6 Real Model / 真实岗位全链验收；这里的模型与岗位均为 Replay/fixture。
- Live Search CAPTCHA/rate-limit 原有限制冻结，不扩展搜索引擎、不联网补库。
- 资格数据不足保持 UNKNOWN；不新增 M3 字段或让 Memory 证明资格。
- literal Evidence 对自由文本长 Requirement 保守，可能产生较多 NO_EVIDENCE；不做语义技能猜测。
- 有界 SQL window/Top-N 不是全市场推荐；输入超过预算显式失败，不静默删除证明材料。
- 并发 guard 是复用现有进程内机制；不是新跨 worker 基础设施。
- 临时验收 API/Vite/浏览器在交付前停止；独立 demo/test 数据库保留以供复核，不清理用户数据。
- M6 交付后停止新 Agent/主要业务功能，不进入任何 M7。
