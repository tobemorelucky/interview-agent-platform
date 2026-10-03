# F0/F1：代码冻结与正式运行验收基线

记录日期：2026-10-03。主仓库：`E:\work\code\python\interview-agent-platform`。

## 冻结范围

M1 公共职业数据、M2 工具采集与公共 Research、M3 Candidate Profile、M4 面试核心、M5 Report/受控 Memory、M6 Job Matching 均已验收并冻结。此前面经审核/UI 修改也保留。F0/F1 不新增 Agent、业务能力、Schema 或 UI；不进入 F2，不调用 Live Search/真实模型。

本轮之前已有 46 个 tracked 修改、138 个 untracked 文件。tracked diff：46 files changed, 2929 insertions(+), 824 deletions(-)。未执行 commit/reset/clean/checkout；旧 migration 不改动。冻结文件 SHA-256 清单在 Git 忽略的 `.runtime/finalization/f1-freeze-manifest.json`，共 390 文件。

## Migration 链

| Revision | 内容 | 前驱 |
|---|---|---|
| 0017 | 面经 review notes | 0016 |
| 0018 | Public Career Data | 0017 |
| 0019 | Public Data Research Infrastructure | 0018 |
| 0020 | PublicDataResearchAgent | 0019 |
| 0021 | Candidate Profile | 0020 |
| 0022 | Mock Interview Core | 0021 |
| 0023 | Interview Reports / Memory Proposals | 0022 |
| 0024 | Job Matching | 0023 |

单 head 为 0024。正式数据库为 localhost:5432/interview_agent，升级前 revision 0017；0017 在预期链上。本轮已按现有链升级至 0024，不 stamp，不猜测历史关联。

## 配置与环境

实际使用仓库根目录 `.env`，被 Git 忽略；进入任务时 1567 bytes，最后修改 2026-05-30T16:01:31.6553955+08:00，SHA-256 `C532A1280E0E649CA2C20DBAF330B22194DC4A9884CB363710BDEB31042FF385`。只记录文件元数据和配置键名，不保存/输出密码、API Key、JWT 或 token。本轮唯一授权配置修改为管理员密码同步，最终 1600 bytes，SHA-256 `AA5D2EC77A25D7C945F9A399AE6108A03E974A57CDE5587C6B9014D7BD4EF1A8`；排除 ADMIN_PASSWORD 后每一行的 SHA-256 完全一致。

`.env.example`、API/Worker lockfiles、API pyproject 在进入本轮前已修改，继续保留。docker-compose.yml、start-dev.bat、.runtime/dev 启动包装均已审查。包装脚本会批量终止端口进程/启动全部组件，本轮只采用其中的底层服务命令，避免终止无关进程或启动无关依赖。

现有端点：PostgreSQL 5432；Redis 6379（cache DB0、broker DB1、results DB2）；MinIO 9000；Milvus 19530；SearXNG 8080。无 DATABASE_URL/ENV_FILE/M1_TEST_DATABASE_URL 进程覆盖。模型配置存在，但本轮不调用。

## 升级前正式库

所有旧业务表保留列投影的排序 SHA-256 和 row counts 已只读记录到 `.runtime/finalization/db-before.json`；不输出原始用户数据。约束/索引无 invalid 状态。

| 表 | 行数 |
|---|---:|
| audit_logs | 0 |
| chat_messages | 6 |
| chat_sessions | 4 |
| experience_agent_runs | 0 |
| experience_agent_step_runs | 0 |
| experience_collection_tasks | 6 |
| experience_keyword_presets | 12 |
| experience_recent_summaries | 0 |
| experience_source_items | 92 |
| interview_experiences | 0 |
| interview_messages | 49 |
| interview_questions | 0 |
| interview_session_questions | 8 |
| interview_sessions | 4 |
| kb_chunks | 2 |
| kb_documents | 2 |
| resume_reports | 27 |
| resumes | 37 |
| user_memory_events | 0 |
| user_memory_items | 0 |
| user_skill_profiles | 0 |
| users | 3 |

Candidate Profile、公共 Company/Job/JD、InterviewEvaluation、FinalReport、JobMatch 表在 0017 尚不存在，不能把“表不存在”描述为已有历史数据为空。

## 测试基线与已知限制

