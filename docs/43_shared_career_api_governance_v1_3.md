# V1.3：共享职业资料与入口治理

日期：2026-10-19。范围仅为公共资料入口、身份/限流/模型用量治理、可逆模型配置和数据库选型评估。不新增 Agent，不重构 Skill V1.1、系统设计工具、Profile、Controller、Matching/Report/Memory 的领域规则，不恢复 Live Search。

## 1. 实施前审计与保留项

已读 AGENTS.md、docs/40–42 及真实代码。进入本轮时已有大量 tracked/untracked 修改，全部保留；未 reset、clean、stash、commit 或 push。历史 migrations 0017–0024 未修改。

已有且继续复用：

- 公共 Company / Job / JDVersion / Requirement / Source / Snapshot / Claim / Evidence，无 user_id；URL hash、正文 hash、JD version/fingerprint 唯一约束。
- ManualImportService 的 Source 行锁、PG ON CONFLICT、task idempotency key；Research 的短事务任务认领；失败后的显式维护者重试。
- JD 的 publication_status=PUBLISHED；面经现有正式可见状态为 APPROVED/AUTO_APPROVED（没有另造一套面经发布状态）。PENDING/拒绝/撤回内容不进入新普通用户接口。
- 私人实体归属检查、ProfileVersion / ContextSnapshot 不可变边界、Answer 幂等、Controller 预算与版本栅栏、Report current 原子切换、Memory 审批。
- Redis 入口频率限制、进程内 Provider semaphore。它们原本不等价于跨 Worker、跨 Session 的用户用量限制。

缺口是公共浏览入口、用户级持久化模型 admission/settlement，以及 Legacy LLM 的同类保护。本轮针对这些缺口增量实现。

## 2. 数据关系

```text
共享、无 user_id：
Company ─ Job ─ JobDescriptionVersion ─ JobRequirement ─ Claim
                                                      │
Source ─ SourceSnapshot ─ ClaimEvidence ────────────────┘
          │
          └─ 已审核 InterviewExperience / InterviewQuestion

User A / User B 各自：
Resume ─ ResumeEvidence ─ CandidateProfileVersion
                            ├─ JobMatchRun ─ Candidate / Recommendation ─ 引用共享 Job/JD
                            └─ InterviewSession ─ immutable ContextSnapshot
                                  ├─ Answer / Evaluation / 工具修订与结果
                                  └─ FinalInterviewReport ─ Observation / MemoryProposal
                                                               └─ 用户确认 ─ UserMemoryItem

User ─ ModelBudgetOwner（用户锁锚点）─ ModelAdmission（每次发送尝试的持久化账本）
公共维护任务 ─ 独立 system:public-career 锚点 ─ 同一账本机制
```

共享的是公共业务实体，不是用户画像/回答。面试仍固定本场实际使用的最小公共内容及 fingerprint，不能因“共享”删除必要的历史快照。

本轮不是 PostgreSQL RLS 多租户方案：隔离依赖服务器鉴权、资源 owner 查询及既有 FK/版本约束，不能声称数据库登录账户本身只能看某个用户。

## 3. 公共接口与页面

所有以下 API 均需正常 JWT，沿用普通用户 Redis 限流：

| API | 语义 |
| --- | --- |
| GET /api/v1/public-career/jobs | 活跃 Company/Job 的最新已发布 JD；分页，最多 100 条 |
| GET /api/v1/public-career/jobs/{job_id} | 已发布正文和岗位要求，不返回内部 metadata/Prompt/Rubric |
| GET /api/v1/public-career/experiences | 只展示已审核面经的公开摘要 |
| GET /api/v1/public-career/experiences/{id} | 已审核面经及允许公开的问题，不返回参考答案 |
| POST /api/v1/public-career/source-requests | 仅记录/合并来源申请，**不执行 Search/Fetch/模型** |
| GET /api/v1/public-career/my-model-usage | 仅当前用户的当日应用额度概览，不是服务商账单 |

