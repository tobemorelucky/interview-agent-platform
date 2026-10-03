# M3：Resume → Candidate Profile

## 范围

Candidate Profile 是私有、可追溯、版本化的稳定事实入口。M3 不改 Interview
Workflow、Memory、Job Matching 或公共 Research；JobPilot 保持只读。
SearXNG 第三方 CAPTCHA / rate limit 是已知限制，不作为 M3 前置条件。
不宣称真实 Live Search 已稳定通过。

保留 Resume 上传、MinIO、Parser、Celery、raw_text、ResumeReport.summary_json。
每次上传仍为材料版本，没有 ResumeVersion。仅去掉 processor 中已不使用、但构造时
仍会连接网络的 embedding/Milvus 客户端，不改变检索和模拟面试。

## Schema / ER

```text
User --unique--> CandidateProfile --current_version_id(nullable)--> CandidateProfileVersion
Resume --RESTRICT--> ResumeEvidence                                      |
Resume --RESTRICT--> CandidateProfileVersion                             +--> CandidateFact
CandidateProfile --> UserConfirmedEvidence                                      |
ResumeEvidence / UserConfirmedEvidence <-- CandidateFactEvidence <--------------+
CandidateFact --> ProjectExperience / WorkExperience / EducationExperience / UserSkill
CandidateProfileVersion --> ProfileCorrection (base/new/field/old/new/user/reason/time)
Resume --> ProfileGeneration --> existing ExperienceAgentRun / ExperienceAgentStepRun
```

Version 包含版本号、base、源 Resume、完整 snapshot、summary、unknowns、producer、
provider/model、prompt version、generation key、agent audit 与时间。JSON 用于完整版本
复制和 Agent 上下文，项目/工作（含实习）/教育/技能另有正式规范化表。
摘要和 strengths 也有 CandidateFact provenance，不是无证据 JSON。
CandidateFactEvidence 用真实 FK 关联两类 Evidence，CHECK 要求恰好一种非空。

DB triggers 禁止 Version、规范化记录、事实/关系、Evidence、correction UPDATE/DELETE；
已发布版本也禁止继续 INSERT 事实/规范化记录/Evidence 关系。
current_version DB trigger 校验属于同一 Profile。user_id UNIQUE 保护单一聚合根。

## ResumeEvidence / Agent

真实 raw_text 按非空行、固定 1200 字符切片生成 Evidence，确定性标题规则识别 section。
不使用 LLM 切分。key = SHA-256(resume_id, section, normalized content hash, start/end)。
重试 ON CONFLICT DO NOTHING，复用 key 与 DB id。excerpt 是真实切片，offset 是 Python
字符偏移。Parser 没有可靠页码时 page=NULL；批量 Evidence 分批写入避免 bind 参数上限。

ResumeProfileAgent 输入 ResumeReport、Evidence keys、当前 snapshot；严格输出
CandidateProfileDelta：candidate_summary、skills、projects、education、work_experience、
internship_experience、strengths、unknowns。模型只能引用已有 key，不能输出 excerpt/location。
Resolver 验证当前材料的 key，持久化再次校验用户归属；不做模糊引用匹配。
本版保守要求 label/statement 是引用 excerpt 的精确连续文本（允许确定性规范化）。
有效 Evidence ID 不能支持凭空新增的能力判断。未知主题由程序回填
“当前简历材料中未提供相关证据”，拒绝“不会/不具备”等负面推断。

复用 ModelProvider、ModelRouter、concurrency guard、Structured Output 和一次 constrained
repair。PromptRegistry 扩展 resume_profile@1.0.0，公共 prompt 不变。共享 ResearchModelCalls
仅增加可选 registry/timeout 注入，公共默认行为不变。现有 Run/Step 审计 role、provider/
model、prompt/version、latency、token usage、retry、error；通过私有 Generation/Version
关联审计，不另建审计系统，不保存隐藏推理或原始模型响应。

## Generation 幂等 / 原子切换

generation key = SHA-256(Resume id, raw_text, canonical JSON Report.summary_json,
prompt version/hash, optional explicit regeneration token)。不使用 Celery task id、Report
row id 或时间戳，所以任务重投递、Report row 重建、JSON key 排序不产生等价版本。
Version 与 Generation 的 generation_key 都 UNIQUE。完成 key 直接复用原版本，
不再调用模型，不把用户修正后的 current_version 切回旧版本。
并发 retry 可能各调用一次模型（均有审计），但最多发布一个同代版本。