M6 上轮完整验收：backend 178 passed（1 warning），frontend 7/7，production build 成功。F1 正式升级后重新运行，不以历史结果代替本轮结果；所有包含 downgrade 的集成测试使用新建隔离测试库，正式业务库只 upgrade。

真实 Live Search 仍有第三方 CAPTCHA/rate limit，冻结，不作为验收前置；Replay/Fixture/Demo 不得冒充 Live。M6 实模型验收留到 F2；学历/专业等缺少可靠结构字段仍 UNKNOWN；Evidence alignment 为有界确定性匹配；不扩 Profile/Research。真实库某类数据不存在时如实记录，不伪造历史数据。

## F1 执行结果

本轮唯一新增的可入 Git 文档为本基线；运行审计/备份在 Git 忽略的 .runtime 目录。390 个冻结文件 SHA-256 对比均未改变，tracked diff stat 与进入本轮时一致；无 commit，无依赖/业务/UI/history migration 修改。

### 原生备份与实际 upgrade

备份：`.runtime/backups/f0f1-20261003/interview_agent_before_0024.dump`（绝对根目录同本仓库），288135 bytes，SHA-256 `A1FE45107C710FA63B7F2A23E2CAD0B32B309CC0DCB4B859A4C59B9429FB5D98`。

使用运行中的 PostgreSQL 16 容器执行 `pg_dump -U interview_agent -d interview_agent --format=custom`；`pg_restore --file=/dev/null` 完整解码成功，容器与本地副本 SHA-256 相同。没有在正式库做 restore/downgrade；没有删除 database/volume。备份经 `git check-ignore` 确认不会加入 Git，含业务数据须作为敏感本地备份保管。

正式执行 `uv run alembic upgrade head` exit 0；`alembic current` 与 `alembic heads` 均为 `0024 (head)`。升级紧接着只读比较：22 张旧业务表行数与原列内容 SHA-256 全部一致。0017 原有 23 张表（包含 alembic_version）升级到 62 张表，新 39 张业务表均可查询、行数为 0；232 个 PK/FK/unique 约束可审计，invalid constraints/indexes 均为空。31 个非内部触发器存在；`uq_final_report_current` partial unique index 存在；4 场历史面试均为 explicit legacy。记录：`.runtime/finalization/db-before.json` / `db-after-upgrade.json`。

### 当前 Runtime

| 组件 | 实际运行 | 验证 |
|---|---|---|
| PostgreSQL 16 | 本项目已有容器/pgdata，5432 | healthy，真实 revision 0024 |
| Redis 7 | 本项目已有容器/redisdata，6379 | healthy，启动 Worker 前默认队列长度 0 |
| MinIO | 本项目 miniodata，9000/9001 | healthy，37/37 旧简历对象 stat 成功 |
| etcd | 本项目 etcddata，2379 | healthy |
| Milvus 2.5.10 | 本项目 milvusdata，19530/9091 | healthy，已有 `kb_chunks_v1` 2 条、`kb_chunks_smoke_test` 6 条可读 |
| SearXNG | 已运行 interview-searxng，8080 | 首页 HTTP 200，未执行 Search |
| FastAPI | `uv run uvicorn interview_api.main:app --reload --host 0.0.0.0 --port 8000` | startup complete，health 200 |
| Celery | `uv run --frozen --no-sync celery -A interview_worker.celery_app worker -l info --pool=solo` | ready，真实队列 ping → pong |
| Vue/Vite | `pnpm dev --host localhost`，5173 | 用户页面可读 |

本项目已有 2 个 kb_documents / 2 个 kb_chunks，故启动当前知识检索所需 Milvus/etcd，并核对旧 collection；没有生成 embedding、重建索引或调用 RAG 模型。另一个 RAG 项目的旧 `milvus-standalone` 容器（Compose project=rag）完全未改动。未启动 Attu、其它项目数据库等无关组件。

普通 Worker `uv run` 首次触发依赖解析/网络 retry，已停止；frozen/no-sync 使用已有环境正常启动，两个 lockfile 哈希未变。API/Worker/Web 保持运行，不调用宽泛 start-dev 的端口批量 kill。

### 正式库低风险业务 smoke