普通页面 `/career` 纳入同一主导航，展示公司、岗位、来源身份、更新时间、JD 要求、面经摘要。缺少来源身份时明确显示“未标注”，不冒充 official。原文和外链均使用正常 Vue 转义/HTTP(S) 链接。资料是只读投影，不开放普通用户编辑/发布。

从岗位卡进入面试仅携带 company_id/job_id；原 M4 创建流程重新读取当前 Profile/JD/已审核题源与 Memory，Skill V1.1 正常自动推荐、手选和固定。进入匹配页面仍使用现有个人匹配逻辑，不因共享库改变资格或排名。

### 来源请求与更新

来源申请先使用已有 URL 安全规范化（拒绝 localhost/private literal、凭据/敏感参数，去 tracking），再以 URL + document type 的 SHA-256 构造唯一 task key。两用户并发申请同一地址取得同一 PublicDataRefreshTask ID。target_type=SOURCE_REQUEST，reason 中明确 USER_REQUEST_ONLY / ADMIN_REQUIRED；不会自动进入 Research 或 Celery，也没有普通用户爬虫入口。

管理员仍用现有人工正文 importer/审核路径采集和处理。来源申请是待核实队列，不是完整自动调度系统；没有新增队列管理 UI，也不宣称申请会自行完成。真正采集复用 Source，正文变化创建新 Snapshot/任务/JDVersion；相同 URL+正文重试复用旧对象。正文相同的跨 URL Snapshot 仍可同时存在，独立内容数由现有 deterministic grouping 计算。

Redis 只负责速率/辅助协调，不决定唯一性。公共 Source/Snapshot 是数据库中的共享可复用资料，不新增有撤回失效风险的 Redis 正文缓存。

## 4. FastAPI 治理边界

- **认证**：JWT + active User；非法 subject 正确返回 401，而非 int 转换 500。
- **授权**：沿用 ADMIN dependency、私人资源 user_id 过滤，普通用户不能从共享入口写最终实体。未知/越权私人资源统一 404。
- **限流**：已有用户级 Redis 滑动窗口；登录/注册增加 30 次/10 分钟的 peer-IP 桶，不信任未配置代理的 X-Forwarded-For。面试 start、Profile generation dispatch、Legacy QA 增补高成本入口限流。
- **并发**：Provider 进程 semaphore 保留；用户 admission 在等待它**之前**执行，防止多 Session 先无限排队、绕过用户并发拒绝。
- **配额**：PostgreSQL 用户锁锚点串行化短 admission/settlement 事务。默认每用户每日 100 次发送尝试、1,000,000 admission Token、同时 2 个，环境配置可调。UTC 日期结算；公共后台维护任务使用独立系统桶，不与普通用户私有 Session 混合。
- **错误**：额度/并发 429，归属缺失 403，幂等冲突 409；Matching/Report 即使先持久化 FAILED audit，也在 API 层返回额度错误。SSE 已开始发送时仍按既有 error event 返回业务错误，不伪造 HTTP 状态可回退。
- Redis 不可用：test/production 返回 503；原 development fail-open 行为保留并记录 warning。**模型额度的 PostgreSQL admission 不 fail-open。**

Redis client 改为按 event loop 复用，并在 API lifespan 关闭，修复全量异步集成中旧 loop 连接导致首个请求 503 的问题。

### 模型账本与不确定结果

`model_budget_owners` 提供跨 API/Worker 的固定行锁；`model_admissions.call_key` 唯一，保存 owner/route/model、预留、实际 usage（若返回）、状态和 lease。

```text
短事务：锁 owner → 检查当日额度/活跃 lease → RESERVED → commit
事务外：等待 Provider 并发槽 → 有界模型 I/O
短事务：同 owner 锁 → SETTLED（真实 usage）或 UNKNOWN（保留预留）
```

