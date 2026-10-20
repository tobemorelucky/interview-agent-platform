# 维护者人工公共职业资料导入

## 边界

MANUAL_SOURCE_IMPORT + LIVE MODEL：真实页面 URL，维护者保存的真实 UTF-8 TXT/MD 正文。
绕过 Search/Fetch discovery，不绕过既有 Research、Structured Output、Evidence resolver、
Claim/Evidence、quality gate 或人工审核。不新增 API/UI/Agent/Schema/migration；不修改
Search/Fetch Provider、模型配置、Profile、Matching、Interview、Coach 或 Memory。

本次仅实现/测试/整理材料。没有调用真实模型，没有执行正式库导入。

## CLI（本阶段仅说明，不执行）

在仓库根目录，设置本进程的模块搜索路径后运行：

```powershell
$env:PYTHONPATH = 'apps/api/src'
& apps/api/.venv/Scripts/python.exe apps/api/scripts/import_public_career_document.py `
  --source-url '<已核对的真实公开URL>' `
  --source-type official `
  --document-type job_jd `
  --file 'data/manual_sources/2026-10-05/<已核对的正文>.txt' `
  --company '<正文中的公司名称>' `
  --job-title '<正文中的岗位名称>'
```

使用 uv 的等价方式：在 apps/api 中 `uv run python scripts/import_public_career_document.py`
并传相同参数（file 路径相对该工作目录）。面经使用 document-type=interview_experience，
source-type=community。没有 Replay CLI 开关；只接受配置的 CompatibleModelProvider 路由。
数据库凭证/API key 仍从现有环境配置读取，不作为命令参数或输出。

正文须为非空 UTF-8 TXT/MD，最大 2MiB，无 NUL；超过既有 Evidence 字符预算时拒绝，
不静默把很长文件当作全面抽取。这里只接受已清洗正文，不做 HTML/OCR/ASR/parser 扩展。
URL 复用现有 canonicalize_public_url：HTTP(S)、去 tracking、拒绝 credentials/secret query/
literal private IP/localhost。本地导入不 DNS 请求、不联网验证网页真实性。

## 来源/审计/事务

1. 短事务准备 Source、SourceSnapshot、PublicDataRefreshTask 到 READY_FOR_EXTRACTION。
2. Source 按 canonical URL SHA-256 复用；不修改已有 Source 的来源身份以制造官方可信。
3. Snapshot 按 `(source_id, raw content SHA-256)` 复用，与原 Fetch persistence 相同；
   normalized content fingerprint 另存 metadata，用于已有独立内容 grouping。
4. Snapshot.metadata_json.manual_source_imports[task_id] 保存完整 manifest；保留此前 Fetch
   或其他人工任务的元数据，不把原 acquisition history 改写成这一次导入。
5. Task.target_ref=MANUAL_SOURCE_IMPORT:<snapshot_id>；reason 为带 schema_version 的 JSON
   manifest，记录真实 URL、来源类型、正文 hash、公司、岗位、文件名、取得时间。
6. 人工 resolver 精确校验 task manifest 与 Snapshot manifest、URL、hash、真实正文一致；
   与旧成功 public.fetch 分支并列，不创建任何假的 ToolInvocation。
7. Snapshot.fetched_at 是现有非空兼容字段。本次新 Snapshot 中保存本地正文文件 mtime，
   metadata 明确 fetched_at_semantics=LOCAL_ACQUIRED_AT_NOT_NETWORK_FETCH；published_at=NULL。
   文件 mtime 不是网站发布时间。旧 Snapshot 原时间不覆盖，任务 manifest 独立记录这次保存时间。
8. 现有 PublicDataResearchAgent 执行真实模型并保存现有 AgentRun/StepRun 审计；只有该 Agent
   通过既有校验和 quality gate 后创建最终实体。本 importer 无最终实体 SQL。

## 幂等与失败

key = canonical URL + 原正文 hash + document type 的确定性 hash。同 URL/同正文/同类型
复用同一 Task、Source、Snapshot。公司/岗位/声明来源类型改变则明确 IDENTITY_CONFLICT，
不在同一个正文/task 上偷偷从 community 升级 official。
相同 URL 新正文形成新 Snapshot/Task；不同 URL 同正文可分别保留 Snapshot。
同一 URL 的准备事务以 Source 行锁串行化，避免并发生成重复 task/provenance。

COMPLETED 重复导入不再次调用模型；FAILED/busy 不自动重试或清除旧失败。失败任务返回
非零退出码，由维护者检查已有 task/run 审计决定后续动作，当前 CLI 不提供重跑刷成功开关。
READY 并发执行由现有 Agent 的 Task 状态/行锁检查约束，不持锁调用模型的行为不在本轮扩展。

## 发布/审核

- official 只是声明，不修改 trusted domains，也不自动创建 Company.official_domain。
  仍须通过既有可信域名、Evidence 和 quality gate；未知域名 WAITING_REVIEW。