现有 `.env` 管理员最初真实登录为 401；数据库只读核对唯一 active 管理员 id=1，其 email/username 与配置一致，但配置密码不匹配数据库哈希。原密码无法从哈希恢复。用户随后明确授权“重置管理员密码并同步 .env”：仅更新 id=1 的 password_hash/updated_at，并同步 `.env` 的 ADMIN_PASSWORD；随机新密码留在本地配置，不打印或写入文档，未改角色/账号/其它用户。短事务校验 ADMIN、active、账号，commit 后再次验证哈希与配置相符；实际登录 200、角色 ADMIN。API 与浏览器均走正常登录，不伪造 token/绕过权限。

按允许的低风险 Demo 范围，仅经真实注册 API 新增普通账号 `DEMO_F1_RUNTIME_20261003`，正常登录 200；users 因此从 3 到 4，未生成 Resume/Profile/Interview/Job/Report/Memory/Match 假历史。`.test` 邮箱被既有 EmailStr 拒绝 422，改用合法 example.com DEMO 地址；未放宽校验。

用户侧读取：resumes、CandidateProfile/versions、public interview targets（Company/Job/JD）、interview sessions、reports、memory items/skills/events/proposals、job matches、QA sessions 均 HTTP 200；新 DEMO 用户无历史，Profile current=NULL，其余列表为空。新公共职业/Profile/Report/Match 表本身也为空，不声称读取了真实新阶段业务记录。普通用户访问 4 个 admin API 正确为 403。

真实 Playwright 浏览器通过现有登录 UI 登录 DEMO 用户；遍历 `/profile`、`/jobs/matches`、`/interview`、`/interview/reports`、`/memory`、`/resumes`、`/qa`、`/`，无 pageerror；4 个 admin 路由按权限重定向 dashboard；3 个不存在 ID 的详情页正确得到 404。`/login` 初始页可用，register API 已验证。截图：`output/playwright/f1-demo-dashboard.png`、`f1-profile-empty.png`。

管理员凭据修复后重新验收：真实 API 返回 resumes 当前页 20 条（该账号 total 36，另一原用户 1，合计原库 37），已有 ResumeReport 详情 200；4 场历史 InterviewSession 及详情 200；2 个 KB document、6 个 experience collection task、空 review queue/audit log API 均 200。Profile、public targets、FinalReports、Memory、JobMatch 仍为空且读取 200，没有借机生成历史。

管理员真实浏览器登录成功后，所有既有用户页与 `/admin/kb/documents`、`/admin/experiences`、`/admin/experience-review`、`/admin/audit` 均正确停留对应路由，page_errors=[]、server_errors=[]。已有 Resume 37 的报告详情可渲染，legacy 会话可只读打开。截图另见 `f1-existing-resume-report.png`、`f1-existing-legacy-interview.png`、`f1-admin-review.png`。未触发生成、Search、模型、删除、采集或审批写操作。

MinIO 全量只读 stat：37 份旧简历对象全部存在、缺失 0。ToolInvocation / ExperienceAgentRun 仍 0，本轮不调用真实 Search/LLM，也不运行 Replay 业务生成来填充正式空表。

### 本轮回归与静态检查

- backend M1–M6：**178 passed**，0 failed/skip，156.43 秒；1 个既有 `.pytest_cache` 权限 warning，断言均通过，覆盖数量未减少。
- frontend：`node --test tests/*.test.mjs` **7/7 passed**。
- `pnpm build`：vue-tsc + Vite production build 成功（150 modules）。
- `compileall`：API/Worker src、scripts、运行审计脚本成功；`git diff --check` 成功（仅 Git 的 LF/CRLF 提示）。
- 隔离新建测试库：`interview_agent_f1_regression_20261003`；每个测试进程同时固定 DATABASE_URL / M1_TEST_DATABASE_URL 指向此库。migration downgrade/upgrade 测试不触碰正式库；正式库始终 0024。

结果保存在 `.runtime/finalization/test-results.json`。最终复核 390 个冻结文件均未变化；唯一新增 Git 可见文件为本基线，原 tracked 修改仍 46 个，备份哈希仍一致。未登录 Register 页面也已真实浏览器读取。没有添加依赖/测试框架/产品功能，也没有进入 F2。

### 当前问题

