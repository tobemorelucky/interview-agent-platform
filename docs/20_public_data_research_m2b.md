# M2B Public Data Research Agent

## 范围与入口

M2A 的搜索/采集仍不需要 LLM。只有管理员显式启动 M2B，才从真实 Fetch
审计所引用的 `SourceSnapshot` 抽取公共数据。唯一对外角色是
`PublicDataResearchAgent`，不实现 JobPilot Supervisor、ResearchRun、Dossier、
Reflection、Matching 或新的模拟面试流程。JobPilot 保持只读。

操作入口（先配置 DATABASE_URL，执行到 READY_FOR_EXTRACTION）：

```powershell
cd apps/api
uv run alembic upgrade head
uv run python scripts/run_public_data_research.py --task-id <id>
```

原管理 API `/admin/experience/sources/{source_id}/extract` 对已绑定公共任务的
source 改为调用该 Agent，并保留原返回结构、审核接口和 UI。未绑定的历史
source 仍使用旧 Extraction/Validation/Routing/Reliability/QualityGate 流程。
显式 `force` 在同一公共任务内重跑面经，保留历史审计，仅替换未批准草稿；
不允许覆盖已批准内容。采集失败仍使用 M2A 显式重采机制。

## 模型配置与结构化输出

配置项（不新增依赖、不改动本阶段前已有的 lockfile 变更）：

- `PUBLIC_RESEARCH_BASE_URL` / `PUBLIC_RESEARCH_API_KEY`：可回退现有 LLM 配置。
- `PUBLIC_RESEARCH_EXTRACT_MODEL`：逻辑 route `public_research_extract`。
- `PUBLIC_RESEARCH_QUALITY_MODEL`：逻辑 route `public_research_quality`。
- `PUBLIC_RESEARCH_MODEL_TIMEOUT_SECONDS` / `PUBLIC_RESEARCH_MAX_OUTPUT_TOKENS`。
- `PUBLIC_RESEARCH_PROVIDER_CONCURRENCY`：同进程、同 event loop 的 endpoint 并发上限。
- `PUBLIC_RESEARCH_MAX_EVIDENCE_CHARS`：输入证据预算，默认 24000，跨 Snapshot 分配。
- `PUBLIC_RESEARCH_OFFICIAL_DOMAINS`：管理员维护的 normalized company name 到域名列表映射。

`ModelProvider`、`ModelRouter`、版本化 `PromptRegistry` 与模型选择和 Agent 解耦。
初版支持配置化的 HTTP compatible provider 和本地 `ReplayModelProvider`。
JSON/Pydantic 验证失败时最多一次 constrained repair：只修复结构/语法，必须
保留原事实字符串与 Evidence IDs；第二次失败即 FAILED，无额外推理链。
Provider 超时/失败和 repair 次数、累计 token usage 均记录审计。

## Evidence 与确定性计数

Catalog 从存储正文生成精确字符区间及 `ev_<hash>` ID。模型仅看到 ID 和正文
区间；输出只能引用已提供的 ID，不能返回 excerpt、URL、hash、snapshot ID
或 source count。Resolver 精确查表回填完整 excerpt/location，不做模糊匹配。
没有 Evidence 或未知 ID 的 Claim 被拒绝；Company/Job 名称必须出现于所引证据。
引用完整性并不证明内容绝对真实，credibility 只表示来源与支持信号。

来源计数由 M2A utility 确定：

- `independent_content_count` = unique normalized content fingerprint count。
- `occurrence_count` = 内容出现/引用数量。
- `multi_source` = independent_content_count >= 2。
- 兼容字段 `source_count` = independent_content_count，绝非模型结果。

不同面经内容分组分别运行原流水线，禁止把同一任务中不相干的面经合为一篇。
转载/镜像保留各自 Snapshot/证据，但不增加独立内容计数。重复 Fetch 的同一个
Snapshot 在任务输入 manifest 中只出现一次，不把重试计作新的出现次数。