新 Resume、Report 实际变化、explicit regenerate 产生新自动 key；显式 token 在
Celery redelivery 中不变。correction 使用独立 key。旧 Resume task 重投递发现已完成
Report 时跳过旧 LLM 抽取，直接进入幂等 Profile 生成。

模型调用不持有 DB session/row lock。发布短事务锁 User（保护首次聚合根创建）和
Profile，校验 base，创建 N+1、写齐规范化记录和 Evidence、flush，最后切换 current_version。
成功 generation/run 状态同事务提交；中间失败整体回滚。LLM 调用期间发生 correction
时，旧 base 发布返回 409，不能覆盖用户最新修正。
Generation 状态 RUNNING/COMPLETED/FAILED，画像失败不影响成功 ResumeReport。
单独的 Profile Celery task 失败抛错，但不修改 Resume 状态。

## Correction / 技能边界

深拷贝当前完整 snapshot，只编辑白名单中的已有 label/statement；未修改经历、摘要、
strengths、unknowns 保留，重建整套 normalized records 和关系。记录 base/new version、
path、old/new、changed_by=user、user id、reason、timestamp；陈旧 base 409。
UserConfirmedEvidence 只保存本次确认的新值，原 ResumeEvidence 继续支持未修改字段，
不把全部旧事实伪装成用户确认。修正技能保守转为 MENTIONED，不是本人认证能力等级。
后续生成保留用户确认事实，按明确旧/新名称排除模型重新引入的旧标签；无模糊匹配。

UserSkill：MENTIONED=材料提及，DEMONSTRATED=项目/工作/实习出现，STRONG_EVIDENCE=
至少两处具体使用证据；这是证据状态，不是熟练度评分。旧 memory.UserSkillProfile
和 Interview writer 保留，继续表示动态面试表现估计，不作为新稳定事实的真相源。

## Resume 删除保护

Service 先锁本人 Resume，检查 ResumeEvidence、Version、Generation 引用。有引用返回
409 RESUME_REFERENCED_BY_PROFILE，不调用 MinIO 删除。Evidence 创建/发布也锁相同 Resume。
DB FK 使用 RESTRICT，绕过 Service 的 SQL DELETE 仍失败。无引用简历旧删除流程不变。
没有级联删除历史 provenance，也没有新增历史清除 UI。

## API / UI

全部接口经 get_current_user 验证，只读写本人数据。

| 接口 | 行为 |
|---|---|
| GET /api/v1/candidate-profile | current、normalized、真实 Evidence、处理状态 |
| GET /api/v1/candidate-profile/versions | 本人版本列表 |
| GET /api/v1/candidate-profile/versions/{id} | 历史完整只读；跨用户 404 |
| POST /api/v1/candidate-profile/generate | 已完成 Resume 入队；默认幂等重试 |
| POST /api/v1/candidate-profile/corrections | 完整 N+1；陈旧 base 409 |

`/profile` 我的画像展示摘要/技能/项目/教育/工作/实习/strengths、Evidence 原文/offset、
unknowns、版本、生成错误。支持明确事实修正、历史只读，标记 Replay/fixture；无 Matching、
推荐、润色或简历改写 UI。

## Migration / 配置

只新增 0021，不修改 0017–0020。升级后启动 API/Worker。历史 Resume 没有强制回填，
用户可通过 generate 按需建立画像。downgrade 会删除 M3 表和数据，生产回退先备份；
本次 downgrade 测试只在空白隔离库执行。
新增 RESUME_PROFILE_MODEL/BASE_URL/API_KEY/TIMEOUT_SECONDS/MAX_OUTPUT_TOKENS/
PROVIDER_CONCURRENCY，空 endpoint/key/model 复用已有 LLM 配置。无新依赖或锁文件修改。

## 实际验收（2026-10-02）

- 最终空白隔离 DB interview_agent_m3_verified_20261002：完整 pytest **74 passed / 37.39s**，
  包含 M1/M2A/M2B、0021 upgrade/downgrade 和 18 项 M3 测试（含 migration）。