管理员凭据不匹配问题已按用户明确授权解决。最终正式库仍 0024、constraints/indexes 全部有效；相对升级前只有 users 表存在预期差异（新增 DEMO 普通账号与 id=1 密码重置），另外 21 张旧业务表行数和原列 SHA-256 仍完全一致。最终记录：`.runtime/finalization/db-final.json`、`runtime-smoke-admin.json`。ORM 61 张业务表/全部列与正式 schema 对比无缺失。

既有 favicon 404、login autocomplete 提示、PyMilvus ORM deprecation 提示、pytest cache 权限 warning 如实记录，不以修改冻结 UI/依赖来绕过。`.test` 注册地址按既有校验返回 422，并未放宽约束。SearXNG CAPTCHA/rate-limit 已知限制继续保留，不宣称 Live Search 稳定通过。新的 Profile/公共岗位/Report/Memory/Match 没有正式历史数据，所以本轮只验收可读空态；其完整业务路径由 178 回归测试覆盖，真实模型业务验收留 F2。

### 执行命令摘要

```text
git status --short
git diff --stat
git ls-files --others --exclude-standard
uv run alembic current / heads / history
docker exec ... pg_dump -U interview_agent -d interview_agent --format=custom
docker exec ... pg_restore --file=/dev/null ...dump
docker cp ...dump .runtime/backups/f0f1-20261003/...
uv run alembic upgrade head
uv run --frozen --no-sync --directory apps/api python ../../.runtime/finalization/db_audit.py ...
docker compose up -d minio etcd milvus-standalone
uv run uvicorn interview_api.main:app --reload --host 0.0.0.0 --port 8000
uv run --frozen --no-sync celery -A interview_worker.celery_app worker -l info --pool=solo
pnpm dev --host localhost
uv run --frozen --no-sync --directory apps/api pytest tests -q
node --test tests/*.test.mjs
pnpm build
uv run --frozen --no-sync --directory apps/api python -m compileall -q ...
git diff --check
npx --yes --package @playwright/cli playwright-cli --session f1qa ...
```

应用服务与本项目基础组件保留运行，未删除本次隔离测试库/备份。当前阶段止于 F0/F1；不进入 F2、不提交 Git、不启动任何新 Agent 功能开发。

## 进入任务前 tracked 修改（完整清单）

```text
.env.example
.gitignore
README.md
apps/api/pyproject.toml
apps/api/src/interview_api/core/config.py
apps/api/src/interview_api/core/rate_limit.py
apps/api/src/interview_api/core/url_safety.py
apps/api/src/interview_api/infrastructure/tasks/celery_client.py
apps/api/src/interview_api/main.py
apps/api/src/interview_api/modules/experience/agents/repository.py
apps/api/src/interview_api/modules/experience/agents/service.py
apps/api/src/interview_api/modules/experience/enums.py
apps/api/src/interview_api/modules/experience/fetchers/base.py
apps/api/src/interview_api/modules/experience/fetchers/httpx_fetcher.py
apps/api/src/interview_api/modules/experience/models.py
apps/api/src/interview_api/modules/experience/router.py
apps/api/src/interview_api/modules/experience/schemas.py
apps/api/src/interview_api/modules/experience/search/base.py
apps/api/src/interview_api/modules/experience/search/searxng.py
apps/api/src/interview_api/modules/experience/search/url_utils.py
apps/api/src/interview_api/modules/experience/service.py
apps/api/src/interview_api/modules/interview/models.py
apps/api/src/interview_api/modules/interview/router.py
apps/api/src/interview_api/modules/interview/schemas.py
apps/api/src/interview_api/modules/interview/service.py
apps/api/src/interview_api/modules/memory/interview_writer.py
apps/api/src/interview_api/modules/memory/policies.py
apps/api/src/interview_api/modules/models.py
apps/api/src/interview_api/modules/resume/processor.py
apps/api/src/interview_api/modules/resume/service.py
apps/api/uv.lock
apps/web/src/api/admin.ts
apps/web/src/api/interview.ts
apps/web/src/layouts/UserLayout.vue
apps/web/src/pages/DashboardPage.vue
apps/web/src/pages/InterviewChatPage.vue
apps/web/src/pages/MemoryPage.vue
apps/web/src/router/index.ts
apps/web/src/types/interview.ts
apps/worker/src/interview_worker/tasks/resume_tasks.py
apps/worker/uv.lock
docs/05_database_design.md
docs/07_ingestion_pipeline.md
docs/08_api_contract.md
docs/12_task_state_machine.md
docs/19_agent_platform_evolution_plan.md
```