## JOB_JD / 发布

Company/Job 唯一键复用 M1；Job 锁序列化 JD 版本号。JD fingerprint 基于所引用
Snapshot 的完整 Fetch 正文，内容分组后按 fingerprint 排序，与模型 excerpt
选择、镜像 URL 或采集顺序无关。相同 Job + 正文复用版本，不覆盖历史 JD。
每个 Requirement 关联 Claim，ClaimEvidence 指向真实 Snapshot 的精确区间。
已有 Company 的 canonical name / alias 唯一精确命中时复用实体；歧义 alias
不自动关联或生成猜测归属，进入失败待处理。

官方判定仅使用管理员配置域名或已有 Company.official_domain，不相信模型输出。
所有引用来源匹配可信官方域名、证据完整、质量无风险时可 APPROVED；否则存为
WAITING_REVIEW，不自动进入用户查询或 Milvus。模型质量建议无法越过确定性 gate。
本阶段不增加 JD 人工审核 UI/API；待审 JD 保留为后台领域数据。

INTERVIEW_DATA 复用原分类、评分与 gate，即使模型建议 APPROVE 也只能进入现有
人工审核状态。Company/Job FK 只对规范化后唯一精确名称/alias 匹配写入，否则
保留字符串和空 FK。标准答案仍区分原文与生成内容，Claim 只支持来源中的问题/面经。

## 审计与迁移

复用 `ExperienceAgentRun` / `ExperienceAgentStepRun`，公共 run 允许 source_item
为空并关联 refresh_task_id；面经 run 仍绑定兼容 source 供原审核页面查看。
新增 agent role、provider/model、prompt/version/hash、status、latency、usage、
structured retry 和 error；不保存隐藏推理或原始 provider 响应。

仅新增 `0020`，不修改 `0017` / `0018` / `0019`。增加 JD publication_status、
面经/问题 provenance FK、审计泛化列。公共实体仍没有 user_id。
Downgrade 可恢复 0019；如果已有无 source_item 的公共审计，迁移会拒绝回退，
必须先由管理员导出/归档这些审计，避免静默删除。

## 验收与已知边界

Live smoke 数据库 `interview_agent_m2b_smoke_20261001` 中 task 5 已到
READY_FOR_EXTRACTION。本地 SearXNG Wikipedia 引擎搜索 `Software engineer`，
真实 Fetch URL：`https://en.wikipedia.org/wiki/Software_engineering`。
调用 6（Search）、7/8（Fetch/重复 Fetch）均 LIVE / SUCCEEDED；Source 1、
Snapshot 1，真实正文 45004 字符，重复调用复用同一 Source/Snapshot。
content hash：`28764a7895e6444a2ddc957cffa6b387e90bed17dd5db8767ac4cadf93f8bf49`。
该 smoke 没有模型调用；Wikipedia 只是采集验收内容，不作为官方 JD 自动发布依据。

当前网络普通搜索引擎存在验证码/429，且系统 Fake-IP DNS 被 SSRF 正确拒绝。
smoke 使用本地代理 `http://127.0.0.1:7890` 与公共 DoH 获取实际公网地址，仍逐跳
执行 IP/URL 校验，未降低生产安全策略。生产环境需可达 SearXNG 上游和可信 DNS。

Replay 工具和模型只有本地 fixture 路径，无 live fallback；全链路测试禁用
httpx 网络 send，并验证相同 Gateway、Source persistence、dedupe 和审计。
M2B 的真实付费模型抽取未作 Live 验收，结构化协议与业务全链路由 Replay 验证。
输入有证据预算上限，超过预算的内容应拆分任务，不宣称完成整个长文的全面抽取。
Provider concurrency 是进程级，不新增分布式队列/限流基础设施。
不进入 M3。

## 本阶段修改文件与已有修改保护

新增：