模型输入 UTF-8 序列化字节数 + 输出上限用于保守 admission 估算，**不是 tokenizer 实测，也不是金额**。有真实 total_tokens 才按实际结算。模型发出后超时、取消、usage 缺失或进程失联均保留预留；过期 lease 可释放并发位置，但不乐观退额度。过期后晚到 usage/重复 settle 不允许再次返还；等待 Provider 槽期间若尚未发送即被取消，明确按未发送结算 0 Token，发送尝试计数保留。没有“失败一定免费”的假设。

每个 constrained repair / fallback 都独立 admission，不复用上次调用预留。Legacy SDK 自动 retries 关闭，避免隐藏 HTTP 重试绕过账本；Legacy chat/stream 同样设置输出上限、总 deadline，并在断连时保守结算。Resume Worker 从真实 Resume owner 绑定身份；六 Agent 从既有 ProfileGeneration/InterviewAgentCall/ReportAttempt/MatchAttempt 等持久化 FK 解析归属，不信任请求体传 owner。

此账本不是外部资金账本，不保证服务商计费规则，也不能确认赠送额度覆盖。UNKNOWN 可能多扣应用额度；需要有真实 provider reconciliation 证据才能设计维护者调账，当前不提供猜测式自动退款。DB 结算故障时预留仍在，后续 lease 回收，不重复发送模型。

## 5. 模型临时切换与费用阻塞

`.env` 仍被 Git ignore。仅修改原 LLM_MODEL / LLM_FALLBACK_MODEL 两项，原值以 `# V1.3 restore:` 注释保留，Key、endpoint 和其他设置未改。当前解析结果：

| 路径 | 有效模型 | thinking |
| --- | --- | --- |
| public_research_extract / quality | deepseek-v4-pro-ga-260813 | disabled（保留原策略） |
| resume_profile | 同上 | disabled |
| job_matching | 同上 | disabled |
| interview_main / answer_evaluation | 同上 | disabled |
| interview_coach | 同上 | disabled |
| Legacy LLM | 同上 | 保留原请求参数，不伪称已实际验过 |

统一 Provider 为现有 openai_compatible / 原方舟地址；fallback 当前清空，避免这次验收隐式调用豆包。上述是**配置解析通过**，不是模型网络可达性或费用安全证明。

以后用户明确要求恢复时：将注释保留的两行 `doubao-seed-2-1-turbo-260628` 恢复为有效配置，移除/注释当前 DeepSeek 行，重启项目所属 API/Worker 以刷新进程 settings。其他 route override 为空时继承 LLM_MODEL，不需在 Agent 写模型名。`.env.example` 继续保留原豆包默认值，并说明临时切换及新增应用额度。

**真实模型调用 0 次；无实测 Token/latency。** 本地 API Key 和模型 ID 无法证明可用赠送额度、抵扣顺序及超额付费已禁止；当前没有可验证的账单/硬支出保护连接，因此未发送可收费的“最小连通测试”。V1.2 的两个 LIVE 场景仍待费用安全证据，14 逻辑 / 28 HTTP / 15 分钟上限未启动、未消耗。Replay 不代表真实 DeepSeek 会正确选择工具。

## 6. Migration / 正式库保护

仅新增 `0025_add_model_admission_ledger.py`，两张空账本表、FK/唯一/检查约束和 owner 索引；无旧数据 backfill。downgrade 在 ledger 非空时明确拒绝，不能静默丢弃用量审计。

正式库原 revision=0024。先在隔离库验证 upgrade/downgrade 和全量回归，再使用已有 PG 容器原生 pg_dump custom 备份；已通过 pg_restore --list 解析校验，未进行含真实个人数据的测试克隆/恢复演练。

备份位于本地 ignored `.runtime/backups/interview_agent_before_0025_20261019_v13.dump`，1,273,620 bytes，SHA-256：`7AAD71B636449A6A61533EB6EE3E2BC73E1C7CEC787A4B89657AFCA98CFD5DC0`。容器 `/tmp` 也保留该备份；未删除旧备份或 volume。