- M3 覆盖 Evidence 稳定/未知 ID/无 Evidence/unsupported strength、repair/hard fail、
  normalized relations、完整 correction、provenance、DB 历史不可变/sealed snapshot、
  并发 retry/Report 变化/新 Resume、原子切换故障回滚、权限隔离与删除保护。
- `pnpm build`（vue-tsc + Vite）通过。Playwright 实际登录、读取、修正及历史只读验证。
- 原有 smoke_experience_agent_graph.py、smoke_experience_review.py、
  smoke_memory_interview_context.py 在 M3 隔离测试库再次执行通过。
- 真实本地 TXT：apps/api/tests/fixtures/m3_resume_demo.txt，内容是明确虚构 demo，
  不是用户真实个人简历。真实 API/auth/upload/MinIO/Celery/Parser/DB；两个模型阶段
  都是明确注入的 Replay fixture，**不是 Real Model / Live 网络验收**。
- 最终 smoke DB interview_agent_m3_smoke_20261002：Resume 1，task
  4e424b37-4330-458d-a41b-4006841777df，11 条 ResumeEvidence（Version 引用其中 5 条）。
  Version 1：项目 1、工作/实习 2、教育 1、技能 1；重试复用 Version 1。
- DELETE 返回 409，真实 MinIO 原始字节仍可完整读取。公共 RefreshTask=0。
- UI 将 Redis 修正为 Redis 缓存，生成 Version 2（base=1、USER_CORRECTION）；
  旧版本 Redis/DEMONSTRATED 不变，新版本 MENTIONED，所有未改经历继承。
  原项目 Evidence offset 76–104 保留，用户 Evidence 仅确认“Redis 缓存”。
  两个版本合计 15 条 fact-evidence 关系，1 条 correction。
- QA 截图保存在 output/playwright/（gitignored）。
- 浏览器画像页面 console 检查为 0 errors / 0 warnings。

## 复现

基础设施 PostgreSQL/Redis/MinIO 启动，新建专用隔离数据库；不要对有数据的库重跑
migration downgrade tests。API 目录的不同终端设置相同 DATABASE_URL 后运行：

```powershell
$env:DATABASE_URL='postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_m3_smoke_20261002'
uv run --frozen alembic upgrade head
uv run --frozen python scripts/m3_demo_runtime.py api
# 第二个终端
uv run --frozen python scripts/m3_demo_runtime.py worker
# 第三个终端
uv run --frozen python scripts/smoke_candidate_profile.py
# Web 目录
pnpm dev --host 127.0.0.1
```

Demo Worker 只监听 m3_profile_replay_fixture_demo queue；固定响应只接受本 fixture。
正常运行不要用 demo runtime，照旧启动 API/Worker 使用当前真实模型配置。

## 修改归属与文件

进入任务前已存在 M1/M2/面经审核等未提交修改，包括 0017–0020、public_career、
infrastructure/models、测试、管理页面和两个 uv.lock；全部保留。无 reset/checkout/clean。

M3 新增 candidate_profile/{models,schemas,evidence,prompts,service,agent,router,__init__}.py、
0021、test_04_m3_migration.py、test_m3_candidate_profile.py、m3_resume_demo.txt、
m3_demo_runtime.py、smoke_candidate_profile.py、web/api/profile.ts、CandidateProfilePage.vue、
本文档。增量编辑 model registry/main/config/shared router/shared audit 的可选注入、
Resume processor/service、Celery client/worker、UserLayout/Vue router、.env.example、
.gitignore、README、数据库/API 文档。未修改 Search、Interview、Memory、旧 migrations、
pyproject、uv.lock 或 JobPilot。

## 遗留限制 / 停止点

真实模型未验收；Replay 不能证明真实模型可用性。保守精确文本校验可能拒绝合理改写。
Parser 无可靠页码/扫描 OCR；M3 不重写 Parser。最小 correction 不支持新增/删除整条经历
或任意 JSON 编辑。并发 retry 保证单版本，不保证单模型调用。Interview Workflow 未改为
读取 Profile，统一接口供后续阶段接入。M2 Live Search 限制保留，不继续扩 Research。
完成 M3 后停止，不进入 M4。