- `apps/api/src/interview_api/infrastructure/models/{provider,router,structured,__init__}.py`
- `apps/api/src/interview_api/modules/public_career/research/{agent,audit,evidence,interview,policy,prompts,schemas,__init__}.py`
- `apps/api/alembic/versions/0020_add_public_research_agent.py`
- `apps/api/scripts/run_public_data_research.py`、`smoke_public_data_live.py`
- `apps/api/tests/test_02_m2b_migration.py`、`test_03_m2b_migration_data.py`
- `apps/api/tests/test_m2b_models_evidence.py`、`test_m2b_research_agent.py`
- 本文档。

增量修改：

- `core/config.py`：公共模型配置。
- `experience/models.py`：公共审计泛化及 provenance 可空 FK。
- `experience/agents/service.py`：绑定任务的旧提取入口转到公共 Agent。
- `experience/search/searxng.py`：兼容真实 Wikipedia infobox 搜索结果。
- `public_career/models.py`：JD publication_status。
- `public_career/refresh_service.py`：M2B 状态。
- `tests/test_existing_module_regression.py`：简历文本解析、面试 prompt 回归。
- `docs/12_task_state_machine.md`：M2B 状态附录。

入场已有未提交修改（M1/M2A、面经审核/UI、配置、依赖 lockfile 等）全部保留。
没有 reset/checkout/clean；没有重写 0017/0018/0019；没有修改参考仓库、
前端 UI、模拟面试 Workflow、Candidate/Profile 或 Memory 代码。

## 实际测试结果（2026-10-01）

最终全量命令在全新隔离 PostgreSQL 库 `interview_agent_m2b_verified_20261001`
上执行，所有 migration 使用真实 PostgreSQL upgrade/downgrade，不是 SQLite：

```powershell
cd apps/api
$env:M1_TEST_DATABASE_URL='postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_m2b_verified_20261001'
uv run pytest -q -p no:cacheprovider
```

结果：`56 passed in 39.95s`，无 skipped。覆盖 M1、M2A 和 M2B，包括 JD
版本/重复正文、Requirement provenance、Evidence ID 拒绝、单次 repair 与事实
关联保护、失败 usage、并发 guard、官方/社区 gate、面经旧入口/显式重跑、
转载计数、不同面经分组、发布回滚、迁移数据保留及简历/面试基础行为。

另外在独立测试库以 FakeLLM/已有测试机制运行：

```powershell
uv run python scripts/smoke_experience_agent_graph.py
uv run python scripts/smoke_experience_review.py
uv run python scripts/smoke_memory_interview_context.py
```

三项均通过。验证原面经 API 权限/Redis lock/审核状态/重跑、审核编辑和审批
审计，以及模拟面试只读上下文不会写入 Memory。没有调用真实 LLM。
新增模块/scripts/migration 的 `compileall` 和 `git diff --check` 均通过。
环境已有的 SSL_CERT_DIR 提示及 Git CRLF 提示不影响验收；pytest 禁用 cache
provider，避开该 Windows 目录已有缓存权限问题。

Live smoke 复现：

```powershell
$env:EXPERIENCE_SEARCH_LANGUAGE='en'
$env:SEARXNG_ENGINES='wikipedia'
uv run python scripts/smoke_public_data_live.py --database-url 'postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_m2b_smoke_20261001' --query 'Software engineer' --proxy 'http://127.0.0.1:7890'
```

此命令的环境覆盖只针对 smoke，不改变默认 SearXNG Provider 或产品配置。
迁移只在隔离 smoke/test 库执行，未替用户升级其业务数据库。
测试迁移用库必须是隔离的新库；不要对用户业务库运行 downgrade 测试。
已清理本次创建的中间 test/final/acceptance 测试库（仅可重新运行测试重建）；
保留 `interview_agent_m2b_smoke_20261001` 与 `interview_agent_m2b_verified_20261001`
供核查，业务库未改动。