- community JD 不因 URL 恰好命中 trusted domain 而自动发布。
- 如果相同 Job/正文早已有已批准 JD，复用历史版本而不降级/覆写；输出同时给实际
  publication_status 和本次 manual_publication_decision，不能把历史批准说成本次社区自动发布。
- 模型抽取的公司/岗位必须与维护者输入规范化精确一致，否则在最终实体写入前失败。
  输入只是校验预期身份，不拼入正文，不替代 Evidence，也不强迫模型按提示造事实。
- 面经复用 Extraction→Validation→Routing→Reliability→QualityGate，仅产生待审/需检查
  或被拒草稿，不自动发布、索引。声明 official 的面经也不获得自动发布权限。
- 兼容 ExperienceSourceItem 标记 MANUALLY_IMPORTED，fetched_at=NULL，不冒充网络 FETCHED。
  既有审核接口仍适用；旧仅允许 FETCHED 的单 source re-extract 入口不是人工导入重跑入口。
- JD 人工审核 UI/API 尚不是本轮新增能力。待审 JD 保持既有后台领域状态。

## 本地真实材料

`data/manual_sources/2026-10-05/manifest.json` 只保存 URL/身份/路径/状态/统计，不含大段正文。
正文 TXT 和其他 raw source 文件由 `.gitignore` 精确目录规则排除；仅 manifest.json、README.md
可跟踪。READY 表示本轮真正取得并核对公开主体正文，不表示已入库或经过模型。
CLI 输出 configured_model_mode=LIVE 表示要求使用真实 provider；实际调用以 StepRun 为准，
COMPLETED 复用并未再次调用模型，不将“配置为 LIVE”冒充新的实际模型成功。
SOURCE_UNAVAILABLE 不生成占位正文；SOURCE_MISMATCH 不强行纳入指定测试场景。
遇到登录/验证码/付费墙/访问限制停止，不采用搜索摘要或推测补写。

## 测试与材料结果

全量隔离库 interview_agent_f4_regression_manual_import_20261005_1：
**287 passed / 119.95 秒，无 skip/fail**。原 267 项保留，新增 20 项。
新增覆盖 URL/文件校验、CLI 参数及 LIVE-only guard、并发准备幂等、无 Fetch 审计/无最终实体
裸写、声明身份冲突回滚、official 有/无可信域名、community 即使命中可信域名仍待审、
完整 Claim/Evidence/JD/Requirement 持久化、completed 去重不调模型、错误身份/未知 Evidence/
篡改正文失败、FAILED 不自动重跑、旧面经 pipeline/人工 review、变更正文及跨 URL 相同正文。
测试只使用明确 DEMO/REPLAY、独立 PostgreSQL 数据库，阻断 HTTP 网络；没有调用真实模型。

9 个真实候选 URL 都返回 HTTP200，7 项 READY，2 项 SOURCE_MISMATCH，0 项 SOURCE_UNAVAILABLE。
JD01 实际是安全隐私工程岗位列表，未找到指定岗位；EXP04 实际是AI视频行为面试分析，
不能作为AI技术岗位面经。两项不创建占位正文，等待维护者决定。
READY 正文字符数/hash、实际标题、取材范围、身份确认事项见目录 README/manifest。
高校转载仍为 community；旧年份面经不伪装成2027新材料。材料整理未调用模型，
未创建任何正式 Source/Snapshot/RefreshTask 或最终实体，不等于完成真实模型导入验收。

CLI `--help`、Python compile、git diff --check 通过。无新增依赖/migration/API/UI。
Frontend 11/11、production build/vue-tsc 通过。7 份正文的字符数/SHA-256 与 manifest
逐文件重算一致，全部 ignored、未 tracked；两个 mismatch 文件不存在。
入场已有 Profile/context/docs 等未提交修改全部保留；本轮不对这些文件继续编辑。
对 Profile/Matching/Interview/Report/Memory、Search/Fetch tools、模型基础设施/config 和
全部 migration 的 94 个文件校验 SHA-256，与本轮前完全一致。未 commit、未 push。

实际回归命令：

```powershell
& apps/api/.venv/Scripts/python.exe scripts/run-regression.py --database interview_agent_f4_regression_manual_import_20261005_1
& apps/api/.venv/Scripts/python.exe -m compileall -q apps/api/src apps/api/tests apps/api/scripts
& apps/api/.venv/Scripts/python.exe apps/api/scripts/import_public_career_document.py --help
git diff --check
# apps/web
node --test tests/product-ux.test.mjs tests/job-matching.test.mjs tests/interview-sse.test.mjs tests/interview-report.test.mjs
pnpm build
```

最终 CLI 输出标签调整后，独立测试库再跑 importer 20 项：20 passed / 3.52 秒。
新增未跟踪 code/docs/manifest/README 也通过路径级 whitespace check。

本轮文件：import_public_career_document.py、public_career/manual_import.py、
public_career/research/agent.py 的人工来源分支、test_manual_public_career_import.py、
.gitignore、本文档、数据目录的 README/manifest，以及仅留本地且 ignored 的 7 份 TXT。