## 进入任务前 untracked 文件（展开后完整清单）

```text
apps/api/alembic/versions/0017_add_experience_review_notes.py
apps/api/alembic/versions/0018_add_public_career_data_domain.py
apps/api/alembic/versions/0019_add_public_data_research_infrastructure.py
apps/api/alembic/versions/0020_add_public_research_agent.py
apps/api/alembic/versions/0021_add_candidate_profile.py
apps/api/alembic/versions/0022_add_mock_interview_core.py
apps/api/alembic/versions/0023_add_interview_reports_and_memory_proposals.py
apps/api/alembic/versions/0024_add_job_matching.py
apps/api/scripts/backfill_public_career_data.py
apps/api/scripts/m3_demo_runtime.py
apps/api/scripts/m4_demo_runtime.py
apps/api/scripts/m5_demo_runtime.py
apps/api/scripts/m6_demo_runtime.py
apps/api/scripts/run_public_data_research.py
apps/api/scripts/smoke_candidate_profile.py
apps/api/scripts/smoke_experience_review.py
apps/api/scripts/smoke_interview_coach.py
apps/api/scripts/smoke_job_matching.py
apps/api/scripts/smoke_mock_interview.py
apps/api/scripts/smoke_public_data_live.py
apps/api/scripts/smoke_public_research_real_model.py
apps/api/src/interview_api/infrastructure/models/__init__.py
apps/api/src/interview_api/infrastructure/models/provider.py
apps/api/src/interview_api/infrastructure/models/router.py
apps/api/src/interview_api/infrastructure/models/structured.py
apps/api/src/interview_api/modules/candidate_profile/__init__.py
apps/api/src/interview_api/modules/candidate_profile/agent.py
apps/api/src/interview_api/modules/candidate_profile/evidence.py
apps/api/src/interview_api/modules/candidate_profile/models.py
apps/api/src/interview_api/modules/candidate_profile/prompts.py
apps/api/src/interview_api/modules/candidate_profile/router.py
apps/api/src/interview_api/modules/candidate_profile/schemas.py
apps/api/src/interview_api/modules/candidate_profile/service.py
apps/api/src/interview_api/modules/experience/review_repository.py
apps/api/src/interview_api/modules/experience/review_service.py
apps/api/src/interview_api/modules/interview/agents.py
apps/api/src/interview_api/modules/interview/artifacts.py
apps/api/src/interview_api/modules/interview/context.py
apps/api/src/interview_api/modules/interview/controller.py
apps/api/src/interview_api/modules/interview/core_service.py
apps/api/src/interview_api/modules/interview_report/__init__.py
apps/api/src/interview_api/modules/interview_report/agent.py
apps/api/src/interview_api/modules/interview_report/models.py
apps/api/src/interview_api/modules/interview_report/prompts.py
apps/api/src/interview_api/modules/interview_report/resolver.py
apps/api/src/interview_api/modules/interview_report/router.py
apps/api/src/interview_api/modules/interview_report/schemas.py
apps/api/src/interview_api/modules/interview_report/service.py
apps/api/src/interview_api/modules/interview_report/statistics.py
apps/api/src/interview_api/modules/job_matching/__init__.py
apps/api/src/interview_api/modules/job_matching/agent.py
apps/api/src/interview_api/modules/job_matching/models.py
apps/api/src/interview_api/modules/job_matching/policy.py
apps/api/src/interview_api/modules/job_matching/preferences.py
apps/api/src/interview_api/modules/job_matching/prompts.py
apps/api/src/interview_api/modules/job_matching/resolver.py
apps/api/src/interview_api/modules/job_matching/retrieval.py
apps/api/src/interview_api/modules/job_matching/router.py
apps/api/src/interview_api/modules/job_matching/schemas.py
apps/api/src/interview_api/modules/job_matching/service.py
apps/api/src/interview_api/modules/memory/proposal_models.py
apps/api/src/interview_api/modules/memory/proposal_router.py
apps/api/src/interview_api/modules/memory/proposal_schemas.py
apps/api/src/interview_api/modules/memory/proposal_service.py
apps/api/src/interview_api/modules/memory/snapshot.py
apps/api/src/interview_api/modules/public_career/__init__.py
apps/api/src/interview_api/modules/public_career/backfill.py
apps/api/src/interview_api/modules/public_career/compat.py
apps/api/src/interview_api/modules/public_career/dedupe.py
apps/api/src/interview_api/modules/public_career/fingerprints.py
apps/api/src/interview_api/modules/public_career/models.py
apps/api/src/interview_api/modules/public_career/refresh_policy.py
apps/api/src/interview_api/modules/public_career/refresh_service.py
apps/api/src/interview_api/modules/public_career/repository.py
apps/api/src/interview_api/modules/public_career/research/__init__.py
apps/api/src/interview_api/modules/public_career/research/agent.py
apps/api/src/interview_api/modules/public_career/research/audit.py
apps/api/src/interview_api/modules/public_career/research/evidence.py
apps/api/src/interview_api/modules/public_career/research/interview.py
apps/api/src/interview_api/modules/public_career/research/policy.py
apps/api/src/interview_api/modules/public_career/research/prompts.py
apps/api/src/interview_api/modules/public_career/research/schemas.py
apps/api/src/interview_api/modules/public_career/service.py
apps/api/src/interview_api/modules/public_career/tool_models.py
apps/api/src/interview_api/modules/public_career/tools/__init__.py
apps/api/src/interview_api/modules/public_career/tools/contracts.py
apps/api/src/interview_api/modules/public_career/tools/errors.py
apps/api/src/interview_api/modules/public_career/tools/factory.py
apps/api/src/interview_api/modules/public_career/tools/fixtures/fetch.json
apps/api/src/interview_api/modules/public_career/tools/fixtures/search.json
apps/api/src/interview_api/modules/public_career/tools/gateway.py
apps/api/src/interview_api/modules/public_career/tools/providers.py
apps/api/src/interview_api/modules/public_career/tools/replay.py
apps/api/tests/conftest.py
apps/api/tests/fixtures/m3_resume_demo.txt
apps/api/tests/fixtures/m6_jobs_demo.json
apps/api/tests/test_00_migration.py
apps/api/tests/test_01_m2a_migration.py
apps/api/tests/test_02_m2b_migration.py
apps/api/tests/test_03_m2b_migration_data.py
apps/api/tests/test_04_m3_migration.py
apps/api/tests/test_05_m4_migration.py
apps/api/tests/test_06_m5_migration.py
apps/api/tests/test_07_m6_migration.py
apps/api/tests/test_existing_module_regression.py
apps/api/tests/test_experience_fingerprint_writes.py
apps/api/tests/test_m2a_contracts.py
apps/api/tests/test_m2a_dedupe_policy.py
apps/api/tests/test_m2a_gateway_refresh.py
apps/api/tests/test_m2a_safe_fetch.py
apps/api/tests/test_m2b_models_evidence.py
apps/api/tests/test_m2b_research_agent.py
apps/api/tests/test_m3_candidate_profile.py
apps/api/tests/test_m4_interview.py
apps/api/tests/test_m5_memory.py
apps/api/tests/test_m5_report.py
apps/api/tests/test_m5_two_session_e2e.py
apps/api/tests/test_m6_job_matching.py
apps/api/tests/test_public_career_domain.py
apps/web/src/api/interview-report.ts
apps/web/src/api/job-matching.ts
apps/web/src/api/profile.ts
apps/web/src/api/sse.ts
apps/web/src/pages/CandidateProfilePage.vue
apps/web/src/pages/InterviewReportPage.vue
apps/web/src/pages/JobMatchingPage.vue
apps/web/src/pages/admin/AdminExperienceReviewPage.vue
apps/web/src/types/interview-report.ts
apps/web/src/types/job-matching.ts
apps/web/tests/interview-report.test.mjs
apps/web/tests/interview-sse.test.mjs
apps/web/tests/job-matching.test.mjs
docs/20_public_data_research_m2b.md
docs/21_candidate_profile_m3.md
docs/22_mock_interview_core_m4.md
docs/23_interview_coach_m5.md
docs/24_job_matching_m6.md
docs/m2b-real-model-live-smoke.md
```