正式 `alembic upgrade head` 成功，current=0025。新表均 0 行，以下原数据计数完全未变：users 5、resumes 38、ProfileVersion 1、sessions 19、answers 45、evaluations 41、reports 5、Memory 4、Job 4、JD 4、Experience 5、MatchRun 9。没有把 DEMO 测试写进正式库。

## 7. 实际验证与失败记录

- 新专项：9 passed（真实 PostgreSQL，HTTP ASGI JWT/权限，离线 Provider/MockTransport；包含实际 B 工具结果与成功报告的越权负例）。
- 完整新库 `interview_agent_f4_regression_v13_20261019_c`：**399 passed / 154.38 秒**，原 389 项 + 本轮 9 项业务/治理专项 + 1 项 migration。未减少覆盖。
- 前端：**40/40**，其中新增两项明确是静态 UI 契约检查，不冒充组件/浏览器测试；TypeScript / production build 通过。
- 独立浏览器：真实 Vue + FastAPI + PostgreSQL + Redis + JWT；仅模型替换为明确 Replay，未使用 route.fulfill 拦截业务 API。
- 专用库 `interview_agent_v12_acceptance_v13_20261019_a`：A 正常登录 → 公共 JD 1 → 自动 Java Skill → 项目深挖/标准/系统设计 → Session 12 三轮 → 实际 verify_idempotency / verify_capacity / verify_capacity，FAIL/FAIL/PASS → COMPLETED（3 Answer/3 Evaluation）→ Report 2 → 刷新恢复。
- B 经注册页和登录页创建，读取相同 JD 1 为 200；A 的 Session 12、对应 Report、Resume 1、ProfileVersion 1 均 404；B 的 Session 列表为空。已审核面经及岗位列表正常加载。隔离库中的编号不能与正式同号数据混同。
- 浏览器安全负例带来预期 HTTP 404 console errors，不宣称控制台所有请求都 200。
- 截图：`output/playwright/v13-shared-library.png`、`v13-real-api-report.png`、`v13-shared-user-b.png`；均为 ignored 本地证据。
- 最后复核：岗位详情在对应卡片内展开，`inline_detail=true / error_count=0 / navigation=true`；最新截图 `output/playwright/v13-shared-library-final.png`。中断后正常 JWT 到期自动跳转登录，使用隔离 B 账号重新登录成功，没有绕过鉴权。
- Python compileall、tracked diff whitespace、11 个新增文件的 whitespace/EOF 检查通过。0017–0024 相对 HEAD 无修改，0018 SHA-256 与原冻结 whitelist 一致；`.env`/备份 ignored 且未跟踪，当前配置中的非占位 API secret 未出现在 diff 或本轮文档。

真实发现并修复：新页面误用已解包的 `.data.data`；旧 migration 测试最后停在 0024 导致账本表缺失（增加独立 0025 测试，不改旧 migration）；跨 asyncio loop 共用 Redis client 导致首请求 503；Provider semaphore 前缺少用户 admission 使用户请求先排队而不及时触发 429。

首轮全量因缺新表失败；次轮 397 passed / 1 failed（Redis loop）；最终全部通过，失败库和记录保留，不伪称每轮全绿。

## 8. 本轮文件范围

新增：

- `apps/api/alembic/versions/0025_add_model_admission_ledger.py`
- `apps/api/src/interview_api/infrastructure/models/governance.py`、`governance_models.py`
- `apps/api/src/interview_api/modules/public_career/library.py`、`router.py`
- `apps/api/tests/test_08_v13_migration.py`、`test_v13_shared_governance.py`
- `apps/web/src/pages/PublicCareerPage.vue`、`apps/web/tests/public-career.test.mjs`
- 本文和 `docs/44_postgresql_mysql_assessment.md`

增量修改：API `api/deps.py`、`core/config.py / middleware.py / rate_limit.py`、`infrastructure/models/provider.py / router.py / structured.py`、`infrastructure/llm/provider.py`、`main.py`、`modules/models.py`、`modules/auth/router.py`、`modules/resume/processor.py`、`modules/candidate_profile/router.py`、`modules/experience/agents/service.py`、`modules/public_career/research/audit.py`、`modules/qa/router.py`、`modules/interview/router.py`、`modules/interview_report/router.py`、`modules/job_matching/router.py`；Web `router/index.ts`、`i18n/zh-CN.ts`、`tests/product-ux.test.mjs`；`.env.example` 与 ignored `.env` 模型两项。

其他 git status 中的 Profile / Research / UI / Skill V1–V1.2 等仍为进入本轮前的未提交工作，不归入本轮新增功能。没有新增依赖/Agent/网关/向量库/Worker 引擎。

## 9. 最终 10–15 分钟体验

免费额度安全未确认前，优先使用隔离 Replay：

```powershell
# apps/api，已有验收库，不加 --prepare，不清库
.venv/Scripts/python.exe scripts/v12_acceptance_runtime.py --database interview_agent_v12_acceptance_v13_20261019_a
# 根目录另一终端
node scripts/v12_acceptance_web.mjs
```

1. 打开 `http://127.0.0.1:5175/login`，A：`v12.demo@example.com` / `DEMO-v12-local-only`（仅隔离 DEMO）。
2. 公共资料 → 看公司/岗位/要求/来源 → 开始面试 → 自动 Skill 或手选，选系统设计和三轮。
3. 无去重+先查后写 → 原子去重+先查后写 → 原子去重+原子扣减，查看真实模拟结果和评价。
4. 途中刷新，最后打开报告，确认三轮统计及三版工具结果。
5. 退出后 B：`v13.demo.b@example.com` / `DEMO-v13-b-local-only`；看同一公共岗位，个人简历/面试/报告为空，不出现 A 数据。

正常服务仍用 `./scripts/start-interview-platform.ps1`、`status-interview-platform.ps1`、`stop-interview-platform.ps1`，Web `http://127.0.0.1:5173/career`；正式库已到 0025。启动普通 LIVE Worker/API 后用户发起任务可能收费，费用安全确认前本轮没有启动它们。DeepSeek 配置保留，未自行恢复豆包。

## 10. 限制与停止点

开发与可免费执行验收完成；真实 DeepSeek/V1.2 LIVE 工具选择仍受费用安全阻塞，不宣称完整 LIVE 验收。

当前应用额度是默认全用户同档，未新增充值、管理员调账、服务商 billing reconciliation 或账户计费管理 UI。公共资料申请不自动采集/发布。dev Redis fail-open、单机个人项目规模、来源真实性仍需审核等限制如实保留。独立网关仅在将来多服务、统一边界 TLS/WAF/跨语言路由有真实需求时再评估；当前 FastAPI 足够，不新增 Kong/APISIX。

到此停止 V1.3，不继续扩展其他功能。

交付清理：已关闭本轮隔离 Playwright 会话及经 PID/完整命令行确认的验收 API/Web；保留测试库、截图、原始失败记录、正式库和备份。PostgreSQL/Redis 未停止，未启动普通 LIVE Worker 消费任务。所有已有未提交工作仍保留，无 commit/push。

## 后续授权与 LIVE 更新（2026-10-19）

上述费用阻塞是原交付记录，保留不改写。用户随后授权有界真实调用，已完成 DeepSeek 14 次实际 HTTP、76,322 Token 的批次；14 行用户额度全部 SETTLED，与 provider usage 一致，无 fallback。两场真实模型面试完成，A Coach 报告成功，B Coach 因逻辑上限未执行。完整数值、边界与未验证项见 `docs/45_deepseek_v12_v13_live_acceptance.md`。
